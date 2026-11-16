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
