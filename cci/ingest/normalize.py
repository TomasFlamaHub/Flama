"""Normalización común: números, fechas, día comercial, sucursales y productos."""
from __future__ import annotations

import csv
import re
import unicodedata
from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import select

from .. import config, db


def norm_text(value) -> str:
    s = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def to_number(series: pd.Series, decimal: str = ".", thousands: str | None = None) -> pd.Series:
    """Convierte '$ 12.345,50' / '12345.5' / 12345 a float. Vacíos -> NaN."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype(float)
    s = series.astype(str).str.strip()
    s = s.str.replace(r"[^\d,\.\-]", "", regex=True)
    if thousands:
        s = s.str.replace(thousands, "", regex=False)
    if decimal != ".":
        s = s.str.replace(decimal, ".", regex=False)
    return pd.to_numeric(s.replace({"": None, "-": None}), errors="coerce")


def to_local_datetime(series: pd.Series, fmt: str | None = None, source_tz: str | None = None,
                      dayfirst: bool = True) -> pd.Series:
    """Parsea fechas y las lleva a la hora local del negocio (sin tz)."""
    tz = config.settings()["business"]["timezone"]
    if fmt:
        dt = pd.to_datetime(series, format=fmt, errors="coerce")
    else:
        dt = pd.to_datetime(series, dayfirst=dayfirst, errors="coerce", format="mixed")
    if getattr(dt.dt, "tz", None) is not None:
        dt = dt.dt.tz_convert(tz).dt.tz_localize(None)
    elif source_tz:
        dt = dt.dt.tz_localize(source_tz, ambiguous="NaT", nonexistent="shift_forward") \
               .dt.tz_convert(tz).dt.tz_localize(None)
    return dt


def business_date(ts: pd.Series) -> pd.Series:
    """Día comercial: los pedidos antes de la hora de corte cuentan para el día anterior."""
    cutoff = int(config.settings()["business"].get("business_day_cutoff_hour", 0))
    return (ts - pd.Timedelta(hours=cutoff)).dt.date


class BranchResolver:
    """Traduce el identificador de tienda que trae cada plataforma a la sucursal interna."""

    def __init__(self) -> None:
        s = config.settings()
        self.by_name: dict[str, str] = {}
        for b in s["branches"]:
            for key in [b["id"], b["name"], *b.get("aliases", [])]:
                self.by_name[norm_text(key)] = b["id"]
        with db.engine().connect() as conn:
            rows = conn.execute(select(db.store_map)).all()
        self.by_store = {(r.platform_id, norm_text(r.external_store_id)): r.branch_id for r in rows}

    def resolve(self, platform_id: str, value) -> str | None:
        key = norm_text(value)
        if not key:
            return None
        return self.by_store.get((platform_id, key)) or self.by_name.get(key)


class ProductResolver:
    """Unifica nombres de producto entre plataformas (alias -> producto canónico).

    config/product_aliases.csv (platform,raw_name,canonical_name,category) permite
    declarar equivalencias, p.ej. 'Combo Clásico' en Rappi = 'Clásico + papas' en Justo.
    Los nombres nuevos se crean automáticamente como producto propio.
    """

    def __init__(self) -> None:
        self._load_alias_file()
        with db.engine().connect() as conn:
            self.alias = {(r.platform_id, r.raw_name): r.product_id
                          for r in conn.execute(select(db.product_alias)).all()}
            self.products = {r.name: r.product_id for r in conn.execute(select(db.product)).all()}

    def _load_alias_file(self) -> None:
        path = config.CONFIG_DIR / "product_aliases.csv"
        if not path.exists():
            return
        with open(path, encoding="utf-8") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("raw_name") and r.get("canonical_name")]
        for r in rows:
            pid = self._ensure_product(r["canonical_name"].strip(), r.get("category") or None)
            db.upsert(db.product_alias, [{"platform_id": r["platform"].strip(),
                                          "raw_name": norm_text(r["raw_name"]), "product_id": pid}])

    def _ensure_product(self, name: str, category: str | None = None) -> int:
        with db.engine().begin() as conn:
            row = conn.execute(select(db.product.c.product_id).where(db.product.c.name == name)).first()
            if row:
                return row[0]
            res = conn.execute(db.product.insert().values(name=name, category=category))
            return res.inserted_primary_key[0]

    def resolve(self, platform_id: str, raw_name: str) -> int:
        key = norm_text(raw_name)
        pid = self.alias.get((platform_id, key))
        if pid is None:
            clean = re.sub(r"\s+", " ", str(raw_name)).strip()
            pid = self.products.get(clean) or self._ensure_product(clean)
            self.products[clean] = pid
            db.upsert(db.product_alias, [{"platform_id": platform_id, "raw_name": key, "product_id": pid}])
            self.alias[(platform_id, key)] = pid
        return pid


def yesterday_business_date() -> date:
    import zoneinfo
    tz = zoneinfo.ZoneInfo(config.settings()["business"]["timezone"])
    cutoff = int(config.settings()["business"].get("business_day_cutoff_hour", 0))
    now_local = datetime.now(tz) - timedelta(hours=cutoff)
    return now_local.date() - timedelta(days=1)
