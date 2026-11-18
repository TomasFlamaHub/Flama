"""Carga de DataFrames canónicos a la base, con validación y registro de calidad.

Toda fuente (API, archivo, correo) termina en uno de estos 4 formatos canónicos:

  orders       order_id, store, created_at, status, gross_amount, [discount_merchant,
               discount_platform, commission, other_fees, payout_amount, cancel_reason]
  order_items  order_id, product, quantity, gross_amount, [line_no]
  adjustments  store, date, amount, [adjustment_id, order_id, kind, reason]
  store_day    store, date, [scheduled_minutes, open_minutes, offline_minutes, sessions]
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pandas as pd

from .. import config, db
from .normalize import BranchResolver, ProductResolver, business_date, norm_text

VALID_STATUS = {"completed", "cancelled", "rejected"}


@dataclass
class LoadResult:
    dataset: str
    rows_read: int = 0
    inserted: int = 0
    updated: int = 0
    rejected: int = 0
    issues: list[dict] = field(default_factory=list)

    def issue(self, severity: str, check: str, detail: str, platform_id=None, branch_id=None, business_date=None):
        self.issues.append({"severity": severity, "check_name": check, "detail": detail,
                            "platform_id": platform_id, "branch_id": branch_id, "business_date": business_date})

    @property
    def status(self) -> str:
        if any(i["severity"] == "error" for i in self.issues) or (self.rows_read and self.rejected == self.rows_read):
            return "error"
        return "warning" if self.issues else "ok"


def _nan_to_none(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: _nan_to_none(v) for k, v in r.items()} for r in df.to_dict("records")]


def _require(df: pd.DataFrame, cols: list[str], res: LoadResult, platform_id: str) -> pd.DataFrame:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        res.issue("error", "columnas_faltantes", f"Faltan columnas obligatorias: {missing}", platform_id)
        res.rejected = len(df)
        return df.iloc[0:0]
    bad = df[cols].isna().any(axis=1)
    if bad.any():
        res.rejected += int(bad.sum())
        res.issue("warning", "campos_vacios", f"{int(bad.sum())} filas sin {cols} fueron descartadas", platform_id)
    return df[~bad]


def _assign_branch(df: pd.DataFrame, platform_id: str, res: LoadResult) -> pd.DataFrame:
    resolver = BranchResolver()
    df = df.copy()
    df["branch_id"] = [resolver.resolve(platform_id, v) for v in df["store"]]
    unknown = df["branch_id"].isna()
    if unknown.any():
        stores = sorted({str(s) for s in df.loc[unknown, "store"]})[:10]
        res.rejected += int(unknown.sum())
        res.issue("error", "sucursal_desconocida",
                  f"{int(unknown.sum())} filas con tienda no mapeada {stores}. "
                  "Agregar a store_mapping en settings.yaml.", platform_id)
    return df[~unknown]


def load_orders(df: pd.DataFrame, platform_id: str, run_id: str, source: str, is_demo: bool = False) -> LoadResult:
    res = LoadResult("orders", rows_read=len(df))
    df = _require(df, ["order_id", "store", "created_at", "gross_amount"], res, platform_id)
    if df.empty:
        return res
    df = _assign_branch(df, platform_id, res)
    df = df.copy()
    df["order_id"] = df["order_id"].astype(str).str.strip()
    dups = df["order_id"].duplicated(keep="last")
    if dups.any():
        res.issue("info", "duplicados_en_archivo",
                  f"{int(dups.sum())} órdenes repetidas en la fuente; se conserva la última versión", platform_id)
        df = df[~dups]
    if "status" not in df.columns:
        df["status"] = "completed"
    df["status"] = df["status"].fillna("completed")
    bad_status = ~df["status"].isin(VALID_STATUS)
    if bad_status.any():
        res.rejected += int(bad_status.sum())
        res.issue("warning", "estado_desconocido",
                  f"Estados sin mapear: {sorted(set(df.loc[bad_status, 'status']))[:10]}", platform_id)
        df = df[~bad_status]
    neg = df["gross_amount"] < 0
    if neg.any():
        res.issue("warning", "monto_negativo", f"{int(neg.sum())} órdenes con venta negativa", platform_id)

    # Columnas financieras que la fuente realmente trae: solo esas se escriben, así una fuente sin
    # finanzas (p.ej. el historial de pedidos de Uber) no borra lo que cargó otra (el reporte de ganancias).
    FIN = ["discount_merchant", "discount_platform", "commission", "other_fees", "payout_amount"]
    provided = {c for c in FIN if c in df.columns}
    for c in ["discount_merchant", "discount_platform"]:
        df[c] = df[c].fillna(0.0) if c in df.columns else 0.0
    for c in ["commission", "other_fees", "payout_amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float) if c in df.columns else float("nan")
    if "cancel_reason" not in df.columns:
        df["cancel_reason"] = None
    # Liquidación: real si viene en la fuente; estimada si hay % contractual; si no, vacía.
    df["payout_is_estimated"] = False
    rate = config.settings()["platforms"].get(platform_id, {}).get("commission_rate")
    need = df["payout_amount"].isna() & (df["status"] == "completed")
    if need.any() and rate is not None:
        comm = df["commission"].where(df["commission"].notna(), df["gross_amount"] * float(rate))
        est = df["gross_amount"] - df["discount_merchant"] - comm - df["other_fees"].fillna(0.0)
        df.loc[need, "payout_amount"] = est[need]
        df.loc[need & df["commission"].isna(), "payout_is_estimated"] = True

    df["business_date"] = business_date(df["created_at"])
    now = db.now_utc()
    out = df.assign(platform_id=platform_id, is_demo=is_demo, source=source, run_id=run_id, updated_at=now)
    if rate is not None and need.any():
        provided |= {"payout_amount"}
    cols = [c.name for c in db.orders.columns
            if c.name not in FIN or c.name in provided] + (["payout_is_estimated"] if "payout_amount" in provided else [])
    cols = [c for c in dict.fromkeys(cols) if c != "payout_is_estimated" or "payout_amount" in provided]
    res.inserted, res.updated = db.upsert(db.orders, _records(out[cols]))
    return res


def load_order_items(df: pd.DataFrame, platform_id: str, run_id: str, source: str, is_demo: bool = False) -> LoadResult:
    res = LoadResult("order_items", rows_read=len(df))
    df = _require(df, ["order_id", "product", "quantity"], res, platform_id)
    if df.empty:
        return res
    df = df.copy()
    df["order_id"] = df["order_id"].astype(str).str.strip()
    if "gross_amount" not in df.columns:
        df["gross_amount"] = None
        res.issue("warning", "item_sin_monto", "El reporte de ítems no trae monto; la venta por producto quedará vacía",
                  platform_id)
    if "line_no" not in df.columns:
        df["line_no"] = df.groupby("order_id").cumcount() + 1
    pr = ProductResolver()
    df["product_id"] = [pr.resolve(platform_id, p) for p in df["product"]]
    out = df.rename(columns={"product": "product_raw"}).assign(
        platform_id=platform_id, is_demo=is_demo, source=source, run_id=run_id)
    out["gross_amount"] = out["gross_amount"].fillna(0.0)
    cols = [c.name for c in db.order_items.columns]
    res.inserted, res.updated = db.upsert(db.order_items, _records(out[cols]))
    return res


def load_adjustments(df: pd.DataFrame, platform_id: str, run_id: str, source: str, is_demo: bool = False) -> LoadResult:
    res = LoadResult("adjustments", rows_read=len(df))
    df = _require(df, ["store", "date", "amount"], res, platform_id)
    if df.empty:
        return res
    df = _assign_branch(df, platform_id, res).copy()
    df["amount"] = df["amount"].abs()  # convención: monto positivo = dinero perdido por el local
    df["business_date"] = pd.to_datetime(df["date"]).dt.date
    for c in ["order_id", "kind", "reason"]:
        if c not in df.columns:
            df[c] = None
    if "adjustment_id" not in df.columns or df["adjustment_id"].isna().any():
        # ID determinístico para que recargar el mismo archivo no duplique resarcimientos
        key = df[["branch_id", "business_date", "order_id", "amount", "reason"]].astype(str).agg("|".join, axis=1)
        occurrence = key.groupby(key).cumcount().astype(str)
        gen = (key + "#" + occurrence).map(lambda k: hashlib.sha1(k.encode()).hexdigest()[:20])
        df["adjustment_id"] = df["adjustment_id"].fillna(gen) if "adjustment_id" in df.columns else gen
    out = df.assign(platform_id=platform_id, is_demo=is_demo, source=source, run_id=run_id)
    out["adjustment_id"] = out["adjustment_id"].astype(str)
    cols = [c.name for c in db.adjustments.columns]
    res.inserted, res.updated = db.upsert(db.adjustments, _records(out[cols]))
    return res


def load_store_day(df: pd.DataFrame, platform_id: str, run_id: str, source: str, is_demo: bool = False) -> LoadResult:
    res = LoadResult("store_day", rows_read=len(df))
    df = _require(df, ["store", "date"], res, platform_id)
    if df.empty:
        return res
    df = _assign_branch(df, platform_id, res).copy()
    df["business_date"] = pd.to_datetime(df["date"]).dt.date
    for c in ["scheduled_minutes", "open_minutes", "sessions", "offline_minutes"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float) if c in df.columns else float("nan")
    fill = df["open_minutes"].isna() & df["offline_minutes"].notna() & df["scheduled_minutes"].notna()
    if fill.any():
        df.loc[fill, "open_minutes"] = df.loc[fill, "scheduled_minutes"] - df.loc[fill, "offline_minutes"]
    # Varias filas por día (p.ej. reporte horario) -> se suman
    agg = df.groupby(["branch_id", "business_date"], as_index=False)[
        ["scheduled_minutes", "open_minutes", "offline_minutes", "sessions"]].sum(min_count=1)
    over = agg["open_minutes"] > agg["scheduled_minutes"] + 1
    if over.any():
        res.issue("warning", "open_time_mayor_a_horario",
                  f"{int(over.sum())} días con Open Time mayor al horario programado", platform_id)
    out = agg.assign(platform_id=platform_id, is_demo=is_demo, source=source, run_id=run_id)
    cols = [c.name for c in db.store_day.columns]
    res.inserted, res.updated = db.upsert(db.store_day, _records(out[cols]))
    return res


def load_funnel(df: pd.DataFrame, platform_id: str, run_id: str, source: str, is_demo: bool = False) -> LoadResult:
    """Embudo por período: store, start, end, [store_views, menu_views, add_to_cart, orders, conversion_reported].

    store puede ser una tienda o 'TODAS' (todas las tiendas de la marca)."""
    res = LoadResult("funnel_period", rows_read=len(df))
    df = _require(df, ["store", "start", "end"], res, platform_id)
    if df.empty:
        return res
    resolver = BranchResolver()
    out = []
    for _, r in df.iterrows():
        st = str(r["store"]).strip()
        if norm_text(st) in ("todas", "all", "total"):
            brands = [b for b, v in config.brands().items() if platform_id in v["platforms"]]
            b = f"ALL_{r.get('brand') or (brands[0] if brands else 'kobo')}"
        else:
            b = resolver.resolve(platform_id, st)
        if not b:
            res.rejected += 1
            res.issue("error", "sucursal_desconocida", f"Tienda no mapeada en embudo: {st}", platform_id)
            continue
        f = lambda c: (None if c not in r or pd.isna(r[c]) else float(r[c]))  # noqa: E731
        conv = f("conversion_reported")
        out.append({"platform_id": platform_id, "branch_id": b,
                    "start_date": pd.to_datetime(r["start"]).date(), "end_date": pd.to_datetime(r["end"]).date(),
                    "store_views": f("store_views"), "menu_views": f("menu_views"), "add_to_cart": f("add_to_cart"),
                    "orders": f("orders"), "conversion_reported": conv / 100 if conv and conv > 1 else conv,
                    "is_demo": is_demo, "source": source, "run_id": run_id})
    res.inserted, res.updated = db.upsert(db.funnel_period, out)
    return res


def load_campaigns(df: pd.DataFrame, platform_id: str, run_id: str, source: str, is_demo: bool = False) -> LoadResult:
    res = LoadResult("campaigns", rows_read=len(df))
    df = _require(df, ["campaign", "store", "start", "end"], res, platform_id)
    resolver = BranchResolver()
    out = []
    for _, r in df.iterrows():
        st = str(r["store"]).strip()
        if norm_text(st) in ("todas", "all", "total"):
            brands = [b for b, v in config.brands().items() if platform_id in v["platforms"]]
            b = f"ALL_{brands[0] if brands else 'kobo'}"
        else:
            b = resolver.resolve(platform_id, st)
        if not b:
            res.rejected += 1
            res.issue("error", "sucursal_desconocida", f"Tienda no mapeada en campañas: {st}", platform_id)
            continue
        f = lambda c: (None if c not in r or pd.isna(r[c]) else float(r[c]))  # noqa: E731
        out.append({"platform_id": platform_id, "campaign": str(r["campaign"]).strip()[:200], "branch_id": b,
                    "start_date": pd.to_datetime(r["start"]).date(), "end_date": pd.to_datetime(r["end"]).date(),
                    "kind": (str(r.get("kind") or "ads")).strip().lower(), "spend": f("spend"),
                    "impressions": f("impressions"), "clicks": f("clicks"), "orders": f("orders"),
                    "sales": f("sales"), "new_customers": f("new_customers"), "roas_reported": f("roas_reported"),
                    "source": source, "run_id": run_id})
    res.inserted, res.updated = db.upsert(db.campaigns, out)
    return res


LOADERS = {
    "orders": load_orders,
    "order_items": load_order_items,
    "adjustments": load_adjustments,
    "store_day": load_store_day,
    "funnel_period": load_funnel,
    "campaigns": load_campaigns,
}


def record_run(run_id: str, started, platform_id: str | None, source_type: str, source_name: str,
               results: list[LoadResult], file_hash: str | None = None, message: str = "") -> str:
    status = "ok"
    for r in results:
        if r.status == "error":
            status = "error"
        elif r.status == "warning" and status == "ok":
            status = "warning"
    db.insert_rows(db.ingestion_run, [{
        "run_id": run_id, "started_at": started, "finished_at": db.now_utc(), "platform_id": platform_id,
        "source_type": source_type, "source_name": source_name, "file_hash": file_hash,
        "dataset": ",".join(r.dataset for r in results),
        "rows_read": sum(r.rows_read for r in results), "rows_inserted": sum(r.inserted for r in results),
        "rows_updated": sum(r.updated for r in results), "rows_rejected": sum(r.rejected for r in results),
        "status": status, "message": message,
    }])
    issues = [dict(i, run_id=run_id, detected_at=db.now_utc()) for r in results for i in r.issues]
    db.insert_rows(db.dq_issue, issues)
    return status


def text_key(*parts) -> str:
    return "|".join(norm_text(p) for p in parts)
