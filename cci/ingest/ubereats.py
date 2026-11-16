"""Lectores de reportes de Uber Eats Manager.

Verificado con archivos reales (15-11-2026):
  * Desempeño › Ventas › Embudo de conversión (CSV 'user-conversion_*'): período, tienda visualizada,
    vieron el menú, agregaron al carrito, pedidos realizados, tienda seleccionada.  -> funnel_period

Pendiente de archivo real: reportes de la sección Informes (historial de pedidos, detalles de
ganancias, tiempo de inactividad, pausas, pedidos con errores, reseñas). Se agregan aquí al recibirlos.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import config, db
from . import loader
from .normalize import BranchResolver

P = "ubereats"


def is_funnel(path: Path) -> bool:
    if path.suffix.lower() != ".csv":
        return False
    head = path.read_bytes()[:400].decode("utf-8-sig", "ignore")
    return "Tienda visualizada" in head and "Vieron el men" in head


def load_funnel(path: Path, run_id: str) -> list[loader.LoadResult]:
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
    res = loader.LoadResult("funnel_period", rows_read=len(df))
    br = BranchResolver()
    # la tienda viene solo en la primera fila ("Este periodo"); "Último periodo" es el período anterior
    stores_txt = str(df["Tienda seleccionada"].dropna().iloc[0]) if df["Tienda seleccionada"].notna().any() else ""
    names = [s.strip() for s in stores_txt.splitlines() if s.strip()]
    if len(names) == 1:
        branch = br.resolve(P, names[0])
    else:
        branches = {br.resolve(P, n) for n in names}
        brands = {config.brand_of(b) for b in branches if b}
        branch = f"ALL_{brands.pop()}" if len(brands) == 1 else None
    if not branch:
        res.issue("error", "sucursal_desconocida", f"Embudo con tiendas no mapeadas: {names}", P)
        res.rejected = len(df)
        return [res]
    out = []
    for _, r in df.iterrows():
        f = lambda c: float(pd.to_numeric(r.get(c), errors="coerce"))  # noqa: E731
        out.append({"platform_id": P, "branch_id": branch,
                    "start_date": pd.to_datetime(r["Fecha de inicio"]).date(),
                    "end_date": pd.to_datetime(r["Fecha de finalización"]).date(),
                    "store_views": f("Tienda visualizada"), "menu_views": f("Vieron el menú"),
                    "add_to_cart": f("Agregaron artículo al pedido"), "orders": f("Pedidos realizados"),
                    "is_demo": False, "source": path.name, "run_id": run_id})
    res.inserted, res.updated = db.upsert(db.funnel_period, out)
    return [res]


PARSERS = [("ubereats_embudo", is_funnel, load_funnel)]


# ---------------------------------------------------------------- resúmenes del panel "Desempeño › Ventas"
import re as _re
from datetime import date as _date, timedelta as _td

DOW = {"lun": 0, "mar": 1, "mié": 2, "mie": 2, "jue": 3, "vie": 4, "sáb": 5, "sab": 5, "dom": 6}


def _head(path: Path) -> str:
    if path.suffix.lower() != ".csv":
        return ""
    return path.read_bytes()[:600].decode("utf-8-sig", "ignore").replace("\xa0", " ").lstrip("\ufeff")


def _period_from_name(path: Path):
    m = _re.search(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", path.name)
    return (_date.fromisoformat(m.group(1)), _date.fromisoformat(m.group(2))) if m else (None, None)


def _all_branch(brand="kobo"):
    return f"ALL_{brand}"


def is_store_leaderboard(path: Path) -> bool:
    return _head(path).startswith("Tienda,Id. del negocio,Dirección,Ventas")


def load_store_leaderboard(path: Path, run_id: str):
    df = pd.read_csv(path, encoding="utf-8-sig")
    res = loader.LoadResult("period_summary", rows_read=len(df))
    s, e = _period_from_name(path)
    br = BranchResolver()
    out = []
    for _, r in df.iterrows():
        b = br.resolve(P, r["Tienda"])
        if not b or s is None:
            res.rejected += 1
            continue
        out.append({"platform_id": P, "branch_id": b, "start_date": s, "end_date": e, "sales": float(r["Ventas"]),
                    "orders": float(r["Pedidos"]), "items": float(r["Artículos vendidos"]),
                    "sales_delta_pct": pd.to_numeric(r.get("Ventas %Δ"), errors="coerce"),
                    "source": path.name, "run_id": run_id})
    res.inserted, res.updated = db.upsert(db.period_summary, out)
    return [res]


def is_weekly_sales(path: Path) -> bool:
    h = _head(path)
    return "Incremento de tiempo" in h and "Ventas para entregar" in h


def load_weekly_sales(path: Path, run_id: str):
    """El CSV no dice de qué tienda es: se identifica cruzando el total del período con el ranking de tiendas."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    res = loader.LoadResult("weekly_summary", rows_read=len(df))
    cur = df[df["Periodo"] == "Este periodo"]
    tot_sales, tot_orders = float(cur["Ventas"].sum()), float(cur["Pedidos"].sum())
    ps = db.read_df("SELECT branch_id, SUM(sales) AS s, SUM(orders) AS o FROM period_summary "
                    "WHERE platform_id='ubereats' GROUP BY branch_id, start_date, end_date")
    branch = None
    for r in ps.itertuples():
        if abs(r.s - tot_sales) < 1 and abs(r.o - tot_orders) < 1:
            branch = r.branch_id
    if branch is None and not ps.empty:
        g = db.read_df("SELECT start_date, SUM(sales) s, SUM(orders) o FROM period_summary "
                       "WHERE platform_id='ubereats' AND branch_id NOT LIKE 'ALL_%' GROUP BY start_date, end_date")
        if any(abs(x.s - tot_sales) < 1 and abs(x.o - tot_orders) < 1 for x in g.itertuples()):
            branch = _all_branch()
    if branch is None:
        res.rejected = len(df)
        res.issue("error", "tienda_no_identificada",
                  f"{path.name}: no se pudo identificar la tienda (cargar primero el ranking de tiendas del mismo "
                  "período, o descargar el archivo con la tienda en el nombre).", P)
        return [res]
    out = [{"platform_id": P, "branch_id": branch, "week_start": pd.to_datetime(r["Fecha de inicio"]).date(),
            "sales": float(r["Ventas"]), "orders": float(r["Pedidos"]),
            "sales_delivery": float(r["Ventas para entregar"]), "sales_pickup": float(r["Ventas para recolectar"]),
            "source": path.name, "run_id": run_id} for _, r in df.iterrows()]
    res.inserted, res.updated = db.upsert(db.weekly_summary, out)
    return [res]


def is_item_leaderboard(path: Path) -> bool:
    return _head(path).startswith("Artículo,Ventas,Ventas %Δ,Artículos vendidos")


def load_item_leaderboard(path: Path, run_id: str):
    df = pd.read_csv(path, encoding="utf-8-sig")
    res = loader.LoadResult("product_period", rows_read=len(df))
    s, e = _period_from_name(path)
    if s is None:
        res.rejected = len(df)
        res.issue("error", "periodo_desconocido", f"{path.name}: el nombre no trae las fechas del período", P)
        return [res]
    out = [{"platform_id": P, "branch_id": _all_branch(), "start_date": s, "end_date": e,
            "product": str(r["Artículo"]).strip(), "sales": float(r["Ventas"]), "units": float(r["Artículos vendidos"]),
            "sales_delta_pct": pd.to_numeric(r.get("Ventas %Δ"), errors="coerce"),
            "units_delta_pct": pd.to_numeric(r.get("Artículos vendidos %Δ"), errors="coerce"),
            "source": path.name, "run_id": run_id} for _, r in df.iterrows()]
    res.inserted, res.updated = db.upsert(db.product_period, out)
    res.issue("info", "ranking_sin_tienda", "El ranking de artículos descargado corresponde a todas las tiendas "
              "seleccionadas en el portal (se asume las 6 de Kobo).", P)
    return [res]


def is_hourly(path: Path) -> bool:
    return _head(path).startswith("Día,Hora,Ventas,% de ventas totales")


def load_hourly(path: Path, run_id: str):
    df = pd.read_csv(path, encoding="utf-8-sig")
    res = loader.LoadResult("hourly_period", rows_read=len(df))
    s, e = _period_from_name(path)
    out = []
    for _, r in df.iterrows():
        d = DOW.get(str(r["Día"]).strip().lower().rstrip("."))
        if d is None or s is None:
            res.rejected += 1
            continue
        out.append({"platform_id": P, "branch_id": _all_branch(), "start_date": s, "end_date": e, "dow": d,
                    "hour": int(r["Hora"]), "sales": float(r["Ventas"]), "orders": float(r["Pedidos"]),
                    "source": path.name, "run_id": run_id})
    res.inserted, res.updated = db.upsert(db.hourly_period, out)
    return [res]


# el ranking de tiendas va primero: lo usa el lector semanal para identificar la tienda
PARSERS = [("ubereats_ranking_tiendas", is_store_leaderboard, load_store_leaderboard),
           ("ubereats_ventas_semanales", is_weekly_sales, load_weekly_sales),
           ("ubereats_ranking_articulos", is_item_leaderboard, load_item_leaderboard),
           ("ubereats_ventas_por_hora", is_hourly, load_hourly)] + PARSERS


# ---------------------------------------------------------------- Informes (detalle por pedido)
import hashlib as _hl
import unicodedata as _ud


def _clean_cols(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [_ud.normalize("NFKC", str(c)).replace("\xa0", " ").strip() for c in df.columns]
    return df


def _n(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def is_order_history(path: Path) -> bool:
    h = _head(path)
    return h.startswith("Restaurante,Código externo del restaurante,UUID de la tienda") and "Código del pedido" in h


UBER_STATUS = {"completed": "completed", "canceled": "cancelled", "failed": "rejected", "unfulfilled": "rejected"}


def load_order_history(path: Path, run_id: str):
    """Historial de pedidos: estado, valor, cancelaciones y tiempos operativos de cada pedido."""
    df = _clean_cols(pd.read_csv(path, dtype=str, encoding="utf-8-sig"))
    wf = "Identificador universal (UUID) del flujo de trabajo"
    status = df["Estado del pedido"].str.strip().str.lower()
    orders = pd.DataFrame({
        "order_id": df[wf].str.strip(),
        "store": df["UUID de la tienda"].str.strip(),
        "created_at": pd.to_datetime(df["Hora del pedido del cliente"], errors="coerce"),
        "status": status.map(lambda s: UBER_STATUS.get(s, f"?{s}")),
        "gross_amount": _n(df["Valor del recibo"]),
        "cancel_reason": df["Cancelado por"].where(df["Cancelado por"].notna(), None),
    })
    results = [loader.load_orders(orders, P, run_id, path.name)]
    br = BranchResolver()
    res = loader.LoadResult("order_ops", rows_read=len(df))
    f = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df.columns else pd.Series(float("nan"), index=df.index)  # noqa: E731
    ops = pd.DataFrame({
        "order_id": orders["order_id"], "store": orders["store"], "ts": orders["created_at"],
        "prep_expected_min": f("Tiempo original de preparación"),
        "courier_wait_min": f("Tiempo de espera del repartidor (restaurante)"),
        "avoidable_wait_min": f("Tiempo de espera evitable del socio de la App (restaurante)"),
        "accept_min": f("Tiempo hasta que se aceptó el pedido"),
        "delivery_min": f("Tiempo total de entrega"),
        "total_min": f("Tiempo total de preparación y de entrega"),
    })
    from .normalize import business_date
    ops = ops[ops["ts"].notna()]
    ops["business_date"] = business_date(ops["ts"])
    out = []
    for r in ops.itertuples():
        b = br.resolve(P, r.store)
        if not b:
            res.rejected += 1
            continue
        clean = lambda v: None if pd.isna(v) else float(v)  # noqa: E731
        out.append({"platform_id": P, "order_id": r.order_id, "branch_id": b, "business_date": r.business_date,
                    "prep_expected_min": clean(r.prep_expected_min), "courier_wait_min": clean(r.courier_wait_min),
                    "avoidable_wait_min": clean(r.avoidable_wait_min), "accept_min": clean(r.accept_min),
                    "delivery_min": clean(r.delivery_min), "total_min": clean(r.total_min),
                    "source": path.name, "run_id": run_id})
    res.inserted, res.updated = db.upsert(db.order_ops, out)
    results.append(res)
    return results


def is_downtime(path: Path) -> bool:
    h = _head(path)
    return "Menú disponible" in h and "Restaurante desconectado" in h


def load_downtime(path: Path, run_id: str):
    """Tiempo de inactividad (por hora) -> minutos programados, conectados y desconectados por día."""
    df = _clean_cols(pd.read_csv(path, dtype=str, encoding="utf-8-sig"))
    for c in ["Menú disponible", "Restaurante conectado", "Restaurante desconectado"]:
        df[c] = _n(df[c])
    g = df.groupby(["UUID de la tienda", "Fecha"], as_index=False)[
        ["Menú disponible", "Restaurante conectado", "Restaurante desconectado"]].sum()
    sd = pd.DataFrame({"store": g["UUID de la tienda"], "date": pd.to_datetime(g["Fecha"]).dt.date,
                       "scheduled_minutes": g["Menú disponible"], "open_minutes": g["Restaurante conectado"],
                       "offline_minutes": g["Restaurante desconectado"]})
    sd = sd[sd["scheduled_minutes"] > 0]
    return [loader.load_store_day(sd, P, run_id, path.name)]


def is_earnings_items(path: Path) -> bool:
    h = _head(path)
    return h.startswith("Nombre de la tienda,ID externo de la tienda,UUID de la tienda,Id. del pedido") and \
        "Nombre del artículo" in h


def load_earnings_items(path: Path, run_id: str):
    """Detalles de las ganancias (nivel de artículo).

    Filas de pedido (sin artículo): venta, promociones, tasa de servicio, cargos por error y pago total.
    Filas de artículo: producto, cantidad y venta (no llevan montos financieros).
    Filas sin pedido: gasto en anuncios, otras tarifas y ajustes.
    """
    df = _clean_cols(pd.read_csv(path, dtype=str, encoding="utf-8-sig"))
    money = ["Ventas (con IVA)", "Cargo por error en el pedido (sin IVA)", "IVA sobre el cargo por error en el pedido",
             "Ajustes de precios (con IVA)", "Promociones en artículos (con IVA)", "Tarifa por canje de la oferta",
             "Impuesto sobre la tarifa de canje de la oferta", "Canjes de oferta de entrega (con IVA)",
             "Tasa de servicio (con IVA)", "Tarifa de solicitud (con IVA)", "Otras ganancias", "Pago total"]
    for c in money:
        df[c] = _n(df[c]) if c in df.columns else 0.0
    wf = df["Id. del flujo de trabajo"].astype(str).str.strip()
    df["wf"] = wf
    is_item = df["Nombre del artículo"].notna()
    is_order = ~is_item & df["Id. del pedido"].notna()
    is_other = ~is_item & df["Id. del pedido"].isna()
    store_of = df[is_order].drop_duplicates("wf").set_index("wf")["UUID de la tienda"]
    results = []

    # --- pedidos completados con sus montos
    comp = df[is_order & (df["Estado del pedido"].str.strip() == "Completado")].drop_duplicates("wf", keep="last")
    ts = pd.to_datetime(comp["Fecha del pedido"] + " " + comp["Hora a la que se aceptó el pedido"].fillna("12:00"),
                        format="%d/%m/%y %H:%M", errors="coerce")
    fin = pd.DataFrame({
        "order_id": comp["wf"], "store": comp["UUID de la tienda"], "created_at": ts, "status": "completed",
        "gross_amount": comp["Ventas (con IVA)"],
        "discount_merchant": -(comp["Promociones en artículos (con IVA)"] + comp["Ajustes de precios (con IVA)"]),
        "discount_platform": 0.0,
        "commission": -comp["Tasa de servicio (con IVA)"],
        "other_fees": -(comp["Tarifa por canje de la oferta"] + comp["Impuesto sobre la tarifa de canje de la oferta"]
                        + comp["Tarifa de solicitud (con IVA)"] + comp["Canjes de oferta de entrega (con IVA)"]),
        "payout_amount": comp["Pago total"],
    })
    # el historial de pedidos (si ya está cargado) manda en hora/estado: aquí solo se completan montos
    existing = set(db.read_df("SELECT order_id FROM orders WHERE platform_id='ubereats'")["order_id"])
    new = fin[~fin["order_id"].isin(existing)]
    upd = fin[fin["order_id"].isin(existing)]
    if not new.empty:
        results.append(loader.load_orders(new, P, run_id, path.name))
    if not upd.empty:
        r = loader.LoadResult("orders_finanzas", rows_read=len(upd))
        from sqlalchemy import text
        with db.engine().begin() as conn:
            conn.execute(text("""UPDATE orders SET discount_merchant=:dm, discount_platform=0, commission=:cm,
                                 other_fees=:of, payout_amount=:pa, payout_is_estimated=:est
                                 WHERE platform_id='ubereats' AND order_id=:oid"""),
                         [{"dm": float(x.discount_merchant), "cm": float(x.commission), "of": float(x.other_fees),
                           "pa": float(x.payout_amount), "est": False, "oid": x.order_id} for x in upd.itertuples()])
        r.updated = len(upd)
        results.append(r)

    # --- reembolsos por error en el pedido (resarcimientos): neto de disputas ganadas
    ref = df[is_order & df["Estado del pedido"].str.contains("Reembolso", na=False)].copy()
    if not ref.empty:
        ref["monto"] = -(ref["Cargo por error en el pedido (sin IVA)"] + ref["IVA sobre el cargo por error en el pedido"])
        g = ref.groupby("wf").agg(monto=("monto", "sum"), fecha=("Fecha del pedido", "first"),
                                  store=("UUID de la tienda", "first"),
                                  estados=("Estado del pedido", lambda s: ", ".join(sorted(set(s))))).reset_index()
        g = g[g["monto"].abs() > 0.5]
        adj = pd.DataFrame({"adjustment_id": "err-" + g["wf"], "order_id": g["wf"], "store": g["store"],
                            "date": pd.to_datetime(g["fecha"], format="%d/%m/%y").dt.date, "amount": g["monto"],
                            "kind": "reembolso_error", "reason": g["estados"]})
        results.append(loader.load_adjustments(adj, P, run_id, path.name))

    # --- artículos
    it = df[is_item].copy()
    if not it.empty:
        it["line_no"] = it.groupby("wf").cumcount() + 1
        items = pd.DataFrame({"order_id": it["wf"], "line_no": it["line_no"], "product": it["Nombre del artículo"],
                              "quantity": _n(it["Cantidad final"]).where(_n(it["Cantidad final"]) > 0,
                                                                         _n(it["Cantidad solicitada"])),
                              "gross_amount": it["Ventas (con IVA)"]})
        results.append(loader.load_order_items(items, P, run_id, path.name))

    # --- anuncios y otros cargos sin pedido
    oth = df[is_other & df["UUID de la tienda"].notna()]
    if not oth.empty:
        res = loader.LoadResult("costs", rows_read=len(oth))
        br = BranchResolver()
        rows = []
        for _, r in oth.iterrows():
            b = br.resolve(P, str(r["UUID de la tienda"]).strip())
            d = pd.to_datetime(r["Fecha del pedido"], format="%d/%m/%y", errors="coerce")
            if not b or pd.isna(d):
                res.rejected += 1
                continue
            desc = str(r.get("Descripción de otros pagos") or "")
            amt = -float(r["Otras ganancias"])
            kind = "ads" if "anuncio" in desc.lower() else "otro"
            key = f"{b}|{d.date()}|{desc}|{amt}|{r.get('Id. de referencia de ganancias')}|{r.get('Fecha de pago')}"
            rows.append({"platform_id": P, "cost_id": _hl.sha1(key.encode()).hexdigest()[:24], "branch_id": b,
                         "business_date": d.date(), "kind": kind, "amount": amt, "description": desc,
                         "is_demo": False, "source": path.name, "run_id": run_id})
        res.inserted, res.updated = db.upsert(db.costs, rows)
        results.append(res)
    return results


# historial antes que ganancias (las ganancias completan montos de pedidos ya cargados)
PARSERS = PARSERS[:1] + [("ubereats_historial_pedidos", is_order_history, load_order_history),
                         ("ubereats_ganancias_articulo", is_earnings_items, load_earnings_items),
                         ("ubereats_inactividad", is_downtime, load_downtime)] + PARSERS[1:]
