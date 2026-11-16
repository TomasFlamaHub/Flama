"""Lectores de los reportes reales de Rappi Partners (formatos verificados con archivos del 15-11-2026).

  * Relación de ventas (Finanzas › Financiero): Excel con hojas Indice, Resumen, Detalle,
    Compensaciones, Órdenes canceladas no pagadas.  -> orders, adjustments, costs
  * Nivel de servicio (Rendimiento › Nivel de servicio): Excel con hojas Resumen por tienda,
    Cancelaciones, Disponibilidad de tiendas, Reclamos - Órdenes, Reclamos - Productos,
    Tiempo de espera del repartidor.  -> quality_events, store_day.offline_minutes, order_ops
  * Reseñas (Rendimiento › Reseñas, CSV por tienda).  -> reviews
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from .. import db
from . import loader
from .normalize import BranchResolver, norm_text, to_local_datetime

P = "rappi"
MESES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8, "sep": 9,
         "sept": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12}
_RX = re.compile(r"(\d{1,2})\s+([a-záé]+)\.?\s+(\d{4}),?\s+(\d{1,2}):(\d{2}):(\d{2})\s*([ap])\.?\s*m\.?", re.I)


def parse_es_datetime(s) -> datetime | None:
    """'vie. 16 oct. 2026, 11:57:37 a. m.' -> datetime(2026,10,16,11,57,37)."""
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return None
    if isinstance(s, datetime):
        return s
    m = _RX.search(str(s))
    if not m:
        ts = pd.to_datetime(str(s), dayfirst=True, errors="coerce")
        return None if pd.isna(ts) else ts.to_pydatetime()
    d, mon, y, hh, mm, ss, ap = m.groups()
    h = int(hh) % 12 + (12 if ap.lower() == "p" else 0)
    return datetime(int(y), MESES[mon.lower().rstrip(".")], int(d), h, int(mm), int(ss))


def _num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def _sheets(path: Path) -> list[str]:
    try:
        return pd.ExcelFile(path).sheet_names
    except Exception:
        return []


# ------------------------------------------------------------------ detección
def is_relacion_ventas(path: Path) -> bool:
    return path.suffix.lower() == ".xlsx" and {"Detalle", "Compensaciones"} <= set(_sheets(path))


def is_service_level(path: Path) -> bool:
    return path.suffix.lower() == ".xlsx" and {"Resumen por tienda", "Cancelaciones"} <= set(_sheets(path))


def is_reviews(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    head = path.read_bytes()[:300].decode("utf-8-sig", "ignore")
    return head.startswith("Tienda,ID Tienda,ID Orden,Fecha de creación,Calificación")


# ------------------------------------------------------------------ relación de ventas
MERCHANT_DISC = ["Descuento en créditos", "Descuento de Producto asumido por el aliado",
                 "Cashback en Créditos de Rappi asumido por el aliado",
                 "Challenge en Créditos de Rappi asumidos por el aliado", "Cashback 15 mis o gratis",
                 "Pro Rewards", "PRO Cashback en Créditos de Rappi asumido por el aliado"]
COMMISSION = ["Uso y alquiler de plataforma Rappi",
              "Descuento por inversión de Rappi  a aplicar sobre Uso y alquiler de plataforma Rappi DAR",
              "Uso y Alquiler de plataforma Rappi para órdenes Pro", "IVA Uso y alquiler de plataforma Rappi",
              "Descuento por inversión de Rappi a aplicar sobre el IVA Uso y alquiler de plataforma Rappi DAR"]
OTHER_FEES = ["Costo Canceladas", "Tarifa de Integration", "Tarifa por demora", "Tarifa Transaccional",
              "Comisión billetera virtual", "Tarifa por activación (marketplace)", "Tarifa de servicio al usuario",
              "Contracargos", "Descuento por Service Fee", "Servicio de Cargo", "IVA Campañas",
              "IVA Descuento por Service Fee", "IVA Servicio de Cargo"]
STATUS = {"pending_review": "completed", "finished": "completed", "canceled": "cancelled",
          "canceled_with_charge": "cancelled", "canceled_for_payment_error": "rejected",
          "canceled_by_partner_inactivity": "cancelled", "canceled_by_automation": "cancelled"}


def _sum(df, cols):
    return sum((_num(df[c]) for c in cols if c in df.columns), pd.Series(0.0, index=df.index))


def load_relacion_ventas(path: Path, run_id: str) -> list[loader.LoadResult]:
    det = pd.read_excel(path, sheet_name="Detalle", header=1, dtype=object)
    det.columns = [str(c).strip() for c in det.columns]
    det["ts"] = det["Fecha de creación orden"].map(parse_es_datetime)
    tipo = det["Tipo de transacción"].astype(str).str.strip().str.upper()
    results = []

    # --- órdenes (descarta filas retroactivas en cero que duplican la orden)
    o = det[tipo == "ORDEN"].copy()
    retro = o["Retroactivo"].astype(str).str.lower().eq("true") & (_num(o["Venta Bruta"]) == 0)
    o = o[~retro]
    raw_status = o["Estado de la órden"].astype(str).str.strip()
    orders = pd.DataFrame({
        "order_id": o["ID de la órden"].astype(str).str.strip(),
        "store": o["ID de la tienda"].astype(str).str.strip(),
        "created_at": pd.to_datetime(o["ts"]),
        "status": raw_status.map(lambda s: STATUS.get(s, f"?{s}")),
        "gross_amount": _num(o["Venta Bruta"]),
        "discount_merchant": -_sum(o, MERCHANT_DISC),
        "discount_platform": -_num(o.get("Descuentos por inversión de Rappi DAR", 0)),
        "commission": -_sum(o, COMMISSION),
        "other_fees": -_sum(o, OTHER_FEES),
        "cancel_reason": raw_status.where(raw_status.str.startswith("canceled"), None),
    })
    # las órdenes canceladas vienen con venta en negativo (reverso): se guarda el monto afectado en positivo
    nc = orders["status"] != "completed"
    orders.loc[nc, "gross_amount"] = orders.loc[nc, "gross_amount"].abs()
    orders.loc[nc, "discount_merchant"] = orders.loc[nc, "discount_merchant"].abs()
    orders["payout_amount"] = (orders["gross_amount"] - orders["discount_merchant"] - orders["commission"]
                               - orders["other_fees"]).where(orders["status"] == "completed")
    results.append(loader.load_orders(orders, P, run_id, path.name))

    # --- compensaciones (resarcimientos): monto desde Detalle, motivo desde la hoja Compensaciones
    comp = det[tipo.str.startswith("COMPENSACI")].copy()
    if not comp.empty:
        reasons = {}
        try:
            ct = pd.read_excel(path, sheet_name="Compensaciones", header=1, dtype=object)
            ct = ct.iloc[:, :8]
            ct.columns = ["order_id", "fecha", "razon", "paidlot", "monto", "productos", "ids", "comentarios"]
            for oid, g in ct.dropna(subset=["order_id"]).groupby(ct["order_id"].astype(str).str.strip()):
                reasons[oid] = ", ".join(sorted({str(r) for r in g["razon"].dropna()}))
                reasons[oid + "#prod"] = "; ".join(sorted({str(r).strip('"') for r in g["productos"].dropna()}))[:290]
        except Exception:
            pass
        comp["oid"] = comp["ID de la órden"].astype(str).str.strip()
        comp["n"] = comp.groupby("oid").cumcount()
        adj = pd.DataFrame({
            "adjustment_id": "comp-" + comp["oid"] + "-" + comp["n"].astype(str),
            "order_id": comp["oid"], "store": comp["ID de la tienda"].astype(str).str.strip(),
            "date": pd.to_datetime(comp["ts"]).dt.date, "amount": -_num(comp["Compensaciones"]),
            "kind": "compensacion",
            "reason": [(reasons.get(x) or "sin motivo") + (f" | {reasons[x + '#prod']}" if reasons.get(x + "#prod") else "")
                       for x in comp["oid"]],
        })
        results.append(loader.load_adjustments(adj, P, run_id, path.name))

    # --- costos que no son por pedido: Rappi Ads (EXTRA SERVICE) y ajustes manuales
    other = det[tipo.isin(["EXTRA SERVICE", "AJUSTE"])].copy()
    if not other.empty:
        res = loader.LoadResult("costs", rows_read=len(other))
        br = BranchResolver()
        rows = []
        for i, r in other.iterrows():
            b = br.resolve(P, str(r["ID de la tienda"]).strip())
            if not b:
                res.rejected += 1
                continue
            if str(r["Tipo de transacción"]).strip().upper() == "EXTRA SERVICE":
                amt = -(float(_num(pd.Series([r.get("Cuota de RappiAds")])).iloc[0])
                        + float(_num(pd.Series([r.get("IVA Rappi Ads")])).iloc[0]))
                kind = "ads"
            else:
                amt = -float(_num(pd.Series([r.get("Valor Ajustes Manuales")])).iloc[0])  # positivo = costo
                kind = "ajuste"
            desc = str(r.get("Descripción o comentarios (Ajustes  / RADs)", "") or r.get("Razon (Ajuste / RADs)", ""))
            key = "|".join(str(x) for x in [r.get("ID del paidlot"), r.get("ID de la órden"), r.get("ID de la tienda"),
                                            r.get("ID relacionado (global offer / campaña ADs / paidlot deuda)"), kind, amt])
            ts = r["ts"]
            rows.append({"platform_id": P, "cost_id": hashlib.sha1(key.encode()).hexdigest()[:24], "branch_id": b,
                         "business_date": ts.date() if ts else None, "kind": kind, "amount": amt,
                         "description": desc[:500], "is_demo": False, "source": path.name, "run_id": run_id})
        res.inserted, res.updated = db.upsert(db.costs, [x for x in rows if x["business_date"]])
        results.append(res)
    return results


# ------------------------------------------------------------------ nivel de servicio
def _table(path: Path, sheet: str, first_col: str) -> pd.DataFrame:
    """Las hojas tienen títulos arriba; busca la fila de encabezados por el nombre de la primera columna."""
    raw = pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    for i in range(min(len(raw), 20)):
        vals = [str(v).strip().upper() for v in raw.iloc[i].tolist()]
        if first_col.upper() in vals:
            df = raw.iloc[i + 1:].copy()
            names, seen = [], {}
            for v in raw.iloc[i].tolist():
                n = str(v).strip()
                seen[n] = seen.get(n, 0) + 1
                names.append(n if seen[n] == 1 else f"{n}.{seen[n] - 1}")
            df.columns = names
            df = df.loc[:, [c for c in df.columns if not c.startswith("nan")]]
            return df.dropna(how="all")
    return pd.DataFrame()


def _qe(rows, kind, df, path, run_id, br, res, reason_col, detail_col=None, product_col=None, amount_col=None):
    for _, r in df.iterrows():
        oid = str(r.get("ORDEN ID", "")).strip()
        b = br.resolve(P, str(r.get("TIENDA ID", "")).strip())
        d = pd.to_datetime(r.get("FECHA"), dayfirst=True, errors="coerce")
        if not b or pd.isna(d):
            res.rejected += 1
            continue
        prod = str(r.get(product_col, "") or "") if product_col else None
        key = f"{kind}|{oid}|{prod or ''}|{r.get(reason_col)}"
        rows.append({"platform_id": P, "event_id": hashlib.sha1(key.encode()).hexdigest()[:24], "kind": kind,
                     "order_id": oid, "branch_id": b, "business_date": d.date(),
                     "reason": str(r.get(reason_col) or "")[:200],
                     "detail": str(r.get(detail_col) or "") if detail_col else None,
                     "product": prod[:300] if prod else None,
                     "amount": float(_num(pd.Series([str(r.get(amount_col, "0")).replace("$", "").replace(",", "")])).iloc[0])
                     if amount_col else None,
                     "is_demo": False, "source": path.name, "run_id": run_id})


def load_service_level(path: Path, run_id: str) -> list[loader.LoadResult]:
    br = BranchResolver()
    res = loader.LoadResult("quality_events")
    rows: list[dict] = []
    canc = _table(path, "Cancelaciones", "TIENDA ID")
    rec_o = _table(path, "Reclamos - Órdenes", "ORDEN ID")
    rec_p = _table(path, "Reclamos - Productos", "ORDEN ID")
    res.rows_read = len(canc) + len(rec_o) + len(rec_p)
    _qe(rows, "cancelacion", canc, path, run_id, br, res, "DESCRIPCIÓN", product_col="NOMBRE DEL PRODUCTO")
    comp_col = next((c for c in rec_o.columns if c.upper().startswith("COMPENSACIÓN AL CL")), None)
    detail_col = next((c for c in rec_o.columns if c.upper().startswith("DETALLE DEL MOTIVO")), None)
    _qe(rows, "reclamo", rec_o, path, run_id, br, res, "MOTIVO", detail_col=detail_col, amount_col=comp_col)
    _qe(rows, "reclamo_producto", rec_p, path, run_id, br, res, "MOTIVO",
        detail_col=next((c for c in rec_p.columns if c.upper().startswith("DETALLE DEL MOTIVO")), None),
        product_col="PRODUCTO")
    res.inserted, res.updated = db.upsert(db.quality_events, rows)
    results = [res]

    # Disponibilidad: minutos sin disponibilidad por tienda y día (el portal solo trae los últimos 15 días)
    disp = _table(path, "Disponibilidad de tiendas", "TIENDA ID")
    if not disp.empty:
        dur_col = disp.columns[-1]
        disp["min"] = disp[dur_col].astype(str).str.extract(r"(\d+)\s*m")[0].astype(float)
        disp["h"] = disp[dur_col].astype(str).str.extract(r"(\d+)\s*h")[0].astype(float).fillna(0)
        disp["min"] = disp["min"].fillna(0) + disp["h"] * 60
        sd = disp.groupby([disp["TIENDA ID"].astype(str).str.strip(), disp["FECHA"].astype(str).str[:10]])["min"] \
            .sum().reset_index()
        sd.columns = ["store", "date", "offline_minutes"]
        sd["date"] = pd.to_datetime(sd["date"]).dt.date
        r2 = loader.LoadResult("store_day", rows_read=len(sd))
        b2 = BranchResolver()
        out = []
        for r in sd.itertuples():
            b = b2.resolve(P, r.store)
            if b:
                out.append({"platform_id": P, "branch_id": b, "business_date": r.date,
                            "offline_minutes": float(r.offline_minutes), "source": path.name, "run_id": run_id})
        r2.inserted, r2.updated = db.upsert(db.store_day, out)
        r2.issue("info", "disponibilidad_parcial",
                 "Rappi solo entrega el detalle de indisponibilidad de los últimos 15 días; días sin fila = 0 min "
                 "fuera de línea solo dentro de esa ventana.", P)
        results.append(r2)

    # Tiempos por pedido
    wt = _table(path, "Tiempo de espera del repartidor", "ID de la tienda")
    if not wt.empty:
        cols = list(wt.columns)
        r3 = loader.LoadResult("order_ops", rows_read=len(wt))
        out = []
        for _, r in wt.iterrows():
            b = br.resolve(P, str(r[cols[0]]).strip())
            d = pd.to_datetime(r[cols[3]], errors="coerce")
            if not b or pd.isna(d):
                r3.rejected += 1
                continue
            f = lambda v: float(pd.to_numeric(v, errors="coerce")) if pd.notna(pd.to_numeric(v, errors="coerce")) else None  # noqa: E731
            out.append({"platform_id": P, "order_id": str(r[cols[2]]).strip(), "branch_id": b,
                        "business_date": d.date(), "prep_expected_min": f(r[cols[4]]),
                        "courier_wait_min": f(r[cols[5]]), "prep_real_min": f(r[cols[6]]),
                        "total_min": f(r[cols[7]]) if len(cols) > 7 else None,
                        "source": path.name, "run_id": run_id})
        r3.inserted, r3.updated = db.upsert(db.order_ops, out)
        results.append(r3)
    return results


# ------------------------------------------------------------------ reseñas
def load_reviews(path: Path, run_id: str) -> list[loader.LoadResult]:
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    res = loader.LoadResult("reviews", rows_read=len(df))
    br = BranchResolver()
    out = []
    for _, r in df.iterrows():
        b = br.resolve(P, str(r["ID Tienda"]).strip()) or br.resolve(P, r["Tienda"])
        ts = pd.to_datetime(r["Fecha de creación"], format="%d/%m/%Y - %H:%M", errors="coerce")
        if not b or pd.isna(ts):
            res.rejected += 1
            continue
        out.append({"platform_id": P, "review_id": f"{r['ID Orden']}", "order_id": str(r["ID Orden"]),
                    "branch_id": b, "business_date": ts.date(), "rating": float(r["Calificación"]),
                    "reason": (r.get("Razón") or None), "comment": None, "is_demo": False,
                    "source": path.name, "run_id": run_id})
    res.inserted, res.updated = db.upsert(db.reviews, out)
    return [res]


PARSERS = [
    ("rappi_relacion_ventas", is_relacion_ventas, load_relacion_ventas),
    ("rappi_nivel_servicio", is_service_level, load_service_level),
    ("rappi_resenas", is_reviews, load_reviews),
]
