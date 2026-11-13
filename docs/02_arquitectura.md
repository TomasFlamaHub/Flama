# 2. Arquitectura técnica

## Decisión

Una arquitectura **simple, barata y sin servidores propios que mantener**, que funciona aunque Claude
(o tu computadora) esté apagado:

```
 FUENTES                    PROCESO DIARIO (GitHub Actions, 07:20)          ALMACENAMIENTO           CONSUMO
 ───────                    ──────────────────────────────────────          ──────────────           ───────
 Justo API ───────────┐     1. Carga (API + buzón IMAP + archivos)          Postgres (Supabase)      Dashboard web
 Buzón de reportes ───┼──▶  2. Normaliza, asigna sucursal, deduplica  ──▶   · órdenes, ítems         (Streamlit, PC
  (Rappi / Uber CSV)  │     3. Controles de calidad                         · resarcimientos           y celular)
 Pantalla "Cargar" ───┘     4. KPIs, run rate, alertas                      · operación diaria       Email diario
  (subida manual)           5. Informe IA (Claude)                          · alertas, informes      Analista IA (chat)
                            6. Email con alertas + informe                  · registro de cargas
```

| Componente | Elección | Por qué | Costo aprox. |
|---|---|---|---|
| Base de datos | **PostgreSQL gestionado** (Supabase o Neon) | Estándar, con respaldo, crece sin esfuerzo, conectable a cualquier herramienta de BI | US$0 (plan gratuito) a US$25/mes |
| Automatización | **GitHub Actions** (cron diario + ejecución manual) | Gratis para este volumen, con historial de cada corrida y avisos si falla; no depende de ninguna PC | US$0 |
| Dashboard | **Streamlit** (Python) en Streamlit Community Cloud o Render | El mismo código que calcula los KPIs dibuja el panel: una sola fuente de verdad, fácil de mantener | US$0 a US$7/mes |
| IA | **Claude (API de Anthropic)** con herramientas de solo lectura | Informe diario + preguntas en lenguaje natural sobre datos reales | ≈ US$5–15/mes según uso |
| Notificaciones | Email (SMTP). WhatsApp en fase 3 (API de WhatsApp Business / Twilio) | Email es inmediato y gratis; WhatsApp requiere plantillas aprobadas | US$0 / ~US$0,01 por mensaje |

**Total estimado: US$0–50/mes.**

### Alternativas evaluadas

- **Power BI / Looker Studio** sobre la misma base: se pueden conectar más adelante (Postgres es
  compatible), pero el run rate con patrón por día de semana, la descomposición de variaciones y las
  alertas con filtro de ruido son más mantenibles en código versionado que en fórmulas DAX dispersas.
- **Google Sheets como base**: barato pero frágil (límites de filas, ediciones manuales, sin
  integridad). Descartado como base; sí sirve para exportar.
- **Servidor propio (VPS)**: más control, pero alguien tiene que mantenerlo y respaldarlo. Innecesario hoy.
- **Robots que se loguean en los portales (RPA)**: descartado (ver documento 01).

## Seguridad

- Credenciales **solo** como *Secrets* de GitHub / Streamlit, nunca en el código.
- Dashboard con contraseña (`APP_PASSWORD`). Para varias personas: Streamlit Cloud con lista de
  correos autorizados, o Cloudflare Access.
- La IA solo tiene herramientas de **lectura**; el SQL que puede ejecutar se limita a `SELECT`.
- El buzón de reportes es exclusivo y opcionalmente filtra por remitentes autorizados.

## Robustez

- **Idempotencia**: cada orden tiene clave única (plataforma + ID de orden); recargar no duplica.
- **Recarga de 3 días** en cada corrida: absorbe correcciones tardías (Uber indica hasta 72 h).
- Archivos ya procesados se reconocen por *hash* y no se vuelven a cargar.
- Cada carga queda en `ingestion_run`; cada problema en `dq_issue` (visible en la pantalla Alertas).
- Si una fuente no se actualiza en 36 h, aparece una alerta de **fuente desactualizada**.

## Estructura del código

```
cci/                 núcleo
  config.py          configuración y conexión
  db.py              modelo de datos (tablas) y upsert idempotente
  ingest/            conectores: justo_api, email_inbox (IMAP), files (CSV/Excel + mapeos), loader
  kpis.py            fórmulas, períodos, run rate, descomposición, mix
  quality.py         controles de datos faltantes / inconsistentes
  alerts.py          motor de alertas ROJO/AMARILLO/VERDE con filtro de ruido
  factpack.py        paquete de hechos del día para el informe
  analyst.py         analista IA (informe diario + preguntas)
  notify.py          email
  cli.py             comandos (daily, ingest, alerts, report, ask, profile, demo)
dashboard/app.py     9 pantallas
config/              settings.yaml (sucursales, umbrales, comisiones) y mapeos de reportes
.github/workflows/   corrida diaria
tests/               pruebas automáticas
```
