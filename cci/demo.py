"""Generador de DATOS DE DEMOSTRACIÓN (sintéticos).

Sirve solo para ver el sistema funcionando antes de conectar las fuentes reales.
Todas las filas quedan con is_demo = true y el dashboard muestra un aviso permanente.
Se borran con:  python -m cci.cli demo --purge
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import config, db
from .ingest import loader

PRODUCTS = [  # (nombre, categoría, precio CLP, peso de popularidad)
    ("Hamburguesa Clásica", "Hamburguesas", 7990, 10), ("Hamburguesa Doble", "Hamburguesas", 9990, 7),
    ("Hamburguesa Pollo Crispy", "Hamburguesas", 8490, 5), ("Hamburguesa Veggie", "Hamburguesas", 8290, 2),
    ("Combo Clásico + Papas + Bebida", "Combos", 10990, 9), ("Combo Doble + Papas + Bebida", "Combos", 12990, 6),
    ("Papas Fritas", "Acompañamientos", 2990, 8), ("Papas Cheddar Bacon", "Acompañamientos", 4490, 4),
    ("Aros de Cebolla", "Acompañamientos", 3490, 3), ("Nuggets x8", "Acompañamientos", 4290, 4),
    ("Bebida 500ml", "Bebidas", 1990, 9), ("Limonada", "Bebidas", 2790, 4),
    ("Brownie", "Postres", 2990, 3), ("Shake Vainilla", "Postres", 3990, 2),
]
PRODUCTS_CRUST = [
    ("Pizza Margherita", "Pizzas", 9990, 9), ("Pizza Pepperoni", "Pizzas", 11490, 10),
    ("Pizza Cuatro Quesos", "Pizzas", 11990, 5), ("Pizza Pollo BBQ", "Pizzas", 12490, 6),
    ("Pizza Vegetariana", "Pizzas", 10990, 3), ("Combo Pizza + Bebida", "Combos", 13490, 7),
    ("Palitos de Ajo", "Acompañamientos", 3990, 6), ("Alitas x8", "Acompañamientos", 6490, 4),
    ("Bebida 1,5L", "Bebidas", 2990, 6), ("Cookie", "Postres", 2490, 3),
]
DOW = [0.78, 0.82, 0.88, 0.97, 1.25, 1.42, 1.15]
BRANCH_SCALE = {"KOBO1": 1.25, "KOBO2": 1.0, "KOBO3": 0.95, "KOBO4": 0.85, "KOBO5": 1.1, "KOBO6": 0.7,
                "CRUST1": 1.3, "CRUST2": 1.0}
PLATFORM = {  # pedidos/día base, conversión, comisión demo
    "rappi": (16, 0.105, 0.24), "ubereats": (12, 0.095, 0.28), "justo": (7, 0.17, 0.05)}
REASONS = ["Producto faltante", "Pedido incorrecto", "Demora en entrega", "Producto en mal estado",
           "Pedido no entregado"]


def generate(end: date, days: int = 75, seed: int = 7) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    menus = {}
    for brand in config.brands():
        menu = PRODUCTS_CRUST if brand == "crust" else PRODUCTS
        menus[brand] = ([p[0] for p in menu], np.array([p[3] for p in menu], float), {p[0]: p[2] for p in menu})
    orders, items, adjs, ops = [], [], [], []
    start = end - timedelta(days=days - 1)
    for i in range(days):
        d = start + timedelta(days=i)
        trend = 1 + 0.0015 * i
        for b in [x["id"] for x in config.settings()["branches"]]:
            bs = BRANCH_SCALE.get(b, 1.0)
            brand = config.brand_of(b)
            names, weights, price = menus[brand]
            for p in config.brands()[brand]["platforms"]:
                base_n, conv, comm = PLATFORM[p]
                if brand == "crust":
                    base_n = base_n * 1.4
                sched = 720.0
                open_m = sched - max(0, rng.normal(10, 12))
                conv_d = conv * rng.normal(1, 0.06)
                sess = base_n * bs * DOW[d.weekday()] * trend / conv * rng.normal(1, 0.08)
                ticket_mult = 1.0
                refund_rate = 0.015
                local_w = weights.copy()
                # ---- escenarios sintéticos para que las alertas tengan algo que mostrar
                if b == "KOBO3" and p == "rappi" and d > end - timedelta(days=7):
                    sess *= 0.72                                      # caída de tráfico en Rappi
                if b == "KOBO5" and d == end:
                    refund_rate = 0.09                                # pico de resarcimientos
                if b == "KOBO2" and p == "ubereats" and d == end:
                    open_m = 470.0                                    # tienda pausada ~4 h
                if b == "KOBO4" and d == end:
                    local_w[names.index("Combo Clásico + Papas + Bebida")] = 0
                if b == "CRUST2" and d > end - timedelta(days=5):
                    sess *= 0.7                                       # The Crust 2: caída de tráfico  # producto que dejó de venderse
                if b == "KOBO6" and p == "justo" and d > end - timedelta(days=10):
                    conv_d *= 1.25                                    # mejora de conversión en Justo
                n = int(rng.poisson(max(sess * conv_d * open_m / sched, 0.1)))
                ops.append({"platform": p, "store": b, "date": d, "scheduled_minutes": sched,
                            "open_minutes": round(open_m), "sessions": round(sess)})
                probs = local_w / local_w.sum()
                for k in range(n):
                    oid = f"DEMO-{p[:2].upper()}-{d:%y%m%d}-{b}-{k:04d}"
                    ts = datetime.combine(d, datetime.min.time()) + timedelta(
                        hours=float(rng.choice([12, 13, 14, 19, 20, 21, 22, 23], p=[.12, .16, .1, .1, .16, .18, .12, .06])),
                        minutes=int(rng.integers(0, 60)))
                    lines = rng.choice(len(names), size=int(rng.integers(1, 4)), p=probs)
                    gross = 0.0
                    for ln, idx in enumerate(lines, start=1):
                        q = int(rng.choice([1, 1, 1, 2]))
                        amt = price[names[idx]] * q * ticket_mult
                        gross += amt
                        items.append({"platform": p, "order_id": oid, "line_no": ln, "product": names[idx],
                                      "quantity": q, "gross_amount": amt})
                    status = "cancelled" if rng.random() < 0.02 else "completed"
                    disc = round(gross * 0.1) if rng.random() < 0.15 else 0.0
                    c = round(gross * comm)
                    orders.append({"platform": p, "order_id": oid, "store": b, "created_at": ts, "status": status,
                                   "gross_amount": gross, "discount_merchant": disc, "discount_platform": 0.0,
                                   "commission": c, "other_fees": 0.0, "payout_amount": gross - disc - c,
                                   "cancel_reason": "Sin stock" if status == "cancelled" else None})
                    if status == "completed" and p != "justo" and (rng.random() < refund_rate or (refund_rate > 0.05 and k < 3)):
                        adjs.append({"platform": p, "store": b, "date": d, "order_id": oid,
                                     "amount": round(gross * rng.choice([0.3, 0.5, 1.0])), "kind": "resarcimiento",
                                     "reason": str(rng.choice(REASONS)), "adjustment_id": f"ADJ-{oid}"})
    return {"orders": pd.DataFrame(orders), "order_items": pd.DataFrame(items),
            "adjustments": pd.DataFrame(adjs), "store_day": pd.DataFrame(ops)}


def load(end: date, days: int = 75) -> dict:
    data = generate(end, days)
    run_id, started = "demo-" + uuid.uuid4().hex[:10], db.now_utc()
    results = []
    for ds in ["orders", "order_items", "adjustments", "store_day"]:
        df = data[ds]
        for p, part in df.groupby("platform"):
            results.append(loader.LOADERS[ds](part.drop(columns="platform"), p, run_id, "DEMO", is_demo=True))
    loader.record_run(run_id, started, None, "demo", "datos sintéticos", results)
    for name, cat, *_ in PRODUCTS + PRODUCTS_CRUST:
        db.execute("UPDATE product SET category = :c WHERE name = :n", {"c": cat, "n": name})
    return {ds: len(df) for ds, df in data.items()}


def purge() -> None:
    for t in ["order_items", "orders", "adjustments", "store_day", "alert", "ai_report"]:
        db.execute(f"DELETE FROM {t} WHERE is_demo")
    db.execute("DELETE FROM ingestion_run WHERE source_type = 'demo'")
    db.execute("DELETE FROM dq_issue WHERE run_id LIKE 'demo-%' OR run_id LIKE 'quality-%'")
