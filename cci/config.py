"""Carga de configuración (settings.yaml + variables de entorno)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("CCI_CONFIG_DIR", ROOT / "config"))
DATA_DIR = Path(os.environ.get("CCI_DATA_DIR", ROOT / "data"))

PLATFORMS = ("justo", "rappi", "ubereats")


@lru_cache(maxsize=1)
def settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def database_url() -> str:
    """Postgres en producción (Supabase/Neon). SQLite local por defecto."""
    url = os.environ.get("DATABASE_URL")
    if url:
        # Supabase/Heroku entregan postgres://; SQLAlchemy requiere postgresql+psycopg://
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_file = DATA_DIR / "cci.db"
    # En el panel publicado la base viaja comprimida (cci.db.gz, en data/ o en la raíz del repo).
    gz = next((g for g in (DATA_DIR / "cci.db.gz", ROOT / "cci.db.gz") if g.exists()), None)
    if gz and (not db_file.exists() or gz.stat().st_mtime > db_file.stat().st_mtime):
        import gzip
        import shutil
        with gzip.open(gz, "rb") as src, open(db_file, "wb") as dst:
            shutil.copyfileobj(src, dst)
    return f"sqlite:///{db_file}"


def branch_names() -> dict[str, str]:
    return {b["id"]: b["name"] for b in settings()["branches"]}


def platform_names() -> dict[str, str]:
    return {k: v["name"] for k, v in settings()["platforms"].items()}


def brands() -> dict[str, dict]:
    """{brand_id: {name, platforms}}. Sin marcas configuradas = una marca única."""
    bs = settings().get("brands") or [{"id": "default", "name": settings()["business"]["name"],
                                        "platforms": list(PLATFORMS)}]
    return {b["id"]: b for b in bs}


def brand_of(branch_id: str | None) -> str | None:
    if branch_id is None:
        return None
    for b in settings()["branches"]:
        if b["id"] == branch_id:
            return b.get("brand", next(iter(brands())))
    return None


def branches_of(brand_id: str | None) -> list[str]:
    return [b["id"] for b in settings()["branches"]
            if brand_id is None or b.get("brand", next(iter(brands()))) == brand_id]
