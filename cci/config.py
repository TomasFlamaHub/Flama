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
    return f"sqlite:///{DATA_DIR / 'cci.db'}"


def branch_names() -> dict[str, str]:
    return {b["id"]: b["name"] for b in settings()["branches"]}


def platform_names() -> dict[str, str]:
    return {k: v["name"] for k, v in settings()["platforms"].items()}
