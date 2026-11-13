"""Motor de alertas ROJO / AMARILLO / VERDE.

Línea base por defecto: promedio del MISMO día de la semana en las últimas N semanas
(config alerts.baseline_weeks). Es más robusto que comparar contra un solo día.
Cada alerta responde: qué pasó, dónde (sucursal/plataforma), magnitud, contra qué
período se compara y qué acción se recomienda.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from . import config, db, kpis

LEVEL_ORDER = {"ROJO": 0, "AMARILLO": 1, "VERDE": 2}


def gate(level, z, red_z=3.0, yellow_z=2.0):
    """Filtro de ruido estadístico: con pocas órdenes por día, un -20% puede ser puro azar.
    ROJO exige |z| >= 3 y AMARILLO |z| >= 2; si no alcanza, se degrada o descarta."""
    if level is None or z is None:
        return level
    az = abs(z)
    if level == "ROJO":
        return "ROJO" if az >= red_z else ("AMARILLO" if az >= yellow_z else None)
    if level in ("AMARILLO", "VERDE"):
        return level if az >= yellow_z else None
    return level


def z_count(obs, exp):
    """z de Poisson para conteos (órdenes, unidades)."""
    if exp is None or exp <= 0 or obs is None:
        return None
    return (obs - exp) / exp ** 0.5


def z_amount(obs, base, n_base_orders, cv=0.5):
    """z aproximado para montos = órdenes x ticket (ticket con coeficiente de variación cv)."""
    if not base or not n_base_orders or obs is None:
        return None
    se = base * ((1 + cv ** 2) / n_base_orders) ** 0.5
    return (obs - base) / se


def _fmt_money(v) -> str:
    return "-" if v is None or pd.isna(v) else f"${v:,.0f}".replace(",", ".")


def _fmt_pct(v, signed=True) -> str:
    if v is None or pd.isna(v):
        return "-"
    return f"{v * 100:+.1f}%" if signed else f"{v * 100:.1f}%"


class AlertEngine:
    def __init__(self, D: date):
        self.D = D
        self.cfg = config.settings()["alerts"]
        self.bn = config.branch_names()
        self.pn = config.platform_names()
        self.weeks = int(self.cfg["baseline_weeks"])
        P = kpis.Periods(D)
        s, e = P.load_range
        self.panel = kpis.load_panel(s, e)
        self.panel["sales"] = self.panel[kpis.sales_col()]
        self.data_start, _ = kpis.data_bounds()
        self.base_dates = [D - timedelta(days=7 * k) for k in range(1, self.weeks + 1)
                           if self.data_start and D - timedelta(days=7 * k) >= self.data_start]
        self.cmp_label = f"promedio de los últimos {len(self.base_dates)} {self._dow_name()} (mismo día de semana)"
        self.alerts: list[dict] = []

    def _dow_name(self) -> str:
        return ["lunes", "martes", "miércoles", "jueves", "viernes", "sábados", "domingos"][self.D.weekday()]

    # ------------------------------------------------------------------ helpers
    def _cur(self, sub):
        return kpis.summarize(sub[sub["business_date"] == self.D])

    def _base(self, sub):
        """Resumen promedio de los días base (montos /n; tasas desde sumas)."""
        n = len(self.base_dates)
        s = kpis.summarize(sub[sub["business_date"].isin(self.base_dates)])
        if n:
            for k in kpis.ADDITIVE + ["sales"]:
                if s.get(k) is not None:
                    s[k] = s[k] / n
        return s

    def add(self, level, code, branch, platform, title, what, magnitude, comparison, rec):
        self.alerts.append({"level": level, "code": code, "branch_id": branch, "platform_id": platform,
                            "title": title, "what": what, "magnitude": magnitude, "comparison": comparison,
                            "recommendation": rec})

    def _lvl_drop(self, d, th):
        if d is None:
            return None
        if d <= th["red"]:
            return "ROJO"
        if d <= th["yellow"]:
            return "AMARILLO"
        if "green" in th and d >= th["green"]:
            return "VERDE"
        return None

    # ------------------------------------------------------------------ reglas
    def rule_branch_sales(self):
        th = self.cfg["sales_drop"]
        for b, sub in self.panel.groupby("branch_id"):
            c, base = self._cur(sub), self._base(sub)
            if (base["sales"] or 0) < self.cfg["min_daily_sales_for_alert"]:
                continue
            d = kpis.pct(c["sales"], base["sales"])
            lvl = gate(self._lvl_drop(d, th), z_amount(c["sales"], base["sales"], base["orders"]))
            if not lvl:
                continue
            # ¿qué plataforma lo explica?
            parts = []
            for p, sp in sub.groupby("platform_id"):
                cp, bp = self._cur(sp), self._base(sp)
                parts.append((cp["sales"] - (bp["sales"] or 0), p, kpis.decompose(cp, bp)))
            parts.sort(key=lambda x: x[0] if lvl != "VERDE" else -x[0])
            delta_p, p_main, dec = parts[0]
            expl = f" La plataforma que más explica el cambio es {self.pn.get(p_main)} ({_fmt_money(delta_p)})."
            if dec:
                factor = max(("sesiones", "conversion", "ticket"), key=lambda k: abs(dec[k]))
                expl += (f" En {self.pn.get(p_main)} el factor dominante es {factor} "
                         f"({_fmt_pct(dec['pct'].get(factor))}).")
            if lvl == "VERDE":
                self.add(lvl, "venta_sucursal_alza", b, None, f"{self.bn[b]}: venta {_fmt_pct(d)} sobre su promedio",
                         f"Venta del {self.D}: {_fmt_money(c['sales'])} vs {_fmt_money(base['sales'])}.{expl}",
                         _fmt_pct(d), self.cmp_label,
                         "Identificar qué funcionó (promoción, visibilidad, clima, evento) y replicarlo en otras sucursales.")
            else:
                self.add(lvl, "venta_sucursal_baja", b, None, f"{self.bn[b]}: venta {_fmt_pct(d)} bajo su promedio",
                         f"Venta del {self.D}: {_fmt_money(c['sales'])} vs {_fmt_money(base['sales'])}.{expl}",
                         f"{_fmt_pct(d)} ({_fmt_money(c['sales'] - base['sales'])})", self.cmp_label,
                         "Revisar si el local estuvo pausado/cerrado, quiebres de stock y visibilidad en la plataforma "
                         "que explica la caída; contactar al encargado hoy.")

    def rule_branch_platform_ops(self):
        for (b, p), sub in self.panel.groupby(["branch_id", "platform_id"]):
            c, base = self._cur(sub), self._base(sub)
            where = f"{self.bn[b]} · {self.pn.get(p)}"
            # sesiones
            if c["sessions"] is not None and base["sessions"]:
                d = kpis.pct(c["sessions"], base["sessions"])
                lvl = self._lvl_drop(d, self.cfg["sessions_drop"])
                if lvl in ("ROJO", "AMARILLO"):
                    self.add(lvl, "sesiones_baja", b, p, f"{where}: sesiones {_fmt_pct(d)}",
                             f"Sesiones {c['sessions']:,.0f} vs {base['sessions']:,.0f} promedio. Menos tráfico "
                             "significa menos órdenes aunque la conversión se mantenga.",
                             _fmt_pct(d), self.cmp_label,
                             "Verificar que la tienda aparezca abierta y bien posicionada; revisar si terminó una "
                             "promoción o campaña de ads, cambios de radio de entrega o calificación.")
            # conversión
            if (c["conversion"] is not None and base["conversion"]
                    and (c["sessions"] or 0) >= self.cfg["min_sessions_for_conversion"]):
                d = kpis.pct(c["conversion"], base["conversion"])
                z = z_count(c["orders_s"], c["sessions"] * base["conversion"])
                lvl = gate(self._lvl_drop(d, self.cfg["conversion_drop"]), z)
                if lvl in ("ROJO", "AMARILLO"):
                    self.add(lvl, "conversion_baja", b, p, f"{where}: conversión {_fmt_pct(d)}",
                             f"Conversión {_fmt_pct(c['conversion'], False)} vs {_fmt_pct(base['conversion'], False)}. "
                             "El tráfico llega pero compra menos.",
                             f"{(c['conversion'] - base['conversion']) * 100:+.2f} pp", self.cmp_label,
                             "Revisar productos agotados/pausados, precios, fotos, tiempos de entrega mostrados y "
                             "comparar promociones contra la competencia en la zona.")
            # disponibilidad
            if c["availability"] is not None:
                th = self.cfg["availability"]
                lvl = "ROJO" if c["availability"] < th["red"] else "AMARILLO" if c["availability"] < th["yellow"] else None
                if lvl:
                    self.add(lvl, "open_time_bajo", b, p, f"{where}: disponible {_fmt_pct(c['availability'], False)} del horario",
                             f"Open Time {c['open_minutes']:,.0f} de {c['scheduled_minutes']:,.0f} min programados "
                             f"({c['unavailable_minutes']:,.0f} min sin recibir pedidos).",
                             f"{c['unavailable_minutes']:,.0f} min fuera de línea",
                             f"horario programado del {self.D} (umbral {th['yellow'] * 100:.0f}%)",
                             "Averiguar quién pausó la tienda y por qué (saturación, falta de personal, tablet "
                             "desconectada). Cada hora fuera de línea es venta perdida.")
            # cancelaciones
            th = self.cfg["cancel_rate"]
            if c["cancelled_orders"] >= th["min_orders"] and c["cancel_rate"] is not None:
                br = base["cancel_rate"] or 0
                if c["cancel_rate"] >= th["red_abs"] or (br and c["cancel_rate"] >= br * th["red_multiple"]):
                    lvl = "ROJO"
                elif br and c["cancel_rate"] >= br * th["yellow_multiple"]:
                    lvl = "AMARILLO"
                else:
                    lvl = None
                exp = max(br, 0.01) * (c["orders"] + c["cancelled_orders"])
                lvl = gate(lvl, z_count(c["cancelled_orders"], exp))
                if lvl:
                    self.add(lvl, "cancelaciones_alza", b, p, f"{where}: {c['cancelled_orders']:.0f} cancelaciones",
                             f"Tasa de cancelación {_fmt_pct(c['cancel_rate'], False)} vs {_fmt_pct(br, False)}; "
                             f"venta afectada {_fmt_money(c['cancelled_amount'])}.",
                             _fmt_pct(c["cancel_rate"], False), self.cmp_label,
                             "Revisar motivos de cancelación (sin stock, demora, rechazo del local) y el tiempo de "
                             "aceptación de pedidos.")
            # ticket
            if c["orders"] >= 10 and base["ticket"]:
                d = kpis.pct(c["ticket"], base["ticket"])
                se = base["ticket"] * float(self.cfg.get("ticket_cv", 0.5)) / c["orders"] ** 0.5
                lvl = gate(self._lvl_drop(d, self.cfg["ticket_drop"]), (c["ticket"] - base["ticket"]) / se)
                if lvl in ("ROJO", "AMARILLO"):
                    self.add(lvl, "ticket_baja", b, p, f"{where}: ticket medio {_fmt_pct(d)}",
                             f"Ticket {_fmt_money(c['ticket'])} vs {_fmt_money(base['ticket'])}.",
                             _fmt_pct(d), self.cmp_label,
                             "Revisar si hay una promoción de bajo ticket, combos agotados o cambio de precios; "
                             "evaluar sugerencias de adicionales.")

    def rule_refunds(self):
        th = self.cfg["refunds_rate"]
        for keys, label in ((["branch_id"], "sucursal"), (["branch_id", "platform_id"], "sucursal_plataforma")):
            for k, sub in self.panel.groupby(keys):
                b, p = (k if isinstance(k, tuple) else (k,)) + ((None,) if len(keys) == 1 else ())
                c, base = self._cur(sub), self._base(sub)
                if c["adj_amount"] < th["min_amount"] or not c["gross"]:
                    continue
                br = base["adj_rate"] or 0
                rate = c["adj_rate"]
                if rate >= th["red_abs"] or (br and rate >= br * th["red_multiple"]):
                    lvl = "ROJO"
                elif br and rate >= br * th["yellow_multiple"]:
                    lvl = "AMARILLO"
                else:
                    continue
                if label == "sucursal_plataforma" and lvl != "ROJO":
                    continue  # evitar ruido: a nivel plataforma sólo los rojos
                where = self.bn[b] + (f" · {self.pn.get(p)}" if p else "")
                reasons = ""
                if p:
                    r = db.read_df("""SELECT reason, COUNT(*) n, SUM(amount) m FROM adjustments
                                      WHERE business_date=:d AND branch_id=:b AND platform_id=:p
                                      GROUP BY reason ORDER BY m DESC""",
                                   {"d": self.D.isoformat(), "b": b, "p": p})
                    if not r.empty and r["reason"].notna().any():
                        reasons = " Motivos: " + "; ".join(f"{x.reason} ({x.n})" for x in r.head(3).itertuples()) + "."
                self.add(lvl, "resarcimientos_alza", b, p, f"{where}: resarcimientos {_fmt_pct(rate, False)} de la venta",
                         f"{c['adj_count']:.0f} resarcimiento(s) por {_fmt_money(c['adj_amount'])} "
                         f"(base {_fmt_pct(br, False)} de la venta).{reasons}",
                         f"{_fmt_money(c['adj_amount'])} · x{(rate / br) if br else float('nan'):.1f} vs base",
                         self.cmp_label,
                         "Revisar las órdenes reclamadas (productos faltantes, errores de armado, demoras, "
                         "empaque), reforzar checklist de despacho y disputar los reclamos improcedentes.")

    def rule_products(self):
        th = self.cfg["product_missing"]
        mix_hist = kpis.product_mix(self.D - timedelta(days=28), self.D - timedelta(days=1))
        mix_today = kpis.product_mix(self.D, self.D)
        if mix_hist.empty:
            return
        n_days = max(1, min(28, (self.D - self.data_start).days)) if self.data_start else 28
        for b, h in mix_hist.groupby("branch_id"):
            top = h.groupby("product")[["units", "revenue"]].sum().sort_values("revenue", ascending=False).head(th["top_n"])
            today = mix_today[mix_today["branch_id"] == b].groupby("product")["units"].sum()
            share = h.groupby("product")["units"].sum() / h["units"].sum()
            for prod, r in top.iterrows():
                avg = r["units"] / n_days
                expected = float(share.get(prod, 0)) * float(today.sum())  # unidades esperadas hoy
                # P(0 unidades | esperado) = e^-esperado: < 1% amarillo, < 0,1% rojo
                if avg >= th["min_avg_units"] and today.get(prod, 0) == 0 and expected >= 4.6:
                    lvl = "ROJO" if expected >= 6.9 else "AMARILLO"
                    self.add(lvl, "producto_sin_venta", b, None, f"{self.bn[b]}: '{prod}' no se vendió",
                             f"Producto top de la sucursal (promedio {avg:.1f} u/día; con el volumen de hoy se "
                             f"esperaban ~{expected:.0f}) con 0 unidades el {self.D}.",
                             f"-{avg:.1f} u/día", "promedio diario de los últimos 28 días",
                             "Verificar si está pausado/agotado en alguna plataforma o hubo quiebre de stock.")

    def rule_platform_wtd(self):
        P = kpis.Periods(self.D)
        th = self.cfg["platform_wtd"]
        for p, sub in self.panel.groupby("platform_id"):
            c, b = kpis.summarize(kpis.filt(sub, *P.wtd)), kpis.summarize(kpis.filt(sub, *P.wtd_prev))
            d = kpis.pct(c["sales"], b["sales"])
            lvl = self._lvl_drop(d, th)
            if not lvl:
                continue
            dec = kpis.decompose(c, b)
            expl = ""
            if dec:
                expl = (" Descomposición: sesiones {s}, conversión {cv}, ticket {t}.".format(
                    s=_fmt_pct(dec["pct"]["sesiones"]), cv=_fmt_pct(dec["pct"]["conversion"]),
                    t=_fmt_pct(dec["pct"]["ticket"])))
            word = "crece" if lvl == "VERDE" else "cae"
            self.add(lvl, "plataforma_semana", None, p, f"{self.pn.get(p)} {word} {_fmt_pct(d)} en la semana",
                     f"Acumulado semana {_fmt_money(c['sales'])} vs {_fmt_money(b['sales'])} a la misma altura "
                     f"de la semana anterior.{expl}", _fmt_pct(d),
                     f"semana actual {P.wtd[0]}..{P.wtd[1]} vs {P.wtd_prev[0]}..{P.wtd_prev[1]}",
                     "Si cae: revisar disponibilidad, posicionamiento y promociones activas en esa plataforma. "
                     "Si crece: evaluar mover inversión promocional hacia ella.")

    def rule_run_rate(self):
        P = kpis.Periods(self.D)
        if (self.D - P.week_start).days < 1:
            return  # con un solo día la proyección es demasiado ruidosa
        th = self.cfg["run_rate_vs_goal"]
        targets = config.settings().get("targets", {}).get("weekly", {}) or {}
        rr = kpis.run_rate(self.panel, self.D, "week", "branch_id")
        for r in rr.itertuples():
            goal = targets.get(r.group)
            ref, ref_label = (goal, "el objetivo semanal") if goal else (r.base_sales, "la venta real de la semana anterior")
            if not ref:
                continue
            ratio = r.sales_proj / ref
            lvl = "ROJO" if ratio < th["red"] else "AMARILLO" if ratio < th["yellow"] else "VERDE" if ratio > th["green"] else None
            if not lvl:
                continue
            self.add(lvl, "run_rate_semana", r.group, None,
                     f"{self.bn[r.group]}: proyección semanal al {ratio * 100:.0f}% frente a {ref_label}",
                     f"Proyección de cierre {_fmt_money(r.sales_proj)} (método: {r.method}) vs {_fmt_money(ref)}.",
                     f"{(ratio - 1) * 100:+.1f}%", ref_label,
                     "Quedan días para recuperar: activar promoción táctica o reforzar disponibilidad en la "
                     "plataforma más débil de la sucursal." if lvl != "VERDE"
                     else "Mantener la ejecución; revisar si hay capacidad para captar más demanda.")

    # ------------------------------------------------------------------ orquestación
    def run(self) -> list[dict]:
        if not self.panel[self.panel["business_date"] == self.D].shape[0]:
            self.add("ROJO", "sin_datos", None, None, f"No hay datos cargados para el {self.D}",
                     "La carga diaria no trajo información del día analizado.", "100% de los datos", "-",
                     "Revisar el registro de cargas (pantalla Alertas > Calidad de datos).")
            return self.alerts
        for rule in (self.rule_branch_sales, self.rule_branch_platform_ops, self.rule_refunds,
                     self.rule_products, self.rule_platform_wtd, self.rule_run_rate):
            rule()
        self.alerts.sort(key=lambda a: LEVEL_ORDER[a["level"]])
        return self.alerts


def generate(D: date, persist: bool = True) -> list[dict]:
    alerts = AlertEngine(D).run()
    if persist:
        demo = kpis.is_demo_data()
        db.execute("DELETE FROM alert WHERE business_date = :d", {"d": D.isoformat()})
        db.insert_rows(db.alert, [dict(a, generated_at=db.now_utc(), business_date=D, is_demo=demo) for a in alerts])
    return alerts
