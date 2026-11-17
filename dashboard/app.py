"""Centro de Control Comercial · Dashboard (Streamlit).

Ejecutar:  streamlit run dashboard/app.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importlib  # noqa: E402

from cci import config, db, kpis  # noqa: E402

# Streamlit solo recarga el script principal: se recargan a mano los módulos de cci/ para que un cambio
# en GitHub (código o base comprimida) se aplique sin reiniciar el servidor.
for _m in (config, db, kpis):
    importlib.reload(_m)

st.set_page_config(page_title="Centro de Control", page_icon="📊", layout="wide",
                   initial_sidebar_state="expanded")

# ------------------------------------------------------------------ estilo y colores
PLAT_COLOR = {"justo": "#2a78d6", "rappi": "#eb6834", "ubereats": "#1baf7a"}   # slots categóricos 1-3
BRANCH_COLOR = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
CUR, BASE, PROJ = "#2a78d6", "#898781", "#2a78d6"
POS, NEG = "#2a78d6", "#e34948"   # par divergente azul / rojo
LEVEL_ICON = {"ROJO": "🔴", "AMARILLO": "🟡", "VERDE": "🟢"}

st.markdown("""
<style>
.block-container {padding-top: 3.2rem; padding-bottom: 2rem;}
div[data-testid="stMetric"] {border: 1px solid rgba(128,128,128,.25); border-radius: 10px; padding: 10px 14px;}
div[data-testid="stMetricLabel"] p {font-size: .82rem;}
.demo-banner {background:#fab219; color:#0b0b0b; padding:8px 14px; border-radius:8px; font-weight:600; margin-bottom:10px;}
.tag {display:inline-block; font-size:.72rem; padding:1px 7px; border-radius:10px; margin-left:6px;
      border:1px solid rgba(128,128,128,.4);}
.alert-card {border-left: 5px solid; padding: 8px 12px; margin-bottom: 8px; border-radius: 6px;
             background: rgba(128,128,128,.06);}
</style>""", unsafe_allow_html=True)


def _password_gate() -> None:
    pwd = os.environ.get("APP_PASSWORD")
    try:
        pwd = pwd or st.secrets.get("APP_PASSWORD")
    except Exception:
        pass
    if not pwd or st.session_state.get("auth_ok"):
        return
    st.title("Centro de Control")
    entered = st.text_input("Contraseña", type="password")
    if entered and entered == pwd:
        st.session_state["auth_ok"] = True
        st.rerun()
    elif entered:
        st.error("Contraseña incorrecta")
    st.stop()


_password_gate()


# ------------------------------------------------------------------ formato
def money(v) -> str:
    if v is None or pd.isna(v):
        return "–"
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:,.1f} M".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}${v:,.0f}".replace(",", ".")


def num(v, d=0) -> str:
    return "–" if v is None or pd.isna(v) else f"{v:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pctf(v, signed=True, d=1) -> str:
    if v is None or pd.isna(v):
        return "–"
    s = f"{v * 100:+.{d}f}%" if signed else f"{v * 100:.{d}f}%"
    return s.replace(".", ",")


def ppf(v) -> str:
    return "–" if v is None or pd.isna(v) else f"{v * 100:+.2f} pp".replace(".", ",")


def chart_layout(fig: go.Figure, height=320, legend=True) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=8, r=8, t=40, b=8), hovermode="x unified",
                      legend=dict(orientation="h", yanchor="top", y=-0.12, x=0) if legend else None,
                      showlegend=legend, font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif"))
    fig.update_traces(cliponaxis=False, selector=dict(type="bar"))
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.18)", zeroline=False, tickformat=",.0f")
    return fig


# ------------------------------------------------------------------ datos
@st.cache_data(ttl=600, show_spinner=False)
def bounds():
    db.init_db()
    return kpis.data_bounds(), kpis.is_demo_data()


@st.cache_data(ttl=600, show_spinner=False)
def panel_for(D: date, branches: tuple, platforms: tuple) -> pd.DataFrame:
    s, e = kpis.Periods(D).load_range
    p = kpis.load_panel(s, e, list(branches) or None, list(platforms) or None)
    p["sales"] = p[kpis.sales_col()]
    return p


@st.cache_data(ttl=600, show_spinner=False)
def mix_for(s: date, e: date, branches: tuple, platforms: tuple) -> pd.DataFrame:
    return kpis.product_mix(s, e, list(branches) or None, list(platforms) or None)


@st.cache_data(ttl=300, show_spinner=False)
def sql(q: str, **params) -> pd.DataFrame:
    return db.read_df(q, params)


(dmin, dmax), DEMO = bounds()
BN_ALL, PN_ALL, BRANDS = config.branch_names(), config.platform_names(), config.brands()
SALES_LABEL = "Venta bruta" if kpis.sales_col() == "gross" else "Venta neta"

if dmax is None:
    st.cache_data.clear()   # no dejar en memoria el estado "sin datos"
    st.title("Centro de Control Comercial")
    st.warning("Todavía no hay datos cargados. Ver README: cargar plantillas, conectar fuentes o "
               "ejecutar `python -m cci.cli demo` para ver una demostración.")
    with st.expander("Diagnóstico"):
        gz = [str(g) for g in (config.DATA_DIR / "cci.db.gz", config.ROOT / "cci.db.gz") if g.exists()]
        dbf = config.DATA_DIR / "cci.db"
        st.write({"base comprimida encontrada": gz or "no",
                  "base local": str(dbf), "existe": dbf.exists(),
                  "tamaño (bytes)": dbf.stat().st_size if dbf.exists() else 0,
                  "DATABASE_URL configurada": bool(os.environ.get("DATABASE_URL"))})
    st.stop()

# ------------------------------------------------------------------ filtros (barra lateral)
with st.sidebar:
    brand_opts = list(BRANDS) + (["__all__"] if len(BRANDS) > 1 else [])
    brand_sel = st.radio("Marca", brand_opts, horizontal=True,
                         format_func=lambda b: BRANDS[b]["name"] if b in BRANDS else "Todas")
    BRAND = None if brand_sel == "__all__" else brand_sel
    BRAND_NAME = BRANDS[BRAND]["name"] if BRAND else "Todas las marcas"
    BN = {b: BN_ALL[b] for b in config.branches_of(BRAND)}
    PN = {p: n for p, n in PN_ALL.items() if BRAND is None or p in BRANDS[BRAND]["platforms"]}
    D = st.date_input("Día analizado (cierre)", value=dmax, min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
    sel_b = st.multiselect("Sucursales", list(BN), format_func=BN.get, placeholder="Todas")
    sel_p = st.multiselect("Plataformas", list(PN), format_func=PN.get, placeholder="Todas") if len(PN) > 1 else []
    if len(PN) == 1:
        st.caption(f"Plataforma: {next(iter(PN.values()))}")
    st.caption(f"Datos disponibles: {dmin:%d/%m/%Y} – {dmax:%d/%m/%Y}")
    last_run = sql("SELECT MAX(finished_at) AS t FROM ingestion_run WHERE status IN ('ok','warning')")
    if not last_run.empty and pd.notna(last_run.loc[0, "t"]):
        st.caption(f"Última actualización: {pd.to_datetime(last_run.loc[0, 't']):%d/%m/%Y %H:%M} UTC")
    if st.button("↻ Recargar datos"):
        st.cache_data.clear()
        st.rerun()

P = kpis.Periods(D)
SB = sel_b or list(BN)   # sucursales en alcance (las de la marca si no se eligió ninguna)
panel = panel_for(D, tuple(SB), tuple(sel_p))
F = lambda a, b: kpis.summarize(kpis.filt(panel, a, b))  # noqa: E731
scope = " · ".join(filter(None, [BRAND_NAME,
                                 ", ".join(BN[b] for b in sel_b) if sel_b else f"{len(BN)} sucursales",
                                 ", ".join(PN[p] for p in sel_p) if sel_p else
                                 (f"{len(PN)} plataformas" if len(PN) > 1 else next(iter(PN.values())))]))


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def header(title: str, subtitle: str = ""):
    if DEMO:
        st.markdown('<div class="demo-banner">⚠ DATOS DE DEMOSTRACIÓN · sintéticos, no corresponden al negocio real</div>',
                    unsafe_allow_html=True)
    recent = panel[(panel["business_date"] > D - timedelta(days=7)) & (panel["orders"] > 0)]
    present = set(recent["platform_id"].unique())
    missing = [PN[p] for p in (sel_p or list(PN)) if p not in present]
    if missing:
        st.warning(f"**Datos parciales:** no hay órdenes cargadas de {', '.join(missing)} en los últimos 7 días. "
                   f"Las cifras de esta pantalla incluyen solo: {', '.join(PN[p] for p in present) or 'ninguna plataforma'}.",
                   icon="⚠️")
    st.markdown(f"## {title}")
    st.caption(f"{scope} · día analizado: {DIAS[D.weekday()]} {D:%d/%m/%Y}" + (f" · {subtitle}" if subtitle else ""))


def metric(col, label, value, delta=None, help=None, inverse=False):
    col.metric(label, value, delta, delta_color="inverse" if inverse else "normal", help=help)


def alert_cards(df: pd.DataFrame, limit: int | None = None):
    colors = {"ROJO": "#d03b3b", "AMARILLO": "#fab219", "VERDE": "#0ca30c"}
    for r in df.head(limit or len(df)).itertuples():
        where = " · ".join(filter(None, [BN.get(r.branch_id) if r.branch_id else None,
                                          PN.get(r.platform_id) if r.platform_id else None])) or "Consolidado"
        st.markdown(
            f'<div class="alert-card" style="border-color:{colors[r.level]}">'
            f'<b>{LEVEL_ICON[r.level]} {r.level} · {r.title}</b><br>'
            f'<span style="opacity:.85">{r.what}</span><br>'
            f'<small><b>Dónde:</b> {where} · <b>Magnitud:</b> {r.magnitude} · <b>Comparado con:</b> {r.comparison}</small><br>'
            f'<small><b>Acción recomendada:</b> {r.recommendation}</small></div>', unsafe_allow_html=True)


def ai_rep() -> pd.DataFrame:
    r = sql("SELECT content_md, model, generated_at, brand_id FROM ai_report WHERE business_date = :d", d=D.isoformat())
    return r[r["brand_id"].isna() if BRAND is None else (r["brand_id"] == BRAND)].reset_index(drop=True)


def load_alerts() -> pd.DataFrame:
    a = sql("SELECT * FROM alert WHERE business_date = :d", d=D.isoformat())
    if BRAND:
        a = a[a["brand_id"].isna() | (a["brand_id"] == BRAND)]
    if sel_b:
        a = a[a["branch_id"].isna() | a["branch_id"].isin(sel_b)]
    if sel_p:
        a = a[a["platform_id"].isna() | a["platform_id"].isin(sel_p)]
    a["o"] = a["level"].map({"ROJO": 0, "AMARILLO": 1, "VERDE": 2})
    return a.sort_values("o")


def pace_chart(kind: str):
    """Curva acumulada: período actual vs anterior + proyección (línea punteada)."""
    if kind == "week":
        start, end = P.week_full
        bstart = P.prev_week_full[0]
        labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        n = 7
        prev_name = "Semana anterior"
    else:
        start, end = P.month_start, P.month_end
        bstart, bend = P.prev_month_full
        n = (end - start).days + 1
        labels = [str(i + 1) for i in range(max(n, (bend - bstart).days + 1))]
        prev_name = "Mes anterior"
    cur = panel[(panel.business_date >= start) & (panel.business_date <= D)].groupby("business_date")["sales"].sum()
    cur_c = [cur.get(start + timedelta(days=i), 0) for i in range((D - start).days + 1)]
    bend_ = bstart + timedelta(days=len(labels) - 1) if kind == "month" else bstart + timedelta(days=6)
    if kind == "month":
        bend_ = P.prev_month_full[1]
    base = panel[(panel.business_date >= bstart) & (panel.business_date <= bend_)].groupby("business_date")["sales"].sum()
    base_c = [base.get(bstart + timedelta(days=i), 0) for i in range((bend_ - bstart).days + 1)]
    cum = pd.Series(cur_c).cumsum()
    bcum = pd.Series(base_c).cumsum()
    rr = kpis.run_rate(panel, D, kind).iloc[0]
    fig = go.Figure()
    fig.add_scatter(x=labels[:len(bcum)], y=bcum, name=prev_name, line=dict(color=BASE, width=2))
    fig.add_scatter(x=labels[:len(cum)], y=cum, name="Actual (real)", line=dict(color=CUR, width=2.5),
                    mode="lines+markers", marker=dict(size=8))
    # proyección: reparte lo que falta según el patrón por día de semana
    if len(cum) < n and pd.notna(rr["sales_proj"]):
        remaining = [start + timedelta(days=i) for i in range(len(cum), n)]
        w, _ = kpis.dow_weights(kpis.filt(panel, *P.history), "sales", dmin, P.history[1])
        ws = [w.get(d.weekday(), 1) if w else 1 for d in remaining]
        left = rr["sales_proj"] - cum.iloc[-1]
        proj = [cum.iloc[-1]] + list(cum.iloc[-1] + pd.Series(ws).cumsum() / sum(ws) * left)
        fig.add_scatter(x=labels[len(cum) - 1:n], y=proj, name=f"Proyección ({rr['method']})",
                        line=dict(color=PROJ, width=2, dash="dash"))
    fig.update_layout(title=dict(text=f"{SALES_LABEL} acumulada", font=dict(size=14)))
    return chart_layout(fig, 300), rr


# ====================================================================== PANTALLAS
def page_resumen():
    header("Resumen ejecutivo")
    y, d1, d7 = F(D, D), F(D - timedelta(days=1), D - timedelta(days=1)), F(D - timedelta(days=7), D - timedelta(days=7))
    wtd, wtdp = F(*P.wtd), F(*P.wtd_prev)
    mtd, mtdp = F(*P.mtd), F(*P.prev_mtd)
    rrw = kpis.run_rate(panel, D, "week").iloc[0]
    rrm = kpis.run_rate(panel, D, "month").iloc[0]

    st.markdown("##### Ventas")
    st.caption("Variaciones: ayer vs mismo día de la semana anterior · semana y mes vs misma altura del período "
               "anterior · proyecciones vs período anterior completo (real).")
    c = st.columns(5)
    metric(c[0], f"{SALES_LABEL} ayer", money(y["sales"]), pctf(kpis.pct(y["sales"], d7["sales"])),
           help=f"Mismo día sem. anterior: {money(d7['sales'])}. Día anterior: {money(d1['sales'])} "
                f"({pctf(kpis.pct(y['sales'], d1['sales']))})")
    metric(c[1], "Semana acumulada", money(wtd["sales"]), pctf(kpis.pct(wtd["sales"], wtdp["sales"])),
           help=f"{P.wtd[0]:%d/%m}–{P.wtd[1]:%d/%m}: {money(wtd['sales'])} vs "
                f"{P.wtd_prev[0]:%d/%m}–{P.wtd_prev[1]:%d/%m}: {money(wtdp['sales'])}")
    metric(c[2], "Proyección semana", money(rrw["sales_proj"]), pctf(rrw["vs_base_pct"]),
           help=f"PROYECCIÓN (run rate). Semana anterior real: {money(rrw['base_sales'])}. Método: {rrw['method']} "
                f"({rrw['weeks_history']} semanas de historia).")
    metric(c[3], "Mes acumulado", money(mtd["sales"]), pctf(kpis.pct(mtd["sales"], mtdp["sales"])),
           help=f"Mes anterior, mismos {D.day} días: {money(mtdp['sales'])}")
    metric(c[4], "Proyección mes", money(rrm["sales_proj"]), pctf(rrm["vs_base_pct"]),
           help=f"PROYECCIÓN (run rate). Mes anterior completo real: {money(rrm['base_sales'])}. "
                f"Método: {rrm['method']}.")

    st.markdown("##### Operación ayer")
    c = st.columns(5)
    metric(c[0], "Órdenes", num(y["orders"]), pctf(kpis.pct(y["orders"], d7["orders"])))
    metric(c[1], "Ticket promedio", money(y["ticket"]), pctf(kpis.pct(y["ticket"], d7["ticket"])))
    metric(c[2], "Conversión", pctf(y["conversion"], False, 2),
           ppf(y["conversion"] - d7["conversion"]) if y["conversion"] and d7["conversion"] else None,
           help="Órdenes / sesiones, solo donde la plataforma informa sesiones. Definiciones distintas por plataforma.")
    metric(c[3], f"Resarcimientos ({pctf(y['adj_rate'], False)} venta)", money(y["adj_amount"]),
           pctf(kpis.pct(y["adj_amount"], d7["adj_amount"])), inverse=True,
           help=f"{num(y['adj_count'])} casos. Mismo día sem. ant.: {money(d7['adj_amount'])}")
    metric(c[4], "Open Time", pctf(y["availability"], False),
           ppf(y["availability"] - d7["availability"]) if y["availability"] and d7["availability"] else None,
           help=f"Tiempo disponible / horario programado. {num(y['unavailable_minutes'])} min fuera de línea ayer.")

    platform_overview()

    left, right = st.columns([3, 2])
    with left:
        tab_w, tab_m = st.tabs(["Ritmo semanal", "Ritmo mensual"])
        with tab_w:
            fig, _ = pace_chart("week")
            st.plotly_chart(fig, use_container_width=True, theme="streamlit")
        with tab_m:
            fig, _ = pace_chart("month")
            st.plotly_chart(fig, use_container_width=True, theme="streamlit")
        st.markdown("##### ¿Quién explica la variación de la semana?")
        ct = kpis.contribution(panel, P.wtd, P.wtd_prev, "branch_id")
        if not ct.empty:
            ct["name"] = ct["branch_id"].map(BN)
            fig = go.Figure(go.Bar(x=ct["delta"], y=ct["name"], orientation="h",
                                   marker=dict(color=[POS if v >= 0 else NEG for v in ct["delta"]], cornerradius=4),
                                   text=[money(v) for v in ct["delta"]], textposition="outside",
                                   hovertemplate="%{y}: %{x:,.0f}<extra></extra>"))
            lim = float(ct["delta"].abs().max() or 1) * 1.45
            fig.update_xaxes(title=f"Δ {SALES_LABEL.lower()} vs misma altura semana anterior", range=[-lim, lim])
            st.plotly_chart(chart_layout(fig, 260, legend=False), use_container_width=True, theme="streamlit")
    with right:
        a = load_alerts()
        st.markdown(f"##### Alertas del día · {LEVEL_ICON['ROJO']} {int((a.level == 'ROJO').sum())} "
                    f"{LEVEL_ICON['AMARILLO']} {int((a.level == 'AMARILLO').sum())} "
                    f"{LEVEL_ICON['VERDE']} {int((a.level == 'VERDE').sum())}")
        alert_cards(a[a.level != "VERDE"], 6)
        rep = ai_rep()
        with st.expander("🧠 Informe del analista IA", expanded=False):
            if rep.empty:
                st.info("Aún no hay informe IA para este día (se genera en la corrida diaria).")
            else:
                st.markdown(rep.loc[0, "content_md"])


PERIOD_OPTS = {
    "Ayer vs mismo día semana anterior": lambda: ((D, D), (D - timedelta(days=7), D - timedelta(days=7))),
    "Semana acumulada vs misma altura sem. anterior": lambda: (P.wtd, P.wtd_prev),
    "Mes acumulado vs mismos días mes anterior": lambda: (P.mtd, P.prev_mtd),
}


def entity_view(by: str, names: dict, page_title: str):
    header(page_title)
    opt = st.radio("Período", list(PERIOD_OPTS), horizontal=True, index=1)
    cur_r, base_r = PERIOD_OPTS[opt]()
    t = kpis.entity_table(panel, cur_r, base_r, by)
    if t.empty:
        st.info("Sin datos para los filtros elegidos.")
        return t
    rr = kpis.run_rate(panel, D, "week", by).set_index("group")
    rrm = kpis.run_rate(panel, D, "month", by).set_index("group")
    t["proy_semana"] = t[by].map(rr["sales_proj"])
    t["proy_sem_vs_ant"] = t[by].map(rr["vs_base_pct"])
    t["proy_mes"] = t[by].map(rrm["sales_proj"])
    t.insert(0, "nombre", t[by].map(names))
    show = t.drop(columns=[by, "venta_base"]).copy()
    for c in ["ticket", "venta", "liquidacion", "resarcimientos", "proy_semana", "proy_mes"]:
        show[c] = show[c].round(0)
    for c in ["var_venta", "var_ordenes", "var_ticket", "var_sesiones", "conversion", "disponibilidad",
              "resarc_pct", "var_resarc", "cancel_pct", "take_rate", "proy_sem_vs_ant"]:
        show[c] = show[c] * 100
    show["var_conversion_pp"] = show["var_conversion_pp"] * 100
    pc = lambda label: st.column_config.NumberColumn(label, format="%+.1f%%")  # noqa: E731
    st.dataframe(
        show, hide_index=True, use_container_width=True,
        column_config={
            "nombre": st.column_config.TextColumn("", pinned=True),
            "venta": st.column_config.NumberColumn(SALES_LABEL, format="localized"),
            "var_venta": pc("Δ venta"), "ordenes": st.column_config.NumberColumn("Órdenes", format="localized"),
            "var_ordenes": pc("Δ órdenes"), "ticket": st.column_config.NumberColumn("Ticket", format="localized"),
            "var_ticket": pc("Δ ticket"), "sesiones": st.column_config.NumberColumn("Sesiones", format="localized"),
            "var_sesiones": pc("Δ sesiones"),
            "conversion": st.column_config.NumberColumn("Conversión", format="%.2f%%"),
            "var_conversion_pp": st.column_config.NumberColumn("Δ conv (pp)", format="%+.2f"),
            "disponibilidad": st.column_config.NumberColumn("Open Time %", format="%.1f%%"),
            "min_no_disponible": st.column_config.NumberColumn("Min. fuera de línea", format="localized"),
            "resarcimientos": st.column_config.NumberColumn("Resarcimientos $", format="localized"),
            "resarc_pct": st.column_config.NumberColumn("Resarc. % venta", format="%.2f%%"),
            "var_resarc": pc("Δ resarc."),
            "cancelaciones": st.column_config.NumberColumn("Cancel.", format="localized"),
            "cancel_pct": st.column_config.NumberColumn("Cancel. %", format="%.1f%%"),
            "liquidacion": st.column_config.NumberColumn("Liquidación", format="localized",
                                                         help="Lo que paga la plataforma. Ver columna 'Liq. estimada'."),
            "take_rate": st.column_config.NumberColumn("Costo plataforma %", format="%.1f%%",
                                                       help="(Venta bruta − liquidación) / venta bruta"),
            "liq_estimada": st.column_config.CheckboxColumn("Liq. estimada"),
            "proy_semana": st.column_config.NumberColumn("Proy. semana", format="localized"),
            "proy_sem_vs_ant": pc("Proy. vs sem. ant."),
            "proy_mes": st.column_config.NumberColumn("Proy. mes", format="localized"),
        })
    st.caption("Hacé clic en el encabezado de una columna para ordenar. Δ = variación vs el período base elegido. "
               "Proyecciones = run rate con patrón por día de semana.")
    return t



def uber_weekly_section():
    """Tendencia semanal de Uber por tienda (resumen del portal; no requiere detalle por pedido)."""
    w = sql("SELECT branch_id, week_start, sales, orders FROM weekly_summary WHERE platform_id='ubereats' ORDER BY week_start")
    if w.empty:
        return
    w["week_start"] = pd.to_datetime(w["week_start"])
    w = w[w["week_start"] <= pd.Timestamp(D)]
    allk = f"ALL_{BRAND}" if BRAND else None
    pv = w.pivot(index="week_start", columns="branch_id", values="sales")
    stores = [c for c in pv.columns if c in BN and c in SB]
    if allk in pv.columns:
        known = [c for c in pv.columns if not c.startswith("ALL_")]
        rest = [b for b in BN if b not in known and BRAND and "ubereats" in BRANDS[BRAND]["platforms"]]
        if rest and not sel_b:
            pv["_rest"] = pv[allk] - pv[known].sum(axis=1)
            stores.append("_rest")
    if not stores:
        return
    st.markdown("##### Uber Eats · venta semanal por tienda (resumen del portal)")
    fig = go.Figure()
    rest_names = " + ".join(BN[b] for b in BN if b not in pv.columns and b != "_rest")
    for i, c in enumerate(stores):
        name = BN.get(c, f"{rest_names} (por diferencia)")
        fig.add_scatter(x=pv.index, y=pv[c], name=name, mode="lines+markers",
                        line=dict(color=BRANCH_COLOR[i % len(BRANCH_COLOR)], width=2), marker=dict(size=7))
    st.plotly_chart(chart_layout(fig, 330), use_container_width=True, theme="streamlit")
    st.caption("Semanas de lunes a domingo. Fuente: Uber Eats Manager › Ventas (CSV por tienda). Las tiendas sin archivo "
               "propio se muestran juntas, calculadas como total − tiendas conocidas. La semana del 14/09 incluye "
               "el cierre por Fiestas Patrias.")

def page_sucursales():
    t = entity_view("branch_id", BN, "Sucursales")
    if t is None or t.empty:
        return
    c1, c2 = st.columns(2)
    with c1:
        s = t.sort_values("var_venta")
        fig = go.Figure(go.Bar(x=s["var_venta"] * 100, y=s["nombre"], orientation="h",
                               marker=dict(color=[POS if v >= 0 else NEG for v in s["var_venta"].fillna(0)], cornerradius=4),
                               text=[pctf(v) for v in s["var_venta"]], textposition="outside",
                               hovertemplate="%{y}: %{x:.1f}%<extra></extra>"))
        fig.update_layout(title=dict(text="Variación de venta por sucursal (%)", font=dict(size=14)))
        lim = float(s["var_venta"].abs().max() or 0.1) * 100 * 1.35
        fig.update_xaxes(ticksuffix="%", range=[-lim, lim])
        st.plotly_chart(chart_layout(fig, 280, False), use_container_width=True, theme="streamlit")
    with c2:
        sel = st.selectbox("Detalle diario de", list(BN), format_func=BN.get)
        sub = kpis.filt(panel[panel.branch_id == sel], D - timedelta(days=27), D)
        g = sub.groupby(["business_date", "platform_id"])["sales"].sum().reset_index()
        fig = go.Figure()
        for p in PN:
            gp = g[g.platform_id == p]
            if not gp.empty:
                fig.add_bar(x=gp.business_date, y=gp.sales, name=PN[p], marker=dict(color=PLAT_COLOR[p]))
        fig.update_layout(barmode="stack", bargap=0.25,
                          title=dict(text=f"{BN[sel]} · venta diaria por plataforma (28 días)", font=dict(size=14)))
        st.plotly_chart(chart_layout(fig, 280), use_container_width=True, theme="streamlit")


def _sucursales_extra():
    uber_weekly_section()


def page_plataformas():
    t = entity_view("platform_id", PN, "Plataformas")
    if t is None or t.empty:
        return
    st.info("Las **sesiones** se definen distinto en cada plataforma (ver Configuración). Compará la conversión "
            "de cada plataforma contra sí misma en el tiempo; entre plataformas, solo como referencia.", icon="ℹ️")
    # solo semanas COMPLETAS (una semana a medias parecería una caída)
    sub = kpis.filt(panel, P.week_start - timedelta(days=56), P.week_start - timedelta(days=1))
    sub = sub[sub.business_date >= (dmin + timedelta(days=(7 - dmin.weekday()) % 7))]
    weekly = sub.assign(week=pd.to_datetime(sub.business_date).dt.to_period("W-SUN").dt.start_time)
    c1, c2 = st.columns(2)
    for col, metric_name, title, fmt in ((c1, "sales", f"{SALES_LABEL} por semana completa", ",.0f"),
                                         (c2, "conv", "Conversión por semana completa (órdenes/sesiones)", ".1%")):
        fig = go.Figure()
        for p in PN:
            w = weekly[weekly.platform_id == p].groupby("week")[["sales", "orders_s", "sessions"]].sum()
            if w.empty:
                continue
            y = w["sales"] if metric_name == "sales" else w["orders_s"] / w["sessions"].replace(0, pd.NA)
            fig.add_scatter(x=w.index, y=y, name=PN[p], mode="lines+markers",
                            line=dict(color=PLAT_COLOR[p], width=2), marker=dict(size=8))
        fig.update_layout(title=dict(text=title, font=dict(size=14)))
        fig = chart_layout(fig, 300)
        fig.update_yaxes(tickformat=fmt)
        col.plotly_chart(fig, use_container_width=True, theme="streamlit")
    st.markdown("##### Descomposición de la variación semanal: Venta = Sesiones × Conversión × Ticket")
    rows = []
    for (b, p), s in panel.groupby(["branch_id", "platform_id"]):
        dec = kpis.decompose(kpis.summarize(kpis.filt(s, *P.wtd)), kpis.summarize(kpis.filt(s, *P.wtd_prev)))
        if dec:
            rows.append({"Sucursal": BN[b], "Plataforma": PN[p], "Δ venta": dec["delta"],
                         "por sesiones": dec["sesiones"], "por conversión": dec["conversion"], "por ticket": dec["ticket"],
                         "Δ sesiones %": (dec["pct"]["sesiones"] or 0) * 100,
                         "Δ conversión %": (dec["pct"]["conversion"] or 0) * 100,
                         "Δ ticket %": (dec["pct"]["ticket"] or 0) * 100})
    if rows:
        dd = pd.DataFrame(rows).sort_values("Δ venta").round(0)
        loc = st.column_config.NumberColumn(format="localized")
        pc = st.column_config.NumberColumn(format="%+.1f%%")
        st.dataframe(dd, hide_index=True, use_container_width=True,
                     column_config={"Δ venta": loc, "por sesiones": loc, "por conversión": loc, "por ticket": loc,
                                    "Δ sesiones %": pc, "Δ conversión %": pc, "Δ ticket %": pc})
        st.caption("Semana actual acumulada vs misma altura de la semana anterior. Las tres columnas 'por …' suman "
                   "exactamente el Δ venta (método LMDI): indican si el cambio vino de tráfico, conversión o ticket.")


def page_mix():
    header("Mix de productos")
    opt = st.radio("Período", list(PERIOD_OPTS), horizontal=True, index=1)
    cur_r, base_r = PERIOD_OPTS[opt]()
    cur = mix_for(*cur_r, tuple(SB), tuple(sel_p))
    base = mix_for(*base_r, tuple(SB), tuple(sel_p))
    if cur.empty:
        st.info("No hay detalle de productos para este período/filtros.")
        return
    prods = st.multiselect("Filtrar productos", sorted(cur["product"].dropna().unique()), placeholder="Todos")
    if prods:
        cur, base = cur[cur["product"].isin(prods)], base[base["product"].isin(prods)]
    m = kpis.mix_compare(cur, base)
    c1, c2 = st.columns([3, 2])
    with c1:
        top = m.head(15).iloc[::-1]
        fig = go.Figure(go.Bar(x=top["revenue"], y=top["product"], orientation="h",
                               marker=dict(color=CUR, cornerradius=4),
                               customdata=top[["units", "share"]],
                               hovertemplate="%{y}<br>Venta %{x:,.0f}<br>Unidades %{customdata[0]:,.0f}"
                                             "<br>Participación %{customdata[1]:.1%}<extra></extra>"))
        fig.update_layout(title=dict(text="Ranking por facturación", font=dict(size=14)))
        st.plotly_chart(chart_layout(fig, 420, False), use_container_width=True, theme="streamlit")
    with c2:
        st.markdown("##### Mayores cambios vs período base")
        mv = m[(m["revenue_base"] > 0) | (m["revenue"] > 0)].copy()
        mv = mv.reindex(mv["delta_revenue"].abs().sort_values(ascending=False).index).head(10)
        st.dataframe(pd.DataFrame({"Producto": mv["product"], "Δ venta": mv["delta_revenue"],
                                   "Δ unid. %": mv["var_units"] * 100, "Δ partic. (pp)": mv["share_pp"] * 100}),
                     hide_index=True, use_container_width=True,
                     column_config={"Δ venta": st.column_config.NumberColumn(format="localized"),
                                    "Δ unid. %": st.column_config.NumberColumn(format="%+.1f%%"),
                                    "Δ partic. (pp)": st.column_config.NumberColumn(format="%+.2f")})
    st.markdown("##### Ranking completo")
    st.dataframe(pd.DataFrame({"Producto": m["product"], "Categoría": m["category"], "Unidades": m["units"],
                               "Venta": m["revenue"], "Participación %": m["share"] * 100,
                               "Δ unidades %": m["var_units"] * 100, "Δ venta %": m["var_revenue"] * 100,
                               "Δ partic. (pp)": m["share_pp"] * 100}),
                 hide_index=True, use_container_width=True,
                 column_config={"Unidades": st.column_config.NumberColumn(format="localized"),
                                "Venta": st.column_config.NumberColumn(format="localized"),
                                "Participación %": st.column_config.ProgressColumn(format="%.1f%%", min_value=0,
                                                                                   max_value=float(max(m["share"].max() * 100, 1))),
                                "Δ unidades %": st.column_config.NumberColumn(format="%+.1f%%"),
                                "Δ venta %": st.column_config.NumberColumn(format="%+.1f%%"),
                                "Δ partic. (pp)": st.column_config.NumberColumn(format="%+.2f")})
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("##### Participación de cada producto por sucursal")
        hm = cur.groupby(["branch_id", "product"])["revenue"].sum().reset_index()
        hm["share"] = hm["revenue"] / hm.groupby("branch_id")["revenue"].transform("sum")
        top_names = m.head(12)["product"].tolist()
        pv = hm[hm["product"].isin(top_names)].pivot(index="product", columns="branch_id", values="share") \
            .reindex(top_names).rename(columns=BN)
        fig = go.Figure(go.Heatmap(z=pv.values, x=list(pv.columns), y=list(pv.index),
                                   colorscale=[[0, "#cde2fb"], [0.5, "#3987e5"], [1, "#104281"]],
                                   xgap=2, ygap=2, hovertemplate="%{y} · %{x}: %{z:.1%}<extra></extra>",
                                   colorbar=dict(tickformat=".0%")))
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(chart_layout(fig, 420, False), use_container_width=True, theme="streamlit")
    with c2:
        st.markdown("##### Sucursales con mix más distinto al resto")
        dv = kpis.branch_mix_divergence(cur)
        if not dv.empty:
            dv["branch_id"] = dv["branch_id"].map(BN)
            dv["divergencia"] = dv["divergencia"] * 100
            st.dataframe(dv.rename(columns={"branch_id": "Sucursal", "divergencia": "Divergencia %",
                                            "sobre_representado": "Vende más que el resto",
                                            "sub_representado": "Vende menos que el resto"}),
                         hide_index=True, use_container_width=True,
                         column_config={"Divergencia %": st.column_config.NumberColumn(format="%.1f")})
            st.caption("0% = mix idéntico al del resto de las sucursales; 100% = totalmente distinto.")
    by_p = cur.groupby(["platform_id", "product"])["revenue"].sum().reset_index()
    if by_p["platform_id"].nunique() > 1:
        st.markdown("##### Mix por plataforma (participación de los 8 productos principales)")
        by_p["share"] = by_p["revenue"] / by_p.groupby("platform_id")["revenue"].transform("sum")
        top8 = m.head(8)["product"].tolist()
        fig = go.Figure()
        for p in PN:
            s = by_p[(by_p.platform_id == p) & by_p["product"].isin(top8)].set_index("product").reindex(top8)
            fig.add_bar(x=top8, y=s["share"], name=PN[p], marker=dict(color=PLAT_COLOR[p], cornerradius=4))
        fig = chart_layout(fig, 320)
        fig.update_layout(barmode="group", bargap=0.25, bargroupgap=0.08)
        fig.update_yaxes(tickformat=".0%")
        st.plotly_chart(fig, use_container_width=True, theme="streamlit")


def comparison_table(rows: list[tuple[str, dict]], proj: pd.Series | None):
    metrics = [("Venta", "sales", money), ("Órdenes", "orders", num), ("Ticket medio", "ticket", money),
               ("Sesiones", "sessions", num), ("Conversión", "conversion", lambda v: pctf(v, False, 2)),
               ("Open Time (h)", "open_minutes", lambda v: num(v / 60 if v else v)),
               ("Disponibilidad", "availability", lambda v: pctf(v, False)),
               ("Resarcimientos $", "adj_amount", money), ("Resarcimientos % venta", "adj_rate", lambda v: pctf(v, False, 2)),
               ("Cancelaciones", "cancelled_orders", num), ("Cancelaciones %", "cancel_rate", lambda v: pctf(v, False))]
    proj_map = {"sales": "sales_proj", "orders": "orders_proj", "ticket": "ticket_proj", "sessions": "sessions_proj",
                "conversion": "conversion_proj", "adj_amount": "adj_amount_proj", "adj_rate": "adj_rate_proj",
                "cancelled_orders": "cancelled_orders_proj", "open_minutes": "open_minutes_proj"}
    out = []
    for label, key, f in metrics:
        r = {"Indicador": label}
        for name, s in rows:
            r[name] = f(s.get(key))
        a, b = rows[0][1].get(key), rows[1][1].get(key)
        r["Δ actual vs base"] = (ppf(a - b) if key in ("conversion", "availability", "adj_rate", "cancel_rate")
                                 and a is not None and b is not None else pctf(kpis.pct(a, b)))
        if proj is not None:
            pv = proj.get(proj_map.get(key, ""), None)
            r["PROYECCIÓN cierre"] = f(pv) if key in proj_map and pv is not None and not pd.isna(pv) else "–"
            full = rows[2][1].get(key)
            if key not in proj_map or pv is None or pd.isna(pv):
                r["Proy. vs período ant. completo"] = "–"
            elif key in ("conversion", "adj_rate"):
                r["Proy. vs período ant. completo"] = ppf(pv - full) if full is not None else "–"
            else:
                r["Proy. vs período ant. completo"] = pctf(kpis.pct(pv, full))
        out.append(r)
    st.dataframe(pd.DataFrame(out), hide_index=True, use_container_width=True)



def uber_product_section():
    pp = sql("SELECT product, sales, units, sales_delta_pct, units_delta_pct, start_date, end_date FROM product_period "
             "WHERE platform_id='ubereats' ORDER BY sales DESC")
    if pp.empty or (BRAND and BRAND != "kobo"):
        return
    st.markdown("---\n##### Uber Eats · ranking de productos (resumen del portal, 6 tiendas)")
    st.caption(f"Período {pd.to_datetime(pp['start_date'].iloc[0]):%d/%m} – {pd.to_datetime(pp['end_date'].iloc[0]):%d/%m/%Y}; "
               "variación vs el período anterior de igual duración, según Uber.")
    pp["share"] = pp["sales"] / pp["sales"].sum() * 100
    c1, c2 = st.columns([3, 2])
    with c1:
        top = pp.head(15).iloc[::-1]
        fig = go.Figure(go.Bar(x=top["sales"], y=top["product"], orientation="h", marker=dict(color=PLAT_COLOR["ubereats"],
                               cornerradius=4), customdata=top[["share", "sales_delta_pct"]],
                               hovertemplate="%{y}<br>Venta %{x:,.0f}<br>Participación %{customdata[0]:.1f}%"
                                             "<br>Δ %{customdata[1]:+.1f}%<extra></extra>"))
        st.plotly_chart(chart_layout(fig, 430, False), use_container_width=True, theme="streamlit")
    with c2:
        mv = pp[pp["sales"] > pp["sales"].quantile(0.75)].dropna(subset=["sales_delta_pct"])
        st.markdown("Productos relevantes que más cambiaron")
        mv = mv.reindex(mv["sales_delta_pct"].abs().sort_values(ascending=False).index).head(10)
        st.dataframe(pd.DataFrame({"Producto": mv["product"], "Δ venta %": mv["sales_delta_pct"],
                                   "Partic. %": mv["share"]}), hide_index=True, use_container_width=True,
                     column_config={"Δ venta %": st.column_config.NumberColumn(format="%+.1f%%"),
                                    "Partic. %": st.column_config.NumberColumn(format="%.1f%%")})
    with st.expander("Ranking completo Uber"):
        st.dataframe(pd.DataFrame({"Producto": pp["product"], "Venta": pp["sales"], "Unidades": pp["units"],
                                   "Participación %": pp["share"], "Δ venta %": pp["sales_delta_pct"]}),
                     hide_index=True, use_container_width=True,
                     column_config={"Venta": st.column_config.NumberColumn(format="localized"),
                                    "Participación %": st.column_config.NumberColumn(format="%.1f%%"),
                                    "Δ venta %": st.column_config.NumberColumn(format="%+.1f%%")})


def hourly_section():
    h = sql("SELECT dow, hour, sales, orders, start_date, end_date FROM hourly_period WHERE platform_id='ubereats'")
    if h.empty or (BRAND and BRAND != "kobo"):
        return
    st.markdown("##### Uber Eats · ¿cuándo se vende? (día × hora, 6 tiendas)")
    pv = h.pivot_table(index="dow", columns="hour", values="sales", aggfunc="sum").reindex(range(7))
    pv = pv.reindex(columns=range(int(h["hour"].min()), 24)).fillna(0)
    labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
    fig = go.Figure(go.Heatmap(z=pv.values / 1e6, x=[f"{c}h" for c in pv.columns], y=labels,
                               colorscale=[[0, "#f0efec"], [0.15, "#cde2fb"], [0.5, "#3987e5"], [1, "#104281"]],
                               xgap=2, ygap=2, colorbar=dict(title="$ M"),
                               hovertemplate="%{y} %{x}: $%{z:.1f} M<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(chart_layout(fig, 300, False), use_container_width=True, theme="streamlit")
    tot = h["sales"].sum()
    din = h[h["hour"] >= 19]["sales"].sum() / tot
    lun = h[(h["hour"] >= 12) & (h["hour"] <= 16)]["sales"].sum() / tot
    st.caption(f"Cena (19–23 h): {din * 100:.0f}% de la venta · almuerzo (12–16 h): {lun * 100:.0f}% · "
               f"17–18 h casi sin venta · domingo prácticamente cerrado. Acumulado "
               f"{pd.to_datetime(h['start_date'].iloc[0]):%d/%m} – {pd.to_datetime(h['end_date'].iloc[0]):%d/%m}.")

def page_semanal():
    header("Análisis semanal", f"semana {P.week_start:%d/%m} – {P.week_full[1]:%d/%m}")
    rr = kpis.run_rate(panel, D, "week").iloc[0]
    comparison_table([(f"Semana actual ({P.wtd[0]:%d/%m}–{P.wtd[1]:%d/%m})", F(*P.wtd)),
                      (f"Sem. anterior misma altura ({P.wtd_prev[0]:%d/%m}–{P.wtd_prev[1]:%d/%m})", F(*P.wtd_prev)),
                      ("Semana anterior completa", F(*P.prev_week_full))], rr)
    st.caption(f"Comparación homogénea: mismos días de semana y mismo corte horario. PROYECCIÓN = run rate "
               f"({rr['method']}, {rr['weeks_history']} semanas de historia). Tasas en puntos porcentuales (pp).")
    c1, c2 = st.columns(2)
    with c1:
        fig, _ = pace_chart("week")
        st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    with c2:
        labels = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
        cur = [F(P.week_start + timedelta(days=i), P.week_start + timedelta(days=i))["sales"] if P.week_start + timedelta(days=i) <= D else None for i in range(7)]
        prev = [F(P.prev_week_full[0] + timedelta(days=i), P.prev_week_full[0] + timedelta(days=i))["sales"] for i in range(7)]
        fig = go.Figure()
        fig.add_bar(x=labels, y=prev, name="Semana anterior", marker=dict(color=BASE, cornerradius=4))
        fig.add_bar(x=labels, y=cur, name="Semana actual", marker=dict(color=CUR, cornerradius=4))
        fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08,
                          title=dict(text="Día contra día (lunes vs lunes…)", font=dict(size=14)))
        st.plotly_chart(chart_layout(fig, 300), use_container_width=True, theme="streamlit")
    st.markdown("##### Proyección de cierre semanal por sucursal y plataforma")
    c1, c2 = st.columns(2)
    for col, by, names in ((c1, "branch_id", BN), (c2, "platform_id", PN)):
        r = kpis.run_rate(panel, D, "week", by)
        col.dataframe(pd.DataFrame({"": r["group"].map(names), "Real a la fecha": r["sales_actual"],
                                    "PROYECCIÓN": r["sales_proj"], "Semana ant. real": r["base_sales"],
                                    "Δ %": r["vs_base_pct"] * 100}),
                      hide_index=True, use_container_width=True,
                      column_config={k: st.column_config.NumberColumn(format="localized")
                                     for k in ["Real a la fecha", "PROYECCIÓN", "Semana ant. real"]} |
                      {"Δ %": st.column_config.NumberColumn(format="%+.1f%%")})


def page_mensual():
    header("Análisis mensual", f"{MESES[D.month - 1]} {D.year}")
    rr = kpis.run_rate(panel, D, "month").iloc[0]
    comparison_table([(f"Mes actual real (1–{D.day})", F(*P.mtd)),
                      (f"Mes anterior mismos días (1–{P.prev_mtd[1].day})", F(*P.prev_mtd)),
                      ("Mes anterior completo", F(*P.prev_month_full))], rr)
    st.caption(f"Día {rr['days_elapsed']} de {rr['days_total']}. PROYECCIÓN mensual: real acumulado × "
               f"(peso de todos los días del mes / peso de los días transcurridos), pesos = venta promedio por "
               f"día de semana ({rr['method']}). No contempla feriados aún.")
    fig, _ = pace_chart("month")
    st.plotly_chart(fig, use_container_width=True, theme="streamlit")
    st.markdown("##### Proyección de cierre mensual por sucursal y plataforma")
    c1, c2 = st.columns(2)
    for col, by, names in ((c1, "branch_id", BN), (c2, "platform_id", PN)):
        r = kpis.run_rate(panel, D, "month", by)
        col.dataframe(pd.DataFrame({"": r["group"].map(names), "Real a la fecha": r["sales_actual"],
                                    "PROYECCIÓN": r["sales_proj"], "Órdenes proy.": r["orders_proj"],
                                    "Mes ant. real": r["base_sales"], "Δ %": r["vs_base_pct"] * 100}),
                      hide_index=True, use_container_width=True,
                      column_config={k: st.column_config.NumberColumn(format="localized")
                                     for k in ["Real a la fecha", "PROYECCIÓN", "Órdenes proy.", "Mes ant. real"]} |
                      {"Δ %": st.column_config.NumberColumn(format="%+.1f%%")})


def page_alertas():
    header("Alertas y anomalías")
    a = load_alerts()
    lv = st.segmented_control("Nivel", ["ROJO", "AMARILLO", "VERDE"], default=["ROJO", "AMARILLO", "VERDE"],
                              selection_mode="multi")
    st.caption("Línea base: mismo día de la semana en las últimas 4 semanas. Solo se alerta cuando el desvío "
               "supera el umbral **y** es estadísticamente distinguible del ruido normal.")
    if a.empty:
        st.success("Sin alertas para este día.")
    alert_cards(a[a["level"].isin(lv or [])])
    st.markdown("---\n##### Calidad de datos")
    dq = sql("SELECT severity, check_name, platform_id, branch_id, detail, detected_at FROM dq_issue "
             "WHERE business_date = :d OR (business_date IS NULL AND detected_at >= :t) ORDER BY severity",
             d=D.isoformat(), t=(D - timedelta(days=2)).isoformat())
    if dq.empty:
        st.success("Sin problemas de datos detectados para este día.")
    else:
        st.dataframe(dq, hide_index=True, use_container_width=True)
    with st.expander("Registro de cargas (últimas 50)"):
        st.dataframe(sql("SELECT started_at, platform_id, source_type, source_name, dataset, rows_read, "
                         "rows_inserted, rows_updated, rows_rejected, status, message FROM ingestion_run "
                         "ORDER BY started_at DESC LIMIT 50"), hide_index=True, use_container_width=True)


PERIOD_OPTS_LONG = {
    "Semana acumulada": lambda: P.wtd,
    "Mes acumulado": lambda: P.mtd,
    "Últimas 4 semanas": lambda: (D - timedelta(days=27), D),
    "Últimas 12 semanas": lambda: (D - timedelta(days=83), D),
}


def _in(df, col="branch_id"):
    df = df[df[col].isin(SB)] if col in df else df
    return df[df["platform_id"].isin(sel_p)] if sel_p and "platform_id" in df else df


def page_rentabilidad():
    header("Rentabilidad y calidad")
    opt = st.radio("Período", list(PERIOD_OPTS_LONG), horizontal=True, index=3)
    a, b = PERIOD_OPTS_LONG[opt]()
    pr = {"a": a.isoformat(), "b": b.isoformat()}
    o = _in(sql("""SELECT branch_id, platform_id, COUNT(*) AS ordenes, SUM(gross_amount) AS bruta,
                   SUM(discount_merchant) AS desc_local, SUM(discount_platform) AS desc_plat,
                   SUM(commission) AS comision, SUM(other_fees) AS otros, SUM(payout_amount) AS liquidacion
                   FROM orders WHERE status='completed' AND business_date BETWEEN :a AND :b
                   GROUP BY branch_id, platform_id""", **pr))
    if o.empty:
        st.info("Sin órdenes en el período.")
        return
    adj = _in(sql("""SELECT branch_id, platform_id, SUM(amount) AS compensaciones FROM adjustments
                     WHERE business_date BETWEEN :a AND :b GROUP BY branch_id, platform_id""", **pr))
    ads = _in(sql("""SELECT branch_id, platform_id, SUM(CASE WHEN kind='ads' THEN amount ELSE 0 END) AS ads,
                     SUM(CASE WHEN kind<>'ads' THEN amount ELSE 0 END) AS ajustes FROM costs
                     WHERE business_date BETWEEN :a AND :b GROUP BY branch_id, platform_id""", **pr))
    m = o.merge(adj, how="left", on=["branch_id", "platform_id"]).merge(ads, how="left", on=["branch_id", "platform_id"])
    m = m.fillna({"compensaciones": 0, "ads": 0, "ajustes": 0, "comision": 0, "otros": 0})
    m["neta"] = m["bruta"] - m["desc_local"]
    m["resultado"] = m["neta"] - m["comision"] - m["otros"] - m["compensaciones"] - m["ads"] - m["ajustes"]

    st.markdown("##### De la venta bruta a lo que queda para el local")
    st.caption("Venta bruta (precio de menú) − descuentos que paga el local − comisión e IVA − otros cargos − "
               "compensaciones − publicidad = **resultado después de plataforma** (antes de costo de comida y operación).")
    by = st.radio("Ver por", ["Sucursal", "Plataforma"], horizontal=True)
    key = "branch_id" if by == "Sucursal" else "platform_id"
    g = m.groupby(key)[["ordenes", "bruta", "desc_local", "neta", "comision", "otros", "compensaciones", "ads",
                        "ajustes", "resultado"]].sum()
    g.index = g.index.map(BN_ALL if key == "branch_id" else PN_ALL)
    t = pd.DataFrame({"Órdenes": g["ordenes"], "Venta bruta": g["bruta"], "Ticket bruto": g["bruta"] / g["ordenes"],
                      "Descuentos %": g["desc_local"] / g["bruta"] * 100, "Venta neta": g["neta"],
                      "Comisión %": g["comision"] / g["bruta"] * 100, "Compensaciones %": g["compensaciones"] / g["bruta"] * 100,
                      "Publicidad $": g["ads"], "Publicidad %": g["ads"] / g["bruta"] * 100,
                      "Resultado": g["resultado"], "Resultado %": g["resultado"] / g["bruta"] * 100})
    for c_ in ["Venta bruta", "Ticket bruto", "Venta neta", "Publicidad $", "Resultado"]:
        t[c_] = t[c_].round(0)
    t = t.round(1)
    t.index.name = by
    loc = st.column_config.NumberColumn(format="localized")
    pc = st.column_config.NumberColumn(format="%.1f%%")
    st.dataframe(t, use_container_width=True, column_config={
        "Órdenes": loc, "Venta bruta": loc, "Ticket bruto": loc, "Venta neta": loc, "Publicidad $": loc, "Resultado": loc,
        "Descuentos %": pc, "Comisión %": pc, "Compensaciones %": pc, "Publicidad %": pc, "Resultado %": pc})
    tot = g.sum()
    steps = [("Venta bruta", tot["bruta"]), ("Descuentos", -tot["desc_local"]), ("Comisión", -tot["comision"]),
             ("Otros cargos", -tot["otros"]), ("Compensaciones", -tot["compensaciones"]), ("Publicidad", -tot["ads"]),
             ("Ajustes/reintegros", -tot["ajustes"])]
    fig = go.Figure(go.Waterfall(x=[s_[0] for s_ in steps] + ["Resultado"], y=[s_[1] for s_ in steps] + [0],
                                 measure=["absolute"] + ["relative"] * (len(steps) - 1) + ["total"],
                                 increasing=dict(marker=dict(color=POS)), decreasing=dict(marker=dict(color=NEG)),
                                 totals=dict(marker=dict(color="#256abf")), connector=dict(line=dict(color=BASE, width=1)),
                                 text=[money(v) for v in [s_[1] for s_ in steps] + [tot["resultado"]]], textposition="outside"))
    fig.update_layout(title=dict(text="Cascada del período (todas las sucursales filtradas)", font=dict(size=14)))
    st.plotly_chart(chart_layout(fig, 340, False), use_container_width=True, theme="streamlit")
    st.caption("Uber Eats y Justo aparecerán aquí cuando se carguen sus reportes de ganancias / pedidos.")

    st.markdown("---\n##### Calidad: reclamos, cancelaciones y reseñas")
    q = _in(sql("""SELECT branch_id, platform_id, kind, reason, product, order_id FROM quality_events
                   WHERE business_date BETWEEN :a AND :b""", **pr))
    rv = _in(sql("""SELECT branch_id, platform_id, rating, reason FROM reviews WHERE business_date BETWEEN :a AND :b""", **pr))
    ops = _in(sql("""SELECT branch_id, platform_id, AVG(courier_wait_min) AS espera, AVG(prep_expected_min) AS prep,
                     COUNT(*) AS n FROM order_ops WHERE business_date BETWEEN :a AND :b
                     GROUP BY branch_id, platform_id""", **pr))
    qq = m.groupby("branch_id")["ordenes"].sum().to_frame()
    rec = q[q["kind"] == "reclamo"].groupby("branch_id")["order_id"].nunique()
    qq["Reclamos"] = rec
    qq["Reclamos %"] = qq["Reclamos"] / qq["ordenes"] * 100
    qq["Calificación"] = rv.groupby("branch_id")["rating"].mean()
    qq["Reseñas"] = rv.groupby("branch_id").size()
    qq["Reseñas malas %"] = rv[rv["rating"] <= 2].groupby("branch_id").size() / qq["Reseñas"] * 100
    qq["Espera repartidor (min)"] = ops.groupby("branch_id")["espera"].mean()
    qq.index = qq.index.map(BN_ALL)
    st.dataframe(qq.rename(columns={"ordenes": "Órdenes"}).round(2), use_container_width=True,
                 column_config={"Reclamos %": pc, "Reseñas malas %": pc})
    st.caption("Reclamos y espera del repartidor: Rappi (nivel de servicio). La espera solo incluye pedidos con demora "
               "informados por Rappi. Reseñas: calificación de 1 a 5.")
    c1, c2 = st.columns(2)
    with c1:
        r = q[q["kind"].isin(["reclamo"])].groupby("reason").size().sort_values()
        if not r.empty:
            fig = go.Figure(go.Bar(x=r.values, y=r.index, orientation="h", marker=dict(color=NEG, cornerradius=4),
                                   text=r.values, textposition="outside"))
            fig.update_layout(title=dict(text="Reclamos por motivo", font=dict(size=14)))
            st.plotly_chart(chart_layout(fig, 260, False), use_container_width=True, theme="streamlit")
    with c2:
        pr_ = q[q["kind"] == "reclamo_producto"].groupby("product").size().sort_values().tail(10)
        if not pr_.empty:
            fig = go.Figure(go.Bar(x=pr_.values, y=pr_.index, orientation="h", marker=dict(color=NEG, cornerradius=4),
                                   text=pr_.values, textposition="outside"))
            fig.update_layout(title=dict(text="Productos más reclamados", font=dict(size=14)))
            st.plotly_chart(chart_layout(fig, 300, False), use_container_width=True, theme="streamlit")

    st.markdown("---\n##### Embudo de conversión (por período)")
    f = sql("SELECT * FROM funnel_period WHERE start_date <= :b AND end_date >= :a ORDER BY start_date DESC", **pr)
    if f.empty:
        st.info("Aún no hay datos de embudo para el período. Uber entrega el embudo por período (no diario); "
                "Rappi solo muestra la tasa de conversión en pantalla, sin exportar visitas.")
    else:
        f["Tienda"] = f["branch_id"].map(lambda x: BN_ALL.get(x, "Todas (" + x.replace("ALL_", "") + ")"))
        f["Plataforma"] = f["platform_id"].map(PN_ALL)
        f["Menú/tienda %"] = f["menu_views"] / f["store_views"] * 100
        f["Carrito/menú %"] = f["add_to_cart"] / f["menu_views"] * 100
        f["Pedido/carrito %"] = f["orders"] / f["add_to_cart"] * 100
        f["CVR menú %"] = f["orders"] / f["menu_views"] * 100
        f["CVR total %"] = f["orders"] / f["store_views"] * 100
        st.dataframe(f[["Tienda", "Plataforma", "start_date", "end_date", "store_views", "menu_views", "add_to_cart",
                        "orders", "Menú/tienda %", "Carrito/menú %", "Pedido/carrito %", "CVR menú %", "CVR total %"]]
                     .rename(columns={"start_date": "Desde", "end_date": "Hasta", "store_views": "Vieron la tienda",
                                      "menu_views": "Vieron el menú", "add_to_cart": "Agregaron al carrito",
                                      "orders": "Pedidos"}).round(1),
                     hide_index=True, use_container_width=True,
                     column_config={k: pc for k in ["Menú/tienda %", "Carrito/menú %", "Pedido/carrito %", "CVR menú %",
                                                    "CVR total %"]})
        st.caption("Uber: 'Vieron la tienda' = usuarios que vieron la tienda en listados; CVR menú = pedidos / vistas "
                   "del menú (definición del portal).")


def page_ia():
    header("Analista IA")
    rep = ai_rep()
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    t1, t2 = st.tabs(["Informe del día", "Preguntale al analista"])
    with t1:
        if not rep.empty:
            st.caption(f"Generado {pd.to_datetime(rep.loc[0, 'generated_at']):%d/%m %H:%M} UTC")
            st.markdown(rep.loc[0, "content_md"])
        else:
            st.info("No hay informe para este día.")
        if has_key and st.button("Generar / regenerar informe"):
            from cci import analyst
            with st.spinner("El analista está revisando los números…"):
                analyst.daily_report(D, BRAND)
            st.cache_data.clear()
            st.rerun()
    with t2:
        if not has_key:
            st.warning("Configurar ANTHROPIC_API_KEY para habilitar las preguntas en lenguaje natural.")
            return
        st.caption("Ejemplos: ¿Por qué cayó la venta esta semana? · ¿Qué sucursal tiene peor conversión? · "
                   "¿Cuánto vamos a vender este mes? · ¿Dónde aumentaron los resarcimientos?")
        hist = st.session_state.setdefault("chat", [])
        for role, text in st.session_state.setdefault("chat_view", []):
            st.chat_message(role).markdown(text)
        q = st.chat_input("Escribí tu pregunta…")
        if q:
            from cci import analyst
            st.chat_message("user").markdown(q)
            with st.chat_message("assistant"), st.spinner("Consultando datos…"):
                ans, msgs = analyst.ask(f"[Marca seleccionada en el panel: {BRAND_NAME}] {q}", hist)
                st.markdown(ans)
            st.session_state["chat"] = msgs
            st.session_state["chat_view"] += [("user", q), ("assistant", ans)]


def page_cargar():
    header("Cargar reportes")
    st.markdown("Subí los reportes descargados de **Rappi Partners**, **Uber Eats Manager** o **Justo** (CSV / Excel), "
                "o las plantillas canónicas. El sistema detecta el formato por el nombre del archivo, evita "
                "duplicados y recalcula alertas.")
    ups = st.file_uploader("Archivos", type=["csv", "xlsx", "xls"], accept_multiple_files=True)
    if ups and st.button("Procesar archivos", type="primary"):
        from cci import alerts as al_mod, quality
        from cci.ingest import files
        inbox = config.DATA_DIR / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        for u in ups:
            (inbox / Path(u.name).name).write_bytes(u.getvalue())
        with st.spinner("Procesando…"):
            res = files.ingest_inbox()
            _, last = kpis.data_bounds()
            if last:
                quality.run_checks(last)
                al_mod.generate(last)
        st.dataframe(pd.DataFrame(res), hide_index=True, use_container_width=True)
        st.cache_data.clear()
        st.success("Listo. Los archivos 'sin mapeo' necesitan que configuremos su formato (ver docs).")
    st.markdown("##### Formatos reconocidos")
    from cci.ingest import files
    ms = files.load_mappings(include_unverified=True)
    st.dataframe(pd.DataFrame([{"Formato": m.get("name"), "Plataforma": m.get("platform"), "Datos": m.get("dataset"),
                                "Nombre de archivo": ", ".join(m.get("file_glob") or []) if isinstance(m.get("file_glob"), list) else m.get("file_glob"),
                                "Estado": "✅ activo" if m.get("verified") else "⏳ pendiente de archivo real"}
                               for m in ms if m.get("file_glob")]), hide_index=True, use_container_width=True)


# ====================================================================== VISTAS POR PLATAFORMA Y TIENDA
def _rr_by(df: pd.DataFrame, kind: str, group: str | None) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    r = kpis.run_rate(df, D, kind, group)
    return r.set_index("group")


def platform_overview():
    """Una fila por plataforma (+ total): ayer, semana, mes, proyecciones y comparación."""
    st.markdown("##### Por plataforma")
    rows = []
    plats = [p for p in PN if p in set(panel["platform_id"])]
    for key in plats + ["TOTAL"]:
        sub = panel if key == "TOTAL" else panel[panel["platform_id"] == key]
        f = lambda a, b: kpis.summarize(kpis.filt(sub, a, b))  # noqa: E731
        y, d7 = f(D, D), f(D - timedelta(days=7), D - timedelta(days=7))
        w, wp = f(*P.wtd), f(*P.wtd_prev)
        m, mp = f(*P.mtd), f(*P.prev_mtd)
        rw = kpis.run_rate(sub, D, "week").iloc[0]
        rm = kpis.run_rate(sub, D, "month").iloc[0]
        rows.append({"Plataforma": "Total" if key == "TOTAL" else PN[key],
                     "Ayer": y["sales"], "Δ ayer %": (kpis.pct(y["sales"], d7["sales"]) or 0) * 100,
                     "Semana acum.": w["sales"], "Δ sem %": (kpis.pct(w["sales"], wp["sales"]) or 0) * 100,
                     "Proy. semana": rw["sales_proj"], "vs sem. ant. %": (rw["vs_base_pct"] or 0) * 100,
                     "Mes acum.": m["sales"], "Δ mes %": (kpis.pct(m["sales"], mp["sales"]) or 0) * 100,
                     "Proy. mes": rm["sales_proj"], "Mes anterior": rm["base_sales"],
                     "vs mes ant. %": (rm["vs_base_pct"] or 0) * 100,
                     "Órdenes mes": m["orders"], "Ticket mes": m["ticket"]})
    t = pd.DataFrame(rows)
    money_cols = ["Ayer", "Semana acum.", "Proy. semana", "Mes acum.", "Proy. mes", "Mes anterior", "Ticket mes"]
    for c_ in money_cols:
        t[c_] = t[c_].round(0)
    pc = st.column_config.NumberColumn(format="%+.1f%%")
    loc = st.column_config.NumberColumn(format="localized")
    st.dataframe(t, hide_index=True, use_container_width=True,
                 column_config={**{c_: loc for c_ in money_cols + ["Órdenes mes"]},
                                **{c_: pc for c_ in ["Δ ayer %", "Δ sem %", "vs sem. ant. %", "Δ mes %", "vs mes ant. %"]}})
    st.caption("Δ ayer: vs mismo día de la semana anterior · Δ semana / mes: vs misma altura del período anterior · "
               "Proyección (run rate por día de semana) vs período anterior completo real.")
    tt = t[t["Plataforma"] != "Total"]
    if len(tt):
        fig = go.Figure()
        fig.add_bar(x=tt["Plataforma"], y=tt["Mes anterior"], name="Mes anterior (real)",
                    marker=dict(color=BASE, cornerradius=4), text=[money(v) for v in tt["Mes anterior"]],
                    textposition="outside")
        fig.add_bar(x=tt["Plataforma"], y=tt["Proy. mes"], name="Mes actual (proyección)",
                    marker=dict(color=[PLAT_COLOR.get(k, CUR) for k in plats], cornerradius=4),
                    text=[f"{money(v)} ({pctf(d / 100)})" for v, d in zip(tt["Proy. mes"], tt["vs mes ant. %"])],
                    textposition="outside")
        fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.1,
                          title=dict(text="Proyección del mes vs mes anterior, por plataforma", font=dict(size=14)))
        st.plotly_chart(chart_layout(fig, 300), use_container_width=True, theme="streamlit")


def _period_ranges(kind: str):
    return (P.wtd, P.wtd_prev) if kind == "Semana" else (P.mtd, P.prev_mtd)


def store_platform_section():
    st.markdown("---\n##### Tienda × plataforma")
    kind = st.radio("Comparar", ["Semana", "Mes"], horizontal=True, key="sp_kind",
                    help="Semana: lunes a ayer vs mismos días de la semana anterior. Mes: 1 a ayer vs mismos días "
                         "del mes anterior. Proyección vs período anterior completo.")
    cur_r, base_r = _period_ranges(kind)
    rr = {}
    plats = [p for p in PN if p in set(panel["platform_id"])]
    rows = []
    for b in [x for x in BN if x in SB]:
        row = {"Tienda": BN[b]}
        tot_c = tot_b = 0.0
        for p in plats:
            sub = panel[(panel["branch_id"] == b) & (panel["platform_id"] == p)]
            c = kpis.summarize(kpis.filt(sub, *cur_r))["sales"]
            bb = kpis.summarize(kpis.filt(sub, *base_r))["sales"]
            tot_c += c
            tot_b += bb
            pr = kpis.run_rate(sub, D, "week" if kind == "Semana" else "month").iloc[0] if not sub.empty else None
            row[f"{PN[p]}"] = round(c)
            row[f"{PN[p]} Δ%"] = (kpis.pct(c, bb) or 0) * 100 if bb else None
            row[f"{PN[p]} proy. vs ant.%"] = (pr["vs_base_pct"] or 0) * 100 if pr is not None and pr["base_sales"] else None
        row["Total"] = round(tot_c)
        row["Total Δ%"] = (kpis.pct(tot_c, tot_b) or 0) * 100 if tot_b else None
        rows.append(row)
    t = pd.DataFrame(rows)
    cfg = {c_: st.column_config.NumberColumn(format="%+.1f%%") for c_ in t.columns if "%" in c_}
    cfg.update({c_: st.column_config.NumberColumn(format="localized") for c_ in t.columns
                if c_ not in cfg and c_ != "Tienda"})
    st.dataframe(t, hide_index=True, use_container_width=True, column_config=cfg)

    # ¿qué plataforma explica el cambio de cada tienda?
    fig = go.Figure()
    for p in plats:
        deltas = []
        for b in [x for x in BN if x in SB]:
            sub = panel[(panel["branch_id"] == b) & (panel["platform_id"] == p)]
            deltas.append(kpis.summarize(kpis.filt(sub, *cur_r))["sales"] - kpis.summarize(kpis.filt(sub, *base_r))["sales"])
        fig.add_bar(y=[BN[x] for x in BN if x in SB], x=deltas, name=PN[p], orientation="h",
                    marker=dict(color=PLAT_COLOR[p], cornerradius=3),
                    hovertemplate="%{y} · " + PN[p] + ": %{x:,.0f}<extra></extra>")
    fig.update_layout(barmode="relative", title=dict(text=f"¿Qué plataforma explica el cambio de cada tienda? "
                                                          f"(Δ venta, {kind.lower()} vs anterior)", font=dict(size=14)))
    fig.add_vline(x=0, line_color=BASE, line_width=1)
    st.plotly_chart(chart_layout(fig, 320), use_container_width=True, theme="streamlit")
    store_drilldown(kind)


def _q(sql_, **kw):
    try:
        return sql(sql_, **kw)
    except Exception:
        return pd.DataFrame()


def store_drilldown(kind: str):
    st.markdown("##### Diagnóstico de una tienda")
    stores = [x for x in BN if x in SB]
    if not stores:
        return
    b = st.selectbox("Tienda", stores, format_func=BN.get, key="drill_store")
    cur_r, base_r = _period_ranges(kind)
    rows, lines = [], []
    tot_delta = 0.0
    pr = {"a": cur_r[0].isoformat(), "b": cur_r[1].isoformat(), "c": base_r[0].isoformat(), "d": base_r[1].isoformat(),
          "br": b}
    for p in [x for x in PN if x in set(panel["platform_id"])]:
        sub = panel[(panel["branch_id"] == b) & (panel["platform_id"] == p)]
        c, bb = kpis.summarize(kpis.filt(sub, *cur_r)), kpis.summarize(kpis.filt(sub, *base_r))
        if not c["orders"] and not bb["orders"]:
            continue
        d = c["sales"] - bb["sales"]
        tot_delta += d
        # efecto volumen (órdenes) vs efecto ticket (LMDI)
        eff_o = eff_t = None
        if c["orders"] and bb["orders"] and c["sales"] and bb["sales"] and c["sales"] != bb["sales"]:
            import math
            L = d / (math.log(c["sales"]) - math.log(bb["sales"]))
            eff_o, eff_t = L * math.log(c["orders"] / bb["orders"]), L * math.log(c["ticket"] / bb["ticket"])
        q = _q("""SELECT SUM(CASE WHEN business_date BETWEEN :a AND :b THEN 1 ELSE 0 END) cur,
                         SUM(CASE WHEN business_date BETWEEN :c AND :d THEN 1 ELSE 0 END) prev
                  FROM quality_events WHERE branch_id=:br AND platform_id=:p AND kind='reclamo'""", p=p, **pr)
        rows.append({"Plataforma": PN[p], "Venta": round(c["sales"]), "Venta anterior": round(bb["sales"]),
                     "Δ venta": round(d), "Δ %": (kpis.pct(c["sales"], bb["sales"]) or 0) * 100,
                     "Órdenes Δ %": (kpis.pct(c["orders"], bb["orders"]) or 0) * 100,
                     "Ticket Δ %": (kpis.pct(c["ticket"], bb["ticket"]) or 0) * 100,
                     "Por menos/más órdenes": round(eff_o) if eff_o is not None else None,
                     "Por ticket": round(eff_t) if eff_t is not None else None,
                     "Cancelaciones": int(c["cancelled_orders"]),
                     "Min. fuera de línea": c["unavailable_minutes"] if c["unavailable_minutes"] is not None else None,
                     "Reclamos": int(q["cur"].iloc[0] or 0) if not q.empty else None})
    if not rows:
        st.info("Sin datos para esta tienda en el período.")
        return
    t = pd.DataFrame(rows)
    pc = st.column_config.NumberColumn(format="%+.1f%%")
    loc = st.column_config.NumberColumn(format="localized")
    st.dataframe(t, hide_index=True, use_container_width=True,
                 column_config={"Venta": loc, "Venta anterior": loc, "Δ venta": loc, "Por menos/más órdenes": loc,
                                "Por ticket": loc, "Δ %": pc, "Órdenes Δ %": pc, "Ticket Δ %": pc})
    # explicación en palabras
    tot_prev = t["Venta anterior"].sum()
    word = "sube" if tot_delta >= 0 else "baja"
    lines.append(f"**{BN[b]} {word} {pctf(tot_delta / tot_prev if tot_prev else None)} ({money(tot_delta)})** "
                 f"en la {'semana' if kind == 'Semana' else 'parte del mes'} vs el mismo período anterior.")
    for r in t.sort_values("Δ venta", key=lambda s_: s_ * (1 if tot_delta < 0 else -1)).to_dict("records"):
        share = r["Δ venta"] / tot_delta * 100 if tot_delta else 0
        eo, et = r["Por menos/más órdenes"], r["Por ticket"]
        if eo is not None and et is not None and not (pd.isna(eo) or pd.isna(et)):
            why = ("menos pedidos" if eo < 0 else "más pedidos") if abs(eo) >= abs(et) else \
                  ("un ticket más bajo" if et < 0 else "un ticket más alto")
        else:
            why = "volumen"
        lines.append(f"- **{r['Plataforma']}**: {money(r['Δ venta'])} ({pctf(r['Δ %'] / 100)}), explica el "
                     f"{share:.0f}% del cambio; principalmente por {why} (pedidos {pctf(r['Órdenes Δ %'] / 100)}, "
                     f"ticket {pctf(r['Ticket Δ %'] / 100)}).")
    f = _q("SELECT * FROM funnel_period WHERE branch_id=:br ORDER BY end_date DESC", br=b)
    if not f.empty:
        r = f.iloc[0]
        if pd.notna(r["menu_views"]) and r["menu_views"]:
            lines.append(f"- Embudo {PN.get(r['platform_id'], r['platform_id'])} ({pd.to_datetime(r['start_date']):%d/%m}–"
                         f"{pd.to_datetime(r['end_date']):%d/%m}): {num(r['store_views'])} vieron la tienda → "
                         f"{num(r['menu_views'])} el menú ({pctf(r['menu_views'] / r['store_views'], False)}) → "
                         f"{num(r['orders'])} pedidos (CVR menú {pctf(r['orders'] / r['menu_views'], False)}).")
    st.markdown("\n".join(lines))
    st.caption("'Por menos/más órdenes' y 'Por ticket' reparten el Δ venta entre volumen y ticket (suman el total). "
               "Sesiones/conversión diarias no están disponibles en las plataformas; ver pantalla Embudo y conversión.")


def page_embudo():
    header("Embudo y conversión")
    st.markdown("Qué tan bien convertimos las visitas en pedidos, por plataforma y tienda.")
    f = sql("SELECT * FROM funnel_period ORDER BY end_date DESC, platform_id")
    if f.empty:
        st.info("Sin datos de embudo cargados.")
        return
    allb = f"ALL_{BRAND}" if BRAND else None
    f = f[f["branch_id"].isin(list(SB) + ([allb] if allb else []))]
    if sel_p:
        f = f[f["platform_id"].isin(sel_p)]
    f["Tienda"] = f["branch_id"].map(lambda x: BN_ALL.get(x, "Todas las tiendas"))
    f["Plataforma"] = f["platform_id"].map(PN_ALL)
    f["Período"] = pd.to_datetime(f["start_date"]).dt.strftime("%d/%m/%y") + " – " + \
        pd.to_datetime(f["end_date"]).dt.strftime("%d/%m/%y")
    f["% abre el menú"] = f["menu_views"] / f["store_views"] * 100
    f["% agrega al carrito"] = f["add_to_cart"] / f["menu_views"] * 100
    f["% compra (del carrito)"] = f["orders"] / f["add_to_cart"] * 100
    f["CVR menú %"] = (f["orders"] / f["menu_views"] * 100).fillna(f["conversion_reported"] * 100)
    f["CVR total %"] = f["orders"] / f["store_views"] * 100

    ub = f[(f["platform_id"] == "ubereats") & f["menu_views"].notna()]
    if not ub.empty:
        last_end = ub["end_date"].max()
        cur = ub[(ub["end_date"] == last_end) & ~ub["branch_id"].str.startswith("ALL_")].sort_values("CVR menú %")
        st.markdown(f"##### Uber Eats · embudo por tienda ({cur['Período'].iloc[0] if len(cur) else ''})")
        if len(cur):
            c1, c2 = st.columns(2)
            for col, m_, title in ((c1, "% abre el menú", "Visibilidad: % que abre el menú tras ver la tienda"),
                                   (c2, "CVR menú %", "Conversión: pedidos / vistas del menú")):
                d_ = cur.sort_values(m_)
                avg = ub[(ub["end_date"] == last_end) & ub["branch_id"].str.startswith("ALL_")]
                fig = go.Figure(go.Bar(x=d_[m_], y=d_["Tienda"], orientation="h",
                                       marker=dict(color=PLAT_COLOR["ubereats"], cornerradius=4),
                                       text=[f"{v:.1f}%" for v in d_[m_]], textposition="outside"))
                if not avg.empty:
                    fig.add_vline(x=float(avg[m_].iloc[0]), line_dash="dash", line_color=BASE,
                                  annotation_text=f"Kobo {float(avg[m_].iloc[0]):.1f}%", annotation_position="top")
                fig.update_layout(title=dict(text=title, font=dict(size=14)))
                fig.update_xaxes(ticksuffix="%", range=[0, float(d_[m_].max()) * 1.25])
                col.plotly_chart(chart_layout(fig, 300, False), use_container_width=True, theme="streamlit")
            st.caption("Valores de Las Condes, Manuel Montt y total: CSV exacto del portal. Resto: leídos en pantalla "
                       "(redondeados a miles).")
    show = f[["Plataforma", "Tienda", "Período", "store_views", "menu_views", "add_to_cart", "orders",
              "% abre el menú", "% agrega al carrito", "% compra (del carrito)", "CVR menú %", "CVR total %"]].rename(
        columns={"store_views": "Vieron la tienda (sesiones)", "menu_views": "Vieron el menú",
                 "add_to_cart": "Agregaron al carrito", "orders": "Pedidos"})
    pc = st.column_config.NumberColumn(format="%.1f%%")
    loc = st.column_config.NumberColumn(format="localized")
    st.markdown("##### Detalle")
    st.dataframe(show.round(1), hide_index=True, use_container_width=True,
                 column_config={**{c_: pc for c_ in show.columns if "%" in c_},
                                **{c_: loc for c_ in ["Vieron la tienda (sesiones)", "Vieron el menú",
                                                      "Agregaron al carrito", "Pedidos"]}})
    with st.expander("¿Por qué no hay sesiones diarias? ¿Cómo completar el embudo?"):
        st.markdown("""
- **Uber Eats** entrega el embudo (vieron la tienda → menú → carrito → pedido) solo por **período**, no por día.
  Para ver la evolución semanal se descarga el embudo de cada tienda semana a semana (ver `docs/08_embudo_semanal.md`).
- **Rappi** no exporta visitas: solo muestra en pantalla la *tasa de conversión* por tienda; se registra en la
  plantilla `plantilla_embudo.csv` (columna conversion_pct).
- **Justo** no tiene visitas en su panel: requiere Google Analytics en la tienda web.
""")


pages = [
    st.Page(page_resumen, title="Resumen ejecutivo", icon="🏠", url_path="resumen", default=True),
    st.Page(lambda: (page_sucursales(), store_platform_section(), _sucursales_extra()), title="Sucursales", icon="🏪", url_path="sucursales"),
    st.Page(page_plataformas, title="Plataformas", icon="📱", url_path="plataformas"),
    st.Page(lambda: (page_mix(), uber_product_section()), title="Mix de productos", icon="🍔", url_path="mix"),
    st.Page(lambda: (page_semanal(), hourly_section()), title="Análisis semanal", icon="📅", url_path="semanal"),
    st.Page(page_mensual, title="Análisis mensual", icon="🗓️", url_path="mensual"),
    st.Page(page_embudo, title="Embudo y conversión", icon="🔎", url_path="embudo"),
    st.Page(page_rentabilidad, title="Rentabilidad y calidad", icon="💰", url_path="rentabilidad"),
    st.Page(page_alertas, title="Alertas", icon="🚨", url_path="alertas"),
    st.Page(page_ia, title="Analista IA", icon="🧠", url_path="analista"),
    st.Page(page_cargar, title="Cargar reportes", icon="📤", url_path="cargar"),
]
st.navigation(pages).run()
