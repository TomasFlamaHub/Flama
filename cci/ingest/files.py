"""Lectura de reportes descargables (CSV / Excel) guiada por mapeos YAML.

Cada formato de reporte tiene un archivo en config/mappings/*.yaml que dice qué
columna del reporte corresponde a cada campo canónico. Un mapeo con
`verified: false` NO se usa: así nunca se cargan datos con columnas adivinadas.
Para crear un mapeo nuevo:  python -m cci.cli profile <archivo>
"""
from __future__ import annotations

import fnmatch
import hashlib
import shutil
import uuid
from datetime import date
from pathlib import Path

import pandas as pd
import yaml
from sqlalchemy import select

from .. import config, db
from . import loader
from .normalize import to_local_datetime, to_number, norm_text

NUMERIC = {"gross_amount", "discount_merchant", "discount_platform", "commission", "other_fees",
           "payout_amount", "quantity", "amount", "scheduled_minutes", "open_minutes",
           "offline_minutes", "sessions", "line_no"}
ORDER_FIELDS = ["order_id", "store", "created_at", "status", "gross_amount", "discount_merchant",
                "discount_platform", "commission", "other_fees", "payout_amount", "cancel_reason"]


def load_mappings(include_unverified: bool = False) -> list[dict]:
    out = []
    for path in sorted((config.CONFIG_DIR / "mappings").glob("*.yaml")):
        with open(path, encoding="utf-8") as fh:
            m = yaml.safe_load(fh)
        m["_file"] = path.name
        if m.get("verified") or include_unverified:
            out.append(m)
    return out


def match_mapping(file_name: str, mappings: list[dict]) -> dict | None:
    name = file_name.lower()
    for m in mappings:
        globs = m.get("file_glob") or []
        globs = [globs] if isinstance(globs, str) else globs
        if any(fnmatch.fnmatch(name, g.lower()) for g in globs):
            return m
    return None


def read_raw(path: Path, read_opts: dict | None = None) -> pd.DataFrame:
    o = read_opts or {}
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(path, sheet_name=o.get("sheet", 0), header=o.get("header_row", 0), dtype=object)
    return pd.read_csv(path, sep=o.get("sep", None), engine="python", header=o.get("header_row", 0),
                       encoding=o.get("encoding", "utf-8-sig"), dtype=str)


def apply_mapping(raw: pd.DataFrame, m: dict) -> pd.DataFrame:
    """Renombra columnas al formato canónico y tipa los valores."""
    cols = m.get("columns", {})
    missing = [src for src in cols.values() if src not in raw.columns]
    if missing:
        raise ValueError(f"El archivo no tiene las columnas esperadas por {m['_file']}: {missing}. "
                         f"Columnas presentes: {list(raw.columns)}")
    df = pd.DataFrame({canon: raw[src] for canon, src in cols.items()})
    for k, v in (m.get("constants") or {}).items():
        df[k] = v
    ro = m.get("read", {}) or {}
    for c in df.columns:
        if c in NUMERIC:
            df[c] = to_number(df[c], ro.get("decimal", "."), ro.get("thousands"))
    for c in ("created_at",):
        if c in df.columns:
            df[c] = to_local_datetime(df[c], m.get("datetime_format"), m.get("source_timezone"),
                                      ro.get("dayfirst", True))
    if "date" in df.columns:
        df["date"] = to_local_datetime(df["date"], m.get("date_format"), None, ro.get("dayfirst", True)).dt.date
    if "status" in df.columns and m.get("status_map"):
        smap = {norm_text(k): v for k, v in m["status_map"].items()}
        df["status"] = df["status"].map(lambda s: smap.get(norm_text(s), f"?{s}"))
    for sign_col in m.get("negate", []) or []:
        if sign_col in df.columns:
            df[sign_col] = -df[sign_col]
    return df


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def already_loaded(h: str) -> bool:
    with db.engine().connect() as conn:
        row = conn.execute(select(db.ingestion_run.c.run_id).where(
            (db.ingestion_run.c.file_hash == h) & (db.ingestion_run.c.status.in_(["ok", "warning"])))).first()
    return row is not None


def ingest_file(path: Path, mapping: dict, is_demo: bool = False) -> str:
    run_id = uuid.uuid4().hex[:16]
    started = db.now_utc()
    h = file_hash(path)
    platform_id = mapping.get("platform")
    results: list[loader.LoadResult] = []
    try:
        df = apply_mapping(read_raw(path, mapping.get("read")), mapping)
        dataset = mapping["dataset"]
        platforms = [platform_id] if platform_id != "from_column" else sorted(df["platform"].dropna().unique())
        for p in platforms:
            part = df if platform_id != "from_column" else df[df["platform"] == p]
            if dataset == "orders_with_items":
                order_cols = [c for c in ORDER_FIELDS if c in part.columns]
                orders_df = part[order_cols].drop_duplicates("order_id", keep="last")
                results.append(loader.load_orders(orders_df, p, run_id, path.name, is_demo))
                item_cols = [c for c in ["order_id", "product", "quantity", "item_amount", "line_no"] if c in part.columns]
                items = part[item_cols].rename(columns={"item_amount": "gross_amount"})
                results.append(loader.load_order_items(items, p, run_id, path.name, is_demo))
            else:
                results.append(loader.LOADERS[dataset](part, p, run_id, path.name, is_demo))
        status = loader.record_run(run_id, started, platform_id, "file", path.name, results, h,
                                   f"mapeo {mapping['_file']}")
    except Exception as exc:  # el error queda registrado y la corrida sigue con otros archivos
        r = loader.LoadResult(mapping.get("dataset", "?"))
        r.issue("error", "archivo_ilegible", f"{path.name}: {exc}", platform_id)
        status = loader.record_run(run_id, started, platform_id, "file", path.name, [r], h, str(exc)[:2000])
    return status


def native_parsers():
    """Lectores específicos de reportes reales (formatos complejos: varias hojas, encabezados en 2 filas)."""
    from . import rappi, ubereats
    return rappi.PARSERS + ubereats.PARSERS


def match_parser(path: Path):
    for name, detect, load in native_parsers():
        try:
            if detect(path):
                return name, load
        except Exception:
            continue
    return None


def ingest_with_parser(path: Path, name: str, load) -> str:
    run_id, started, h = uuid.uuid4().hex[:16], db.now_utc(), file_hash(path)
    platform_id = name.split("_")[0]
    try:
        results = load(path, run_id)
        return loader.record_run(run_id, started, platform_id, "file", path.name, results, h, f"lector {name}")
    except Exception as exc:
        r = loader.LoadResult(name)
        r.issue("error", "archivo_ilegible", f"{path.name}: {exc}", platform_id)
        return loader.record_run(run_id, started, platform_id, "file", path.name, [r], h, str(exc)[:2000])


def ingest_inbox(inbox: Path | None = None, processed: Path | None = None, is_demo: bool = False) -> list[dict]:
    """Procesa todos los archivos de data/inbox. Los cargados se mueven a data/processed/AAAA-MM-DD/."""
    inbox = inbox or config.DATA_DIR / "inbox"
    processed = processed or config.DATA_DIR / "processed"
    mappings = load_mappings()
    summary = []
    order = {name: i for i, (name, _, _) in enumerate(native_parsers())}

    def _prio(p):
        m = match_parser(p)
        return (order.get(m[0], 99) if m else 100, p.name)

    for path in sorted((p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")), key=_prio):
        h = file_hash(path)
        if already_loaded(h):
            summary.append({"file": path.name, "status": "skipped (ya cargado)"})
            _move(path, processed)
            continue
        parser = match_parser(path)
        if parser:
            status = ingest_with_parser(path, *parser)
            summary.append({"file": path.name, "status": status, "mapping": parser[0]})
            if status != "error":
                _move(path, processed)
            continue
        m = match_mapping(path.name, mappings)
        if not m:
            db.insert_rows(db.dq_issue, [{"detected_at": db.now_utc(), "severity": "warning",
                                          "check_name": "archivo_sin_mapeo",
                                          "detail": f"{path.name}: ningún mapeo verificado coincide con el nombre"}])
            summary.append({"file": path.name, "status": "sin mapeo"})
            continue
        status = ingest_file(path, m, is_demo)
        summary.append({"file": path.name, "status": status, "mapping": m["_file"]})
        if status != "error":
            _move(path, processed)
    return summary


def _move(path: Path, processed: Path) -> None:
    dest = processed / date.today().isoformat()
    dest.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), dest / path.name)


def profile(path: Path, sheet=None, rows: int = 5) -> str:
    """Muestra hojas, columnas y filas de ejemplo de un reporte, para construir su mapeo."""
    lines = [f"Archivo: {path.name}"]
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        xl = pd.ExcelFile(path)
        lines.append(f"Hojas: {xl.sheet_names}")
        sheets = [sheet] if sheet is not None else xl.sheet_names
        for sh in sheets:
            df = pd.read_excel(path, sheet_name=sh, nrows=rows, dtype=object)
            lines += [f"\n--- Hoja '{sh}' ---", f"Columnas: {list(df.columns)}", df.to_string(max_colwidth=30)]
    else:
        df = read_raw(path).head(rows)
        lines += [f"Columnas: {list(df.columns)}", df.to_string(max_colwidth=30)]
    return "\n".join(lines)
