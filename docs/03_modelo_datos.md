# 3. Modelo de datos

Base relacional única (PostgreSQL en producción, SQLite en local). Definición exacta en `cci/db.py`.

## Dimensiones

| Tabla | Clave | Contenido |
|---|---|---|
| `branch` | branch_id (S1…S6) | Sucursales. El ID interno no cambia nunca |
| `platform` | platform_id (justo, rappi, ubereats) | Plataformas |
| `store_map` | platform_id + external_store_id | **Qué ID de tienda usa cada plataforma para cada sucursal**. Clave para identificar sucursales |
| `product` | product_id | Producto canónico (nombre, categoría) |
| `product_alias` | platform_id + raw_name | Nombre del producto en cada plataforma → producto canónico (unifica "Combo Clásico" de Rappi con "Clásico + papas" de Justo) |

## Hechos

| Tabla | Grano | Clave única (anti-duplicados) | Campos principales |
|---|---|---|---|
| `orders` | 1 fila por orden | platform_id + order_id | branch_id, created_at (hora local), **business_date** (día comercial), status (completed/cancelled/rejected), gross_amount, discount_merchant, discount_platform, commission, other_fees, payout_amount, **payout_is_estimated**, cancel_reason |
| `order_items` | 1 fila por línea de orden | platform_id + order_id + line_no | product_raw, product_id, quantity, gross_amount |
| `adjustments` | 1 fila por resarcimiento | platform_id + adjustment_id | branch_id, business_date, order_id, amount (positivo = dinero perdido), kind, reason |
| `store_day` | sucursal × plataforma × día | platform_id + branch_id + business_date | scheduled_minutes, open_minutes (Open Time), sessions |

## Control y resultados

| Tabla | Para qué |
|---|---|
| `ingestion_run` | Cada carga: fuente, archivo, hash, filas leídas/insertadas/actualizadas/rechazadas, estado, fecha y hora |
| `dq_issue` | Problemas de calidad: tienda no mapeada, columnas faltantes, datos faltantes, inconsistencias, fuente desactualizada |
| `alert` | Alertas generadas por día (nivel, qué, dónde, magnitud, comparación, recomendación) |
| `ai_report` | Informe ejecutivo diario generado por la IA |

## Reglas clave

- **Día comercial**: `business_date = (created_at − hora de corte).date`. Con corte a las 05:00 un
  pedido de la 01:30 del viernes cuenta para el jueves. Esto hace homogéneas las comparaciones.
- **Idempotencia**: todas las cargas son *upsert*: si la orden ya existe, se actualiza (p. ej. pasó
  a cancelada); si no, se inserta.
- **Real vs. estimado**: `payout_is_estimated = true` cuando la liquidación se calculó con el %
  de comisión contractual porque el reporte no la traía. El dashboard lo marca.
- **Datos de demostración**: columna `is_demo` en todas las tablas de hechos; se borran con
  `python -m cci.cli demo --purge`.
- **Historial**: nada se borra, así que se pueden ver tendencias de todo el período cargado.
  Conviene cargar al menos 8 semanas hacia atrás para que el run rate use el patrón por día de semana.
