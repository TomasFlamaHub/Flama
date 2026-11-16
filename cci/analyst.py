"""Analista comercial con IA (Claude): informe ejecutivo diario + preguntas en lenguaje natural.

Diseño anti-alucinación:
  * Todas las cifras vienen del motor de KPIs (factpack) o de herramientas de consulta
    de solo lectura. El modelo no tiene acceso a escribir en la base.
  * El prompt exige distinguir REAL / ESTIMADO / PROYECCIÓN y declarar cuando falta un dato.
"""
from __future__ import annotations

import json
import re
from datetime import date

import anthropic

from . import config, db, factpack, kpis

SYSTEM = """Sos el analista comercial senior de un grupo gastronómico con dos marcas que se analizan por \
separado: Kobo (6 tiendas en Justo, Rappi y Uber Eats) y The Crust (2 tiendas, solo Uber Eats). Escribís en español, para el dueño, que necesita entender el negocio en menos de 5 minutos.

Reglas estrictas:
- Usá SOLO cifras presentes en los datos entregados o devueltas por las herramientas. Nunca inventes ni \
estimes números por tu cuenta. Si un dato no está, decilo ("no disponible").
- Distinguí siempre: datos REALES, ESTIMADOS (p.ej. liquidación calculada con comisión contractual) y \
PROYECCIONES (run rate). Rotulá las proyecciones como tales e indicá el método.
- Las comparaciones son homogéneas: día vs mismo día de la semana anterior, semana acumulada vs misma \
altura de la semana anterior, mes vs mismos días del mes anterior. Decí siempre contra qué comparás.
- Explicá causas con la descomposición Venta = Sesiones x Conversión x Ticket cuando esté disponible: \
distinguí problemas de tráfico (sesiones), de conversión o de ticket.
- Las sesiones tienen definiciones distintas por plataforma: no compares conversiones entre plataformas \
como si fueran idénticas sin advertirlo.
- Si es_demo es true, aclarálo al inicio: los datos son de demostración, no reales.
- Montos en pesos con separador de miles (ej. $1.234.567). Porcentajes con un decimal.
- Sé concreto y accionable. Nada de relleno."""

REPORT_PROMPT = """Con el siguiente paquete de datos del día, escribí el INFORME EJECUTIVO DIARIO de la marca indicada en \
el campo "marca", en Markdown \
con estas secciones, en este orden y breves:

1. **Resumen del negocio** (3-4 frases: venta de ayer, semana acumulada vs semana anterior, proyección \
semanal y mensual vs período anterior).
2. **Sucursales destacadas** (mejores, con la razón).
3. **Sucursales con problemas** (con la causa probable según sesiones/conversión/ticket/open time).
4. **Plataformas** (cuál crece, cuál cae y por qué).
5. **Mix de productos** (cambios relevantes y sucursales con mix atípico).
6. **Resarcimientos y cancelaciones**.
7. **Alertas relevantes** (solo las rojas y las amarillas más importantes).
8. **Oportunidades y recomendaciones concretas** (máximo 5, priorizadas, con responsable sugerido).
9. **Calidad de datos** (solo si hay problemas: qué falta y qué tan confiable es el informe).

DATOS:
```json
{data}
```"""

TOOLS = [
    {"name": "get_daily_factpack",
     "description": "Devuelve el paquete completo de KPIs ya calculados para una fecha (día comercial): "
                    "consolidado, comparaciones, proyecciones semanal/mensual (total, por sucursal y plataforma), "
                    "tablas por sucursal y plataforma, descomposición sesiones x conversión x ticket, mix de "
                    "productos, alertas y calidad de datos. Usala primero para cualquier pregunta general.",
     "input_schema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "AAAA-MM-DD. Por defecto el último día con datos."},
         "brand": {"type": "string", "description": "id de marca (kobo, crust). Omitir = todas."}},
         "required": [], "additionalProperties": False}},
    {"name": "compare_periods",
     "description": "Compara dos rangos de fechas arbitrarios, agrupando por sucursal o plataforma. "
                    "Devuelve venta, órdenes, ticket, sesiones, conversión, disponibilidad, resarcimientos y "
                    "cancelaciones con variaciones.",
     "input_schema": {"type": "object", "properties": {
         "current_start": {"type": "string"}, "current_end": {"type": "string"},
         "base_start": {"type": "string"}, "base_end": {"type": "string"},
         "group_by": {"type": "string", "enum": ["branch_id", "platform_id", "brand_id"]},
         "brand": {"type": "string"},
         "branches": {"type": "array", "items": {"type": "string"}},
         "platforms": {"type": "array", "items": {"type": "string"}}},
         "required": ["current_start", "current_end", "base_start", "base_end", "group_by"],
         "additionalProperties": False}},
    {"name": "product_mix_change",
     "description": "Variación del mix de productos entre dos rangos (unidades, venta, participación, "
                    "aporte a la variación), filtrable por sucursal y plataforma.",
     "input_schema": {"type": "object", "properties": {
         "current_start": {"type": "string"}, "current_end": {"type": "string"},
         "base_start": {"type": "string"}, "base_end": {"type": "string"},
         "branches": {"type": "array", "items": {"type": "string"}},
         "platforms": {"type": "array", "items": {"type": "string"}},
         "top": {"type": "integer"}},
         "required": ["current_start", "current_end", "base_start", "base_end"], "additionalProperties": False}},
    {"name": "run_sql",
     "description": "Consulta SQL de SOLO LECTURA (un único SELECT) sobre las tablas: orders(platform_id, order_id, "
                    "branch_id, created_at, business_date, status, gross_amount, discount_merchant, commission, "
                    "payout_amount, payout_is_estimated, cancel_reason), order_items(platform_id, order_id, "
                    "line_no, product_raw, product_id, quantity, gross_amount), product(product_id, name, "
                    "category), adjustments(platform_id, branch_id, business_date, amount, kind, reason, order_id), "
                    "store_day(platform_id, branch_id, business_date, scheduled_minutes, open_minutes, sessions), "
                    "alert, dq_issue. Máximo 200 filas. Úsala solo si las otras herramientas no alcanzan.",
     "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"],
                      "additionalProperties": False}},
]


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _context() -> str:
    bn = config.branch_names()
    pn = config.platform_names()
    _, last = kpis.data_bounds()
    marcas = {k: {"nombre": v["name"], "plataformas": v["platforms"], "sucursales": config.branches_of(k)}
              for k, v in config.brands().items()}
    return (f"Marcas: {json.dumps(marcas, ensure_ascii=False)}. Si la pregunta no indica marca y la "
            f"respuesta difiere por marca, separala por marca. "
            f"Sucursales (id: nombre): {json.dumps(bn, ensure_ascii=False)}. Plataformas: "
            f"{json.dumps(pn, ensure_ascii=False)}. Último día con datos: {last}. "
            f"Venta principal: {'bruta' if kpis.sales_col() == 'gross' else 'neta'}.")


def execute_tool(name: str, args: dict) -> str:
    try:
        if name == "get_daily_factpack":
            D = _d(args.get("date")) or kpis.data_bounds()[1]
            return json.dumps(factpack.build(D, args.get("brand")), ensure_ascii=False, default=str)
        if name == "compare_periods":
            cs, ce, bs, be = (_d(args[k]) for k in ("current_start", "current_end", "base_start", "base_end"))
            panel = kpis.load_panel(min(cs, bs), max(ce, be), args.get("branches"), args.get("platforms"),
                                    args.get("brand"))
            t = kpis.entity_table(panel, (cs, ce), (bs, be), args["group_by"])
            tot_c = kpis.summarize(kpis.filt(panel, cs, ce))
            tot_b = kpis.summarize(kpis.filt(panel, bs, be))
            return json.dumps({"por_grupo": t.round(4).to_dict("records"),
                               "total": factpack._clean(kpis.compare(tot_c, tot_b)),
                               "descomposicion_total": factpack._clean(kpis.decompose(tot_c, tot_b))},
                              ensure_ascii=False, default=str)
        if name == "product_mix_change":
            cs, ce, bs, be = (_d(args[k]) for k in ("current_start", "current_end", "base_start", "base_end"))
            m = kpis.mix_compare(kpis.product_mix(cs, ce, args.get("branches"), args.get("platforms")),
                                 kpis.product_mix(bs, be, args.get("branches"), args.get("platforms")))
            m = m.reindex(m["delta_revenue"].abs().sort_values(ascending=False).index).head(args.get("top", 15))
            return m.round(4).to_json(orient="records", force_ascii=False)
        if name == "run_sql":
            sql = args["sql"].strip().rstrip(";")
            if not re.match(r"(?is)^\s*(select|with)\b", sql) or ";" in sql or re.search(
                    r"(?i)\b(insert|update|delete|drop|alter|create|attach|pragma|grant|truncate|copy)\b", sql):
                return "ERROR: solo se permite un SELECT de lectura."
            df = db.read_df(f"SELECT * FROM ({sql}) q LIMIT 200")
            return df.to_json(orient="records", force_ascii=False, date_format="iso")
        return f"ERROR: herramienta desconocida {name}"
    except Exception as exc:  # el error vuelve al modelo como resultado
        return f"ERROR: {exc}"


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _create(client, **kw):
    """Llamada con fallback del lado del servidor ante rechazos (recomendado para este modelo)."""
    return client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kw)


def _text(resp) -> str:
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


def daily_report(D: date, brand: str | None = None, persist: bool = True) -> str:
    cfg = config.settings()["ai"]
    data = factpack.build(D, brand)
    client = _client()
    resp = _create(client, model=cfg["model"], max_tokens=16000, system=SYSTEM,
                   thinking={"type": "adaptive"}, output_config={"effort": cfg.get("report_effort", "high")},
                   messages=[{"role": "user", "content": REPORT_PROMPT.format(
                       data=json.dumps(data, ensure_ascii=False, default=str))}])
    if resp.stop_reason == "refusal":
        raise RuntimeError("El modelo no generó el informe (refusal).")
    md = _text(resp)
    if persist:
        db.execute("DELETE FROM ai_report WHERE business_date = :d AND brand_id = :b",
                   {"d": D.isoformat(), "b": brand})
        db.insert_rows(db.ai_report, [{"business_date": D, "brand_id": brand, "generated_at": db.now_utc(),
                                       "model": resp.model,
                                       "content_md": md, "is_demo": data["es_demo"]}])
    return md


def ask(question: str, history: list | None = None, max_steps: int = 8) -> tuple[str, list]:
    """Responde una pregunta en lenguaje natural usando las herramientas de consulta."""
    cfg = config.settings()["ai"]
    client = _client()
    messages = list(history or []) + [{"role": "user", "content": question}]
    system = SYSTEM + "\n\nContexto: " + _context()
    for _ in range(max_steps):
        resp = _create(client, model=cfg["model"], max_tokens=16000, system=system, tools=TOOLS,
                       thinking={"type": "adaptive"}, output_config={"effort": cfg.get("chat_effort", "medium")},
                       messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason == "refusal":
            return "No pude responder esa pregunta.", messages
        if resp.stop_reason != "tool_use":
            return _text(resp), messages
        results = []
        for b in resp.content:
            if b.type == "tool_use":
                out = execute_tool(b.name, b.input)
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out[:150000],
                                "is_error": out.startswith("ERROR")})
        messages.append({"role": "user", "content": results})
    return "La consulta requirió demasiados pasos; probá con una pregunta más acotada.", messages
