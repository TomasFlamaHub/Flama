"""Controles de calidad de datos posteriores a la carga (datos faltantes / inconsistentes)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from . import config, db, kpis


def run_checks(D: date) -> list[dict]:
    run_id = f"quality-{D.isoformat()}"
    db.execute("DELETE FROM dq_issue WHERE run_id = :r", {"r": run_id})
    issues: list[dict] = []

    def add(sev, check, detail, platform=None, branch=None):
        issues.append({"detected_at": db.now_utc(), "run_id": run_id, "severity": sev, "check_name": check,
                       "detail": detail, "platform_id": platform, "branch_id": branch, "business_date": D})

    names = config.branch_names()
    pnames = config.platform_names()
    panel = kpis.load_panel(D - timedelta(days=28), D)
    hist = panel[panel["business_date"] < D]
    today = panel[panel["business_date"] == D]

    # 1) Combinaciones sucursal x plataforma que normalmente venden y hoy no tienen datos
    if not hist.empty:
        avg = hist.groupby(["branch_id", "platform_id"])["orders"].sum() / 28
        for (b, p), a in avg.items():
            row = today[(today["branch_id"] == b) & (today["platform_id"] == p)]
            if a >= 3 and (row.empty or row["orders"].sum() == 0):
                add("warning", "sin_ordenes",
                    f"{names.get(b, b)} en {pnames.get(p, p)}: 0 órdenes el {D} (promedio {a:.1f}/día). "
                    "Puede faltar el archivo/API o el local estuvo cerrado.", p, b)
        # 2) Métricas operativas que suelen venir y faltan
        sd_hist = hist[hist["sessions"].notna()].groupby(["branch_id", "platform_id"]).size()
        for (b, p), n in sd_hist.items():
            row = today[(today["branch_id"] == b) & (today["platform_id"] == p)]
            if n >= 7 and (row.empty or row["sessions"].isna().all()):
                add("warning", "sin_metricas_operativas",
                    f"{names.get(b, b)} en {pnames.get(p, p)}: faltan sesiones/Open Time del {D}.", p, b)

    # 3) Inconsistencias
    for _, r in today.iterrows():
        if pd.notna(r.get("sessions")) and r["sessions"] > 0 and r["orders"] > r["sessions"]:
            add("warning", "conversion_mayor_100",
                f"{names.get(r.branch_id)} en {pnames.get(r.platform_id)}: más órdenes ({r.orders:.0f}) "
                f"que sesiones ({r.sessions:.0f}). Revisar definición de sesiones.", r.platform_id, r.branch_id)
    items = db.read_df("""
        SELECT o.platform_id, SUM(o.gross_amount) AS orders_total, SUM(i.items_total) AS items_total
        FROM orders o JOIN (SELECT platform_id, order_id, SUM(gross_amount) AS items_total
                            FROM order_items GROUP BY platform_id, order_id) i
          ON i.platform_id = o.platform_id AND i.order_id = o.order_id
        WHERE o.business_date = :d AND o.status = 'completed'
        GROUP BY o.platform_id""", {"d": D.isoformat()})
    for _, r in items.iterrows():
        if r.orders_total and abs(r.items_total / r.orders_total - 1) > 0.03:
            add("warning", "items_no_cuadran",
                f"{pnames.get(r.platform_id)}: la suma de productos ({r.items_total:,.0f}) difiere "
                f"{(r.items_total / r.orders_total - 1) * 100:+.1f}% de la venta de órdenes ({r.orders_total:,.0f}).",
                r.platform_id)

    # 4) Frescura de cada fuente
    runs = db.read_df("""SELECT platform_id, MAX(finished_at) AS last_ok FROM ingestion_run
                         WHERE status IN ('ok','warning') AND source_type <> 'demo' GROUP BY platform_id""")
    last = {r.platform_id: pd.to_datetime(r.last_ok) for r in runs.itertuples()}
    if not kpis.is_demo_data():
        for p in config.PLATFORMS:
            lo = last.get(p) or last.get("from_column")
            if lo is None or (pd.Timestamp(db.now_utc()) - lo) > pd.Timedelta(hours=36):
                add("error", "fuente_desactualizada",
                    f"{pnames.get(p)}: sin cargas exitosas en las últimas 36 h (última: {lo}).", p)

    db.insert_rows(db.dq_issue, issues)
    return issues
