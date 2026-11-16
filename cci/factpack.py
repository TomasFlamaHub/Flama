"""Paquete de hechos del día: todos los números ya calculados, listos para el informe y la IA.

La IA NUNCA calcula ni inventa cifras: recibe este paquete (y herramientas de consulta)
y solo redacta/interpreta. Cada bloque indica si es REAL, ESTIMADO o PROYECCIÓN.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from . import config, db, kpis


def _clean(v):
    if isinstance(v, float):
        return None if pd.isna(v) else round(v, 4)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, (date, pd.Timestamp)):
        return v.isoformat()
    return v


def _small(s: dict) -> dict:
    keep = ["sales", "gross", "net", "payout", "orders", "ticket", "sessions", "conversion", "open_minutes",
            "availability", "unavailable_minutes", "adj_count", "adj_amount", "adj_rate", "cancelled_orders",
            "cancel_rate", "cancelled_amount", "payout_estimated"]
    return {k: s.get(k) for k in keep}


def build(D: date, brand: str | None = None) -> dict:
    """Paquete de hechos de una marca (o de todas si brand es None)."""
    P = kpis.Periods(D)
    s, e = P.load_range
    panel = kpis.load_panel(s, e, brand=brand)
    BR = config.branches_of(brand) if brand else None
    bn, pn = config.branch_names(), config.platform_names()
    panel["branch"] = panel["branch_id"].map(bn)
    panel["platform"] = panel["platform_id"].map(pn)
    day = lambda d: kpis.summarize(kpis.filt(panel, d, d))
    from datetime import timedelta
    D7 = D - timedelta(days=7)

    total = {
        "REAL_ayer": _small(day(D)),
        "REAL_dia_anterior": _small(day(D - timedelta(days=1))),
        "REAL_mismo_dia_semana_anterior": _small(day(D7)),
        "REAL_semana_acumulada": _small(kpis.summarize(kpis.filt(panel, *P.wtd))),
        "REAL_semana_anterior_misma_altura": _small(kpis.summarize(kpis.filt(panel, *P.wtd_prev))),
        "REAL_semana_anterior_completa": _small(kpis.summarize(kpis.filt(panel, *P.prev_week_full))),
        "REAL_mes_acumulado": _small(kpis.summarize(kpis.filt(panel, *P.mtd))),
        "REAL_mes_anterior_mismos_dias": _small(kpis.summarize(kpis.filt(panel, *P.prev_mtd))),
        "REAL_mes_anterior_completo": _small(kpis.summarize(kpis.filt(panel, *P.prev_month_full))),
    }
    rr_w = kpis.run_rate(panel, D, "week")
    rr_m = kpis.run_rate(panel, D, "month")
    proj_cols = ["sales_actual", "sales_proj", "orders_proj", "ticket_proj", "sessions_proj", "conversion_proj",
                 "adj_amount_proj", "adj_rate_proj", "base_sales", "vs_base_pct", "method", "weeks_history",
                 "days_elapsed", "days_total"]

    def tbl(df, cols):
        return [_clean({c: r.get(c) for c in cols}) for r in df.to_dict("records")]

    by_branch_day = kpis.entity_table(panel, (D, D), (D7, D7), "branch_id")
    by_branch_wtd = kpis.entity_table(panel, P.wtd, P.wtd_prev, "branch_id")
    by_platform_wtd = kpis.entity_table(panel, P.wtd, P.wtd_prev, "platform_id")
    for t in (by_branch_day, by_branch_wtd):
        t["branch"] = t["branch_id"].map(bn)
    by_platform_wtd["platform"] = by_platform_wtd["platform_id"].map(pn)

    # descomposición sesiones x conversión x ticket por sucursal x plataforma (semana vs semana)
    decomp = []
    for (b, p), sub in panel.groupby(["branch_id", "platform_id"]):
        c, base = kpis.summarize(kpis.filt(sub, *P.wtd)), kpis.summarize(kpis.filt(sub, *P.wtd_prev))
        dec = kpis.decompose(c, base)
        if dec:
            decomp.append({"branch": bn.get(b), "platform": pn.get(p), "delta_venta": dec["delta"],
                           "aporte_sesiones": dec["sesiones"], "aporte_conversion": dec["conversion"],
                           "aporte_ticket": dec["ticket"], "var_pct": dec["pct"]})
    decomp.sort(key=lambda x: x["delta_venta"])

    mixc = kpis.mix_compare(kpis.product_mix(*P.wtd, BR), kpis.product_mix(*P.wtd_prev, BR))
    movers = mixc[(mixc["revenue_base"] > 0)].copy()
    movers = pd.concat([movers.nsmallest(5, "delta_revenue"), movers.nlargest(5, "delta_revenue")])
    div = kpis.branch_mix_divergence(kpis.product_mix(*P.wtd, BR))
    if not div.empty:
        div["branch_id"] = div["branch_id"].map(bn)

    alerts = db.read_df("SELECT level, brand_id, title, what, magnitude, comparison, recommendation FROM alert "
                        "WHERE business_date = :d", {"d": D.isoformat()})
    if brand:
        alerts = alerts[alerts["brand_id"] == brand]
    dq = db.read_df("SELECT severity, check_name, detail FROM dq_issue WHERE business_date = :d "
                    "AND severity IN ('error','warning')", {"d": D.isoformat()})

    return _clean({
        "fecha_analizada": D,
        "marca": config.brands().get(brand, {}).get("name") if brand else "Todas las marcas",
        "sucursales_de_la_marca": [bn[b] for b in (BR or list(bn))],
        "plataformas_de_la_marca": [pn[p] for p in (config.brands()[brand]["platforms"] if brand else pn)],
        "es_demo": kpis.is_demo_data(),
        "moneda": config.settings()["business"]["currency"],
        "venta_principal": "venta bruta" if kpis.sales_col() == "gross" else "venta neta",
        "periodos": {"semana_actual": P.wtd, "semana_anterior_misma_altura": P.wtd_prev,
                     "mes_actual": P.mtd, "mes_anterior_mismos_dias": P.prev_mtd},
        "consolidado": total,
        "PROYECCION_semana": tbl(rr_w, proj_cols),
        "PROYECCION_mes": tbl(rr_m, proj_cols),
        "PROYECCION_semana_por_sucursal": tbl(kpis.run_rate(panel, D, "week", "branch_id").assign(
            group=lambda x: x["group"].map(bn)), ["group"] + proj_cols),
        "PROYECCION_semana_por_plataforma": tbl(kpis.run_rate(panel, D, "week", "platform_id").assign(
            group=lambda x: x["group"].map(pn)), ["group"] + proj_cols),
        "PROYECCION_mes_por_sucursal": tbl(kpis.run_rate(panel, D, "month", "branch_id").assign(
            group=lambda x: x["group"].map(bn)), ["group"] + proj_cols),
        "sucursales_ayer_vs_mismo_dia_sem_anterior": tbl(by_branch_day, list(by_branch_day.columns)),
        "sucursales_semana_vs_semana_anterior": tbl(by_branch_wtd, list(by_branch_wtd.columns)),
        "plataformas_semana_vs_semana_anterior": tbl(by_platform_wtd, list(by_platform_wtd.columns)),
        "descomposicion_semana_sesiones_conversion_ticket": decomp[:8] + decomp[-4:],
        "mix_productos_mayores_cambios_semana": tbl(movers, ["product", "units", "revenue", "share", "var_units",
                                                             "share_pp", "delta_revenue"]),
        "mix_sucursales_mas_distintas": tbl(div, list(div.columns)) if not div.empty else [],
        "alertas": alerts.to_dict("records"),
        "calidad_de_datos": dq.to_dict("records"),
        "definiciones_sesiones": {k: v.get("sessions_definition") for k, v in config.settings()["platforms"].items()},
    })
