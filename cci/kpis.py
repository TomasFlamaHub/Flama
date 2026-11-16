"""Motor de KPIs: fórmulas, períodos comparables, run rate y descomposición de variaciones.

Todas las fórmulas están documentadas en docs/04_kpis_formulas.md. Convenciones:
  * "Día" = día comercial (respeta la hora de corte configurada).
  * Venta bruta  = suma de gross_amount de órdenes COMPLETADAS.
  * Venta neta   = venta bruta - descuentos financiados por el local.
  * Liquidación  = lo que paga la plataforma (real o ESTIMADA, ver payout_is_estimated).
  * Conversión   = órdenes / sesiones, calculada SOLO sobre filas sucursal x plataforma x día
                   que tienen sesiones informadas (si no, se mezclarían peras con manzanas).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config, db

KEYS = ["business_date", "branch_id", "platform_id"]
ADDITIVE = ["gross", "net", "payout", "commission", "orders", "cancelled_orders", "cancelled_amount",
            "adj_count", "adj_amount", "sessions", "orders_s", "sales_s", "open_minutes", "scheduled_minutes"]


# --------------------------------------------------------------------------- carga
def load_panel(start: date, end: date, branches: list[str] | None = None,
               platforms: list[str] | None = None, brand: str | None = None) -> pd.DataFrame:
    """Panel diario sucursal x plataforma con todas las métricas aditivas."""
    p = {"s": start.isoformat(), "e": end.isoformat()}
    o = db.read_df("""
        SELECT business_date, branch_id, platform_id,
               SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END)                         AS orders,
               SUM(CASE WHEN status='completed' THEN gross_amount ELSE 0 END)              AS gross,
               SUM(CASE WHEN status='completed' THEN gross_amount - COALESCE(discount_merchant,0) ELSE 0 END) AS net,
               SUM(CASE WHEN status='completed' THEN payout_amount END)                    AS payout,
               SUM(CASE WHEN status='completed' THEN commission END)                       AS commission,
               MAX(CASE WHEN payout_is_estimated THEN 1 ELSE 0 END)                        AS payout_estimated,
               SUM(CASE WHEN status<>'completed' THEN 1 ELSE 0 END)                        AS cancelled_orders,
               SUM(CASE WHEN status<>'completed' THEN gross_amount ELSE 0 END)             AS cancelled_amount,
               MAX(CASE WHEN is_demo THEN 1 ELSE 0 END)                                    AS is_demo
        FROM orders WHERE business_date BETWEEN :s AND :e
        GROUP BY business_date, branch_id, platform_id""", p)
    a = db.read_df("""
        SELECT business_date, branch_id, platform_id, COUNT(*) AS adj_count, SUM(amount) AS adj_amount
        FROM adjustments WHERE business_date BETWEEN :s AND :e
        GROUP BY business_date, branch_id, platform_id""", p)
    sd = db.read_df("""
        SELECT business_date, branch_id, platform_id, sessions, open_minutes, scheduled_minutes
        FROM store_day WHERE business_date BETWEEN :s AND :e""", p)
    frames = []
    for f in (o, a, sd):
        f["business_date"] = pd.to_datetime(f["business_date"]).dt.date
        frames.append(f.set_index(KEYS))
    panel = frames[0].join(frames[1], how="outer").join(frames[2], how="outer").reset_index()
    for c in ["orders", "gross", "net", "cancelled_orders", "cancelled_amount", "adj_count", "adj_amount",
              "payout_estimated", "is_demo"]:
        panel[c] = panel[c].fillna(0.0) if c in panel else 0.0
    has_s = panel["sessions"].notna() & (panel["sessions"] > 0)
    panel["orders_s"] = np.where(has_s, panel["orders"], 0.0)
    panel["sales_s"] = np.where(has_s, panel["gross"], 0.0)
    if brand:
        allowed = set(config.branches_of(brand))
        branches = [b for b in branches if b in allowed] if branches else sorted(allowed)
    if branches:
        panel = panel[panel["branch_id"].isin(branches)]
    if platforms:
        panel = panel[panel["platform_id"].isin(platforms)]
    panel = panel.assign(brand_id=panel["branch_id"].map(config.brand_of))
    return panel.reset_index(drop=True)


def data_bounds() -> tuple[date | None, date | None]:
    df = db.read_df("SELECT MIN(business_date) AS a, MAX(business_date) AS b FROM orders")
    if df.empty or pd.isna(df.loc[0, "a"]):
        return None, None
    return pd.to_datetime(df.loc[0, "a"]).date(), pd.to_datetime(df.loc[0, "b"]).date()


def is_demo_data() -> bool:
    df = db.read_df("SELECT COUNT(*) AS n, SUM(CASE WHEN is_demo THEN 1 ELSE 0 END) AS d FROM orders")
    return bool(df.loc[0, "n"]) and bool(df.loc[0, "d"])


# --------------------------------------------------------------------------- métricas
def sales_col() -> str:
    return "net" if config.settings()["business"].get("headline_sales") == "net" else "gross"


def _div(a, b):
    return float(a) / float(b) if b not in (0, None) and not pd.isna(b) and not pd.isna(a) else None


def summarize(df: pd.DataFrame) -> dict:
    """Agrega un subconjunto del panel y calcula los indicadores derivados."""
    s = {c: float(df[c].sum(min_count=1)) if c in df and df[c].notna().any() else None for c in ADDITIVE}
    for c in ["gross", "net", "orders", "cancelled_orders", "cancelled_amount", "adj_count", "adj_amount",
              "orders_s", "sales_s"]:
        s[c] = s[c] or 0.0
    s["sales"] = s[sales_col()]
    s["ticket"] = _div(s["sales"], s["orders"])
    s["conversion"] = _div(s["orders_s"], s["sessions"])
    s["ticket_s"] = _div(s["sales_s"], s["orders_s"])
    s["cancel_rate"] = _div(s["cancelled_orders"], s["orders"] + s["cancelled_orders"])
    s["adj_rate"] = _div(s["adj_amount"], s["gross"])
    s["availability"] = _div(s["open_minutes"], s["scheduled_minutes"])
    s["unavailable_minutes"] = (s["scheduled_minutes"] - s["open_minutes"]) \
        if s["scheduled_minutes"] is not None and s["open_minutes"] is not None else None
    s["payout_estimated"] = bool(df["payout_estimated"].max()) if "payout_estimated" in df and len(df) else False
    s["take_rate"] = _div(s["gross"] - s["payout"], s["gross"]) if s["payout"] is not None else None
    s["days"] = int(df["business_date"].nunique()) if len(df) else 0
    return s


def pct(cur, base):
    if cur is None or base in (None, 0) or pd.isna(base) or pd.isna(cur):
        return None
    return cur / base - 1.0


def compare(cur: dict, base: dict, metrics=("sales", "gross", "net", "orders", "ticket", "sessions",
                                            "conversion", "open_minutes", "availability", "adj_amount",
                                            "adj_rate", "cancelled_orders", "cancel_rate", "payout")) -> dict:
    out = {}
    for m in metrics:
        c, b = cur.get(m), base.get(m)
        rate_like = m in ("conversion", "availability", "adj_rate", "cancel_rate")
        out[m] = {"actual": c, "base": b,
                  "delta_pct": pct(c, b),
                  "delta_pp": (c - b) if rate_like and c is not None and b is not None else None}
    return out


def filt(panel: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    return panel[(panel["business_date"] >= start) & (panel["business_date"] <= end)]


# --------------------------------------------------------------------------- períodos
@dataclass
class Periods:
    D: date

    @property
    def week_start(self) -> date:
        return self.D - timedelta(days=self.D.weekday())

    @property
    def wtd(self):
        return self.week_start, self.D

    @property
    def wtd_prev(self):
        return self.week_start - timedelta(days=7), self.D - timedelta(days=7)

    @property
    def week_full(self):
        return self.week_start, self.week_start + timedelta(days=6)

    @property
    def prev_week_full(self):
        return self.week_start - timedelta(days=7), self.week_start - timedelta(days=1)

    @property
    def month_start(self) -> date:
        return self.D.replace(day=1)

    @property
    def month_end(self) -> date:
        nxt = (self.month_start + timedelta(days=32)).replace(day=1)
        return nxt - timedelta(days=1)

    @property
    def mtd(self):
        return self.month_start, self.D

    @property
    def prev_month_full(self):
        end = self.month_start - timedelta(days=1)
        return end.replace(day=1), end

    @property
    def prev_mtd(self):
        """Mismo número de días transcurridos en el mes anterior (recortado a su largo)."""
        s, e = self.prev_month_full
        return s, min(e, s + timedelta(days=self.D.day - 1))

    @property
    def history(self):
        """Semanas completas previas usadas para el patrón por día de semana."""
        n = int(config.settings()["run_rate"]["history_weeks"])
        return self.week_start - timedelta(days=7 * n), self.week_start - timedelta(days=1)

    @property
    def load_range(self):
        """Rango mínimo de datos a cargar para cualquier vista del día D."""
        start = min(self.history[0], self.prev_month_full[0],
                    self.D - timedelta(days=7 * int(config.settings()["alerts"]["baseline_weeks"])))
        return start, self.D


def days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# --------------------------------------------------------------------------- run rate
def dow_weights(hist: pd.DataFrame, metric: str, data_start: date | None, hist_end: date) -> tuple[dict | None, int]:
    """Promedio por día de semana (0=lunes) sobre semanas completas con datos.

    Devuelve (pesos, semanas_usadas). Si no hay suficiente historia -> (None, n).
    """
    if hist.empty or data_start is None:
        return None, 0
    min_w = int(config.settings()["run_rate"]["min_weeks_for_pattern"])
    first = max(hist["business_date"].min(), data_start)
    # sólo semanas completas posteriores al inicio de los datos
    first_monday = first + timedelta(days=(7 - first.weekday()) % 7)
    n_weeks = ((hist_end - first_monday).days + 1) // 7
    if n_weeks < min_w:
        return None, max(n_weeks, 0)
    start = hist_end - timedelta(days=7 * n_weeks - 1)
    h = hist[hist["business_date"] >= start]
    daily = h.groupby("business_date")[metric].sum()
    full = pd.Series(0.0, index=days(start, hist_end))
    full.loc[daily.index] = daily.values
    by_dow = full.groupby([d.weekday() for d in full.index]).mean()
    if by_dow.sum() <= 0:
        return None, n_weeks
    return by_dow.to_dict(), n_weeks


def project(actual: float, elapsed: list[date], period: list[date], weights: dict | None) -> tuple[float, str]:
    if not elapsed:
        return float("nan"), "sin datos"
    if weights:
        e = sum(weights.get(d.weekday(), 0.0) for d in elapsed)
        t = sum(weights.get(d.weekday(), 0.0) for d in period)
        if e > 0:
            return actual * t / e, "patrón día de semana"
    return actual * len(period) / len(elapsed), "lineal"


PROJECTABLE = ["sales", "gross", "net", "orders", "sessions", "orders_s", "adj_amount", "cancelled_orders",
               "open_minutes", "scheduled_minutes"]


def run_rate(panel: pd.DataFrame, D: date, kind: str = "week", group: str | None = None) -> pd.DataFrame:
    """Proyección de cierre semanal/mensual, total o por grupo (branch_id / platform_id).

    Fórmula (patrón día de semana):  Proyección = Real_a_la_fecha x  Σ_{días del período} w_d
                                                                   / Σ_{días transcurridos} w_d
    donde w_d es la venta promedio de ese día de semana en las últimas N semanas completas.
    Si no hay al menos `min_weeks_for_pattern` semanas de historia, se usa la proyección lineal.
    """
    P = Periods(D)
    start, end = (P.week_full if kind == "week" else (P.month_start, P.month_end))
    base_s, base_e = (P.prev_week_full if kind == "week" else P.prev_month_full)
    hs, he = P.history
    data_start, _ = data_bounds()
    elapsed, period = days(start, D), days(start, end)
    panel = panel.assign(sales=panel[sales_col()])
    groups = [None] if group is None else sorted(panel[group].dropna().unique())
    rows = []
    hist_all = filt(panel, hs, he)
    for g in groups:
        sub = panel if g is None else panel[panel[group] == g]
        cur = summarize(filt(sub, start, D))
        base = summarize(filt(sub, base_s, base_e))
        hist = filt(sub, hs, he)
        row = {"group": g or "TOTAL"}
        for m in PROJECTABLE:
            w, n = dow_weights(hist, m if m != "sales" else sales_col(), data_start, he)
            if w is None and g is not None:  # sin historia propia: usar la forma del total
                w, n = dow_weights(hist_all, m if m != "sales" else sales_col(), data_start, he)
            val = cur.get(m) or 0.0
            if m in ("sessions", "open_minutes", "scheduled_minutes") and cur.get(m) is None:
                row[f"{m}_proj"], row[f"{m}_actual"] = None, None
                continue
            proj, method = project(val, elapsed, period, w)
            row[f"{m}_actual"], row[f"{m}_proj"] = val, proj
            if m == "sales":
                row["method"], row["weeks_history"] = method, n
        row["ticket_proj"] = _div(row["sales_proj"], row["orders_proj"])
        row["conversion_proj"] = _div(row.get("orders_s_proj"), row.get("sessions_proj")) \
            if row.get("sessions_proj") else None
        row["adj_rate_proj"] = _div(row["adj_amount_proj"], row["gross_proj"])
        row["base_sales"] = base["sales"]
        row["base_orders"] = base["orders"]
        row["base_ticket"] = base["ticket"]
        row["base_sessions"] = base["sessions"]
        row["base_conversion"] = base["conversion"]
        row["base_adj_amount"] = base["adj_amount"]
        row["base_adj_rate"] = base["adj_rate"]
        row["base_cancelled_orders"] = base["cancelled_orders"]
        row["vs_base_pct"] = pct(row["sales_proj"], base["sales"])
        row["days_elapsed"], row["days_total"] = len(elapsed), len(period)
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- tablas por entidad
def entity_table(panel: pd.DataFrame, cur_range, base_range, by: str) -> pd.DataFrame:
    rows = []
    for g, sub in panel.groupby(by):
        c = summarize(filt(sub, *cur_range))
        b = summarize(filt(sub, *base_range))
        rows.append({by: g, "venta": c["sales"], "var_venta": pct(c["sales"], b["sales"]),
                     "ordenes": c["orders"], "var_ordenes": pct(c["orders"], b["orders"]),
                     "ticket": c["ticket"], "var_ticket": pct(c["ticket"], b["ticket"]),
                     "sesiones": c["sessions"], "var_sesiones": pct(c["sessions"], b["sessions"]),
                     "conversion": c["conversion"],
                     "var_conversion_pp": (c["conversion"] - b["conversion"])
                     if c["conversion"] is not None and b["conversion"] is not None else None,
                     "disponibilidad": c["availability"],
                     "min_no_disponible": c["unavailable_minutes"],
                     "resarcimientos": c["adj_amount"], "resarc_pct": c["adj_rate"],
                     "var_resarc": pct(c["adj_amount"], b["adj_amount"]),
                     "cancelaciones": c["cancelled_orders"], "cancel_pct": c["cancel_rate"],
                     "liquidacion": c["payout"], "take_rate": c["take_rate"],
                     "liq_estimada": c["payout_estimated"],
                     "venta_base": b["sales"]})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- explicación de variaciones
def _lmdi(x1, x0):
    if x1 is None or x0 is None or x1 <= 0 or x0 <= 0:
        return None
    return math.log(x1 / x0)


def decompose(cur: dict, base: dict) -> dict | None:
    """Venta = Sesiones x Conversión x Ticket. Reparte la variación de venta (con sesiones)
    entre los tres factores con el método LMDI (las tres partes suman exactamente la variación)."""
    s1, s0 = cur.get("sales_s"), base.get("sales_s")
    if not s1 or not s0 or not cur.get("sessions") or not base.get("sessions"):
        return None
    conv1, conv0 = _div(cur["orders_s"], cur["sessions"]), _div(base["orders_s"], base["sessions"])
    t1, t0 = _div(cur["sales_s"], cur["orders_s"]), _div(base["sales_s"], base["orders_s"])
    logs = {"sesiones": _lmdi(cur["sessions"], base["sessions"]), "conversion": _lmdi(conv1, conv0),
            "ticket": _lmdi(t1, t0)}
    if any(v is None for v in logs.values()) or s1 == s0:
        return {"delta": s1 - s0, "sesiones": 0.0, "conversion": 0.0, "ticket": 0.0}
    L = (s1 - s0) / (math.log(s1) - math.log(s0))
    out = {k: L * v for k, v in logs.items()}
    out["delta"] = s1 - s0
    out["pct"] = {"sesiones": pct(cur["sessions"], base["sessions"]), "conversion": pct(conv1, conv0),
                  "ticket": pct(t1, t0)}
    return out


def contribution(panel: pd.DataFrame, cur_range, base_range, by: str) -> pd.DataFrame:
    """Qué sucursal/plataforma explica la variación total de venta."""
    t = entity_table(panel, cur_range, base_range, by)
    if t.empty:
        return t
    t["delta"] = t["venta"] - t["venta_base"]
    total = t["delta"].sum()
    t["explica_pct"] = t["delta"] / total if total else None
    return t.sort_values("delta")


# --------------------------------------------------------------------------- mix de productos
def product_mix(start: date, end: date, branches=None, platforms=None, products=None) -> pd.DataFrame:
    df = db.read_df("""
        SELECT o.branch_id, o.platform_id, p.name AS product, p.category,
               SUM(i.quantity) AS units, SUM(i.gross_amount) AS revenue
        FROM order_items i
        JOIN orders o ON o.platform_id = i.platform_id AND o.order_id = i.order_id
        LEFT JOIN product p ON p.product_id = i.product_id
        WHERE o.status = 'completed' AND o.business_date BETWEEN :s AND :e
        GROUP BY o.branch_id, o.platform_id, p.name, p.category""",
                    {"s": start.isoformat(), "e": end.isoformat()})
    if branches:
        df = df[df["branch_id"].isin(branches)]
    if platforms:
        df = df[df["platform_id"].isin(platforms)]
    if products:
        df = df[df["product"].isin(products)]
    return df


def mix_summary(mix: pd.DataFrame) -> pd.DataFrame:
    g = mix.groupby(["product", "category"], dropna=False, as_index=False)[["units", "revenue"]].sum()
    tot = g["revenue"].sum()
    g["share"] = g["revenue"] / tot if tot else 0.0
    g["unit_share"] = g["units"] / g["units"].sum() if g["units"].sum() else 0.0
    return g.sort_values("revenue", ascending=False).reset_index(drop=True)


def mix_compare(cur: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    a, b = mix_summary(cur), mix_summary(base)
    a["category"], b["category"] = a["category"].fillna("Sin categoría"), b["category"].fillna("Sin categoría")
    m = a.merge(b, on=["product", "category"], how="outer", suffixes=("", "_base"))
    num_cols = [c for c in m.columns if c not in ("product", "category")]
    m[num_cols] = m[num_cols].fillna(0.0)
    m["var_units"] = np.where(m["units_base"] > 0, m["units"] / m["units_base"].replace(0, np.nan) - 1, np.nan)
    m["var_revenue"] = np.where(m["revenue_base"] > 0, m["revenue"] / m["revenue_base"].replace(0, np.nan) - 1, np.nan)
    m["share_pp"] = m["share"] - m["share_base"]
    m["delta_revenue"] = m["revenue"] - m["revenue_base"]
    return m.sort_values("revenue", ascending=False).reset_index(drop=True)


def branch_mix_divergence(mix: pd.DataFrame) -> pd.DataFrame:
    """Qué tan distinto es el mix de cada sucursal vs. el resto (distancia de variación total, 0-100%)."""
    rows = []
    for b in sorted(mix["branch_id"].unique()):
        mine = mix[mix["branch_id"] == b].groupby("product")["revenue"].sum()
        rest = mix[mix["branch_id"] != b].groupby("product")["revenue"].sum()
        if mine.sum() <= 0 or rest.sum() <= 0:
            continue
        p, q = (mine / mine.sum()), (rest / rest.sum())
        allp = p.index.union(q.index)
        p, q = p.reindex(allp, fill_value=0), q.reindex(allp, fill_value=0)
        diff = (p - q).sort_values()
        rows.append({"branch_id": b, "divergencia": 0.5 * float((p - q).abs().sum()),
                     "sobre_representado": f"{diff.index[-1]} (+{diff.iloc[-1]*100:.1f} pp)",
                     "sub_representado": f"{diff.index[0]} ({diff.iloc[0]*100:.1f} pp)"})
    return pd.DataFrame(rows).sort_values("divergencia", ascending=False) if rows else pd.DataFrame()
