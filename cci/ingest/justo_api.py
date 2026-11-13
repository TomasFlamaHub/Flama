"""Conector API de Justo (v3).

Lo verificado en la documentación pública (docs.getjusto.com):
  * Autenticación con header  Authorization: Bearer <token>.
  * El token se crea en el portal de desarrolladores de Justo (developers.getjusto.com)
    o, en la versión antigua, en Plataforma > API > API Keys.
  * Existe un endpoint de información del token: /v3/api/me
  * Los webhooks se firman con el header X-Hub-Signature.

Lo NO verificado (hay que confirmarlo con la documentación v3 o con soporte de Justo):
  * URL base exacta, endpoint para listar órdenes por rango de fechas, paginación y
    nombres de campos. Por eso todo eso se configura en config/mappings/justo_api.yaml
    y el conector se niega a cargar mientras ese archivo tenga verified: false.
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timedelta

import pandas as pd
import requests
import yaml

from .. import config, db
from . import loader
from .normalize import to_local_datetime, norm_text


class JustoNotConfigured(RuntimeError):
    pass


def _get(obj, path: str):
    """Lee 'a.b.0.c' de un dict/list anidado."""
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            cur = cur[int(part)] if part.isdigit() and int(part) < len(cur) else None
        else:
            cur = cur.get(part)
    return cur


class JustoClient:
    def __init__(self) -> None:
        self.base = os.environ.get("JUSTO_API_BASE", "").rstrip("/")
        self.token = os.environ.get("JUSTO_API_TOKEN", "")
        path = config.CONFIG_DIR / "mappings" / "justo_api.yaml"
        self.cfg = yaml.safe_load(open(path, encoding="utf-8")) if path.exists() else {}
        if not self.base or not self.token:
            raise JustoNotConfigured("Faltan JUSTO_API_BASE y/o JUSTO_API_TOKEN en el entorno.")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {self.token}", "Accept": "application/json"})

    def whoami(self) -> dict:
        r = self.s.get(f"{self.base}/v3/api/me", timeout=30)
        r.raise_for_status()
        return r.json()

    def fetch_orders(self, start: date, end: date) -> list[dict]:
        if not self.cfg.get("verified"):
            raise JustoNotConfigured("config/mappings/justo_api.yaml no está verificado (endpoint y campos "
                                     "de órdenes pendientes de confirmar con la documentación de Justo).")
        c = self.cfg
        tz = config.settings()["business"]["timezone"]
        cutoff = int(config.settings()["business"].get("business_day_cutoff_hour", 0))
        t0 = pd.Timestamp(datetime.combine(start, time(cutoff)), tz=tz)
        t1 = pd.Timestamp(datetime.combine(end + timedelta(days=1), time(cutoff)), tz=tz)
        params = {c["params"]["from"]: t0.isoformat(), c["params"]["to"]: t1.isoformat()}
        out, page = [], c.get("pagination", {}).get("first_page", 1)
        while True:
            if c.get("pagination"):
                params[c["pagination"]["param"]] = page
            r = self.s.get(f"{self.base}{c['orders_path']}", params=params, timeout=60)
            r.raise_for_status()
            items = _get(r.json(), c["items_path"]) or []
            out.extend(items)
            if not c.get("pagination") or len(items) < c["pagination"].get("page_size", 1 << 30):
                break
            page += 1
        return out

    def to_frames(self, raw: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
        f = self.cfg["fields"]
        orders = pd.DataFrame([{k: _get(o, v) for k, v in f["order"].items()} for o in raw])
        if orders.empty:
            return orders, pd.DataFrame()
        orders["created_at"] = to_local_datetime(orders["created_at"])
        smap = {norm_text(k): v for k, v in (self.cfg.get("status_map") or {}).items()}
        orders["status"] = orders["status"].map(lambda s: smap.get(norm_text(s), f"?{s}"))
        items = []
        for o in raw:
            oid = _get(o, f["order"]["order_id"])
            for i, it in enumerate(_get(o, f["items_path"]) or [], start=1):
                items.append({"order_id": oid, "line_no": i, **{k: _get(it, v) for k, v in f["item"].items()}})
        return orders, pd.DataFrame(items)


def ingest_justo(start: date, end: date) -> str:
    run_id, started = uuid.uuid4().hex[:16], db.now_utc()
    try:
        client = JustoClient()
        orders, items = client.to_frames(client.fetch_orders(start, end))
        results = [loader.load_orders(orders, "justo", run_id, "justo_api")]
        if not items.empty:
            results.append(loader.load_order_items(items, "justo", run_id, "justo_api"))
        return loader.record_run(run_id, started, "justo", "api", f"justo {start}..{end}", results)
    except JustoNotConfigured as exc:
        r = loader.LoadResult("orders")
        r.issue("warning", "api_no_configurada", str(exc), "justo")
        loader.record_run(run_id, started, "justo", "api", "justo", [r], message=str(exc))
        return "skipped"
    except requests.RequestException as exc:
        r = loader.LoadResult("orders")
        r.issue("error", "api_error", f"Justo API: {exc}", "justo")
        return loader.record_run(run_id, started, "justo", "api", "justo", [r], message=str(exc)[:2000])
