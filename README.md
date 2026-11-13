# Centro de Control Comercial · 6 sucursales × Justo / Rappi / Uber Eats

Sistema que todas las mañanas reúne las ventas y la operación de las tres plataformas, las consolida
por sucursal, detecta desvíos y deja listo un panel y un informe para entender el negocio en menos de
5 minutos.

## Qué incluye

| Pieza | Estado |
|---|---|
| Modelo de datos con anti-duplicados, historial y trazabilidad | ✅ Listo |
| Lectura de reportes CSV/Excel por mapeo + plantillas canónicas | ✅ Listo (mapeos de Rappi/Uber **pendientes de un archivo real**) |
| Buzón de correo de reportes (IMAP) | ✅ Listo (falta configurar el buzón) |
| Conector API Justo | ⏳ Autenticación lista; endpoint de órdenes a confirmar con Justo |
| Controles de calidad (faltantes, inconsistencias, fuente desactualizada) | ✅ Listo |
| KPIs, comparaciones homogéneas, run rate con patrón por día de semana | ✅ Listo |
| Descomposición sesiones × conversión × ticket | ✅ Listo |
| Alertas ROJO/AMARILLO/VERDE con filtro de ruido estadístico | ✅ Listo |
| Analista IA: informe diario + preguntas en lenguaje natural | ✅ Listo (requiere clave API) |
| Dashboard de 9 pantallas (PC y celular) | ✅ Listo |
| Corrida diaria automática (GitHub Actions) + email | ✅ Listo (requiere Secrets) |

Documentación: [`docs/`](docs) → 01 investigación de integraciones · 02 arquitectura · 03 modelo de
datos · 04 fórmulas · 05 **qué necesito de tu parte**.

## Probarlo en 1 minuto (con datos de demostración)

```bash
pip install -r requirements.txt
python -m cci.cli demo            # carga ~75 días de datos SINTÉTICOS (marcados como demo)
streamlit run dashboard/app.py    # abrir http://localhost:8501
```

Los datos de demostración muestran un aviso permanente y se eliminan con `python -m cci.cli demo --purge`.

## Uso diario

| Comando | Qué hace |
|---|---|
| `python -m cci.cli daily` | Corrida completa: carga → calidad → alertas → informe IA → email (lo que ejecuta el cron) |
| `python -m cci.cli ingest` | Solo carga (buzón + API Justo + carpeta `data/inbox`) |
| `python -m cci.cli alerts --date 2026-11-12` | Recalcula alertas de un día |
| `python -m cci.cli report` | Genera el informe IA |
| `python -m cci.cli ask "¿Por qué cayó la venta esta semana?"` | Pregunta al analista |
| `python -m cci.cli profile archivo.xlsx` | Muestra hojas y columnas de un reporte nuevo para configurar su mapeo |

## Agregar un formato de reporte

1. `python -m cci.cli profile <archivo>` para ver las columnas.
2. Completar el YAML correspondiente en `config/mappings/` (columnas, formato de fecha, mapa de estados).
3. Cambiar `verified: false` → `true`. Hasta ese momento el mapeo no carga nada (no adivinamos columnas).

## Puesta en producción

1. Crear base Postgres (Supabase/Neon) → `DATABASE_URL`.
2. Subir este repositorio a GitHub (privado) y cargar los Secrets listados en `.env.example`.
3. Completar `config/settings.yaml` (sucursales, IDs de tienda, horario, umbrales).
4. Publicar `dashboard/app.py` en Streamlit Community Cloud o Render con los mismos Secrets + `APP_PASSWORD`.
5. Cargar historia (8–12 semanas) con las plantillas o los reportes de cada plataforma.
6. Probar la corrida a mano en GitHub › Actions › "Corrida diaria" › Run workflow.

## Pruebas

```bash
python -m pytest -q tests
```
