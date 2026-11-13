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
from cci import config, db, kpis  # noqa: E402

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
BN, PN = config.branch_names(), config.platform_names()
SALES_LABEL = "Venta bruta" if kpis.sales_col() == "gross" else "Venta neta"

if dmax is None:
    st.title("Centro de Control Comercial")
    st.warning("Todavía no hay datos cargados. Ver README: cargar plantillas, conectar fuentes o "
               "ejecutar `python -m cci.cli demo` para ver una demostración.")
    st.stop()

# ------------------------------------------------------------------ filtros (barra lateral)
with st.sidebar:
    st.markdown(f"### {config.settings()['business']['name']}")
    D = st.date_input("Día analizado (cierre)", value=dmax, min_value=dmin, max_value=dmax, format="DD/MM/YYYY")
    sel_b = st.multiselect("Sucursales", list(BN), format_func=BN.get, placeholder="Todas")
    sel_p = st.multiselect("Plataformas", list(PN), format_func=PN.get, placeholder="Todas")
    st.caption(f"Datos disponibles: {dmin:%d/%m/%Y} – {dmax:%d/%m/%Y}")
    last_run = sql("SELECT MAX(finished_at) AS t FROM ingestion_run WHERE status IN ('ok','warning')")
    if not last_run.empty and pd.notna(last_run.loc[0, "t"]):
        st.caption(f"Última actualización: {pd.to_datetime(last_run.loc[0, 't']):%d/%m/%Y %H:%M} UTC")
    if st.button("↻ Recargar datos"):
        st.cache_data.clear()
        st.rerun()

P = kpis.Periods(D)
panel = panel_for(D, tuple(sel_b), tuple(sel_p))
F = lambda a, b: kpis.summarize(kpis.filt(panel, a, b))  # noqa: E731
scope = " · ".join(filter(None, [", ".join(BN[b] for b in sel_b) if sel_b else "6 sucursales",
                                 ", ".join(PN[p] for p in sel_p) if sel_p else "3 plataformas"]))


DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]


def header(title: str, subtitle: str = ""):
    if DEMO:
        st.markdown('<div class="demo-banner">⚠ DATOS DE DEMOSTRACIÓN · sintéticos, no corresponden al negocio real</div>',
                    unsafe_allow_html=True)
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


def load_alerts() -> pd.DataFrame:
    a = sql("SELECT * FROM alert WHERE business_date = :d", d=D.isoformat())
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
        rep = sql("SELECT content_md, model, generated_at FROM ai_report WHERE business_date = :d", d=D.isoformat())
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
    cur = mix_for(*cur_r, tuple(sel_b), tuple(sel_p))
    base = mix_for(*base_r, tuple(sel_b), tuple(sel_p))
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


def page_ia():
    header("Analista IA")
    rep = sql("SELECT content_md, model, generated_at FROM ai_report WHERE business_date = :d", d=D.isoformat())
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
                analyst.daily_report(D)
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
                ans, msgs = analyst.ask(q, hist)
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


pages = [
    st.Page(page_resumen, title="Resumen ejecutivo", icon="🏠", url_path="resumen", default=True),
    st.Page(page_sucursales, title="Sucursales", icon="🏪", url_path="sucursales"),
    st.Page(page_plataformas, title="Plataformas", icon="📱", url_path="plataformas"),
    st.Page(page_mix, title="Mix de productos", icon="🍔", url_path="mix"),
    st.Page(page_semanal, title="Análisis semanal", icon="📅", url_path="semanal"),
    st.Page(page_mensual, title="Análisis mensual", icon="🗓️", url_path="mensual"),
    st.Page(page_alertas, title="Alertas", icon="🚨", url_path="alertas"),
    st.Page(page_ia, title="Analista IA", icon="🧠", url_path="analista"),
    st.Page(page_cargar, title="Cargar reportes", icon="📤", url_path="cargar"),
]
st.navigation(pages).run()
