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
    return path.read_bytes()[:400].decode("utf-8-sig", "ignore") if path.suffix.lower() == ".csv" else ""


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
