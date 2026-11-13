# 4. Fórmulas de los KPIs

Implementación: `cci/kpis.py`. Convención: **D** = día analizado (último día comercial cerrado, normalmente ayer).

## Ventas

| KPI | Fórmula | Tipo |
|---|---|---|
| Venta bruta | Σ `gross_amount` de órdenes **completadas** (precio de menú, antes de descuentos) | Real |
| Venta neta | Venta bruta − Σ descuentos financiados por el local | Real |
| Liquidación | Σ `payout_amount` (lo que paga la plataforma) | Real o **Estimada** |
| Costo de plataforma % | (Venta bruta − Liquidación) / Venta bruta | Real o Estimado |
| Órdenes | Cantidad de órdenes completadas | Real |
| Ticket promedio | Venta / Órdenes | Real |
| Venta "principal" | Bruta o neta según `headline_sales` en settings.yaml | — |

## Operación y conversión

| KPI | Fórmula | Nota |
|---|---|---|
| Sesiones | Σ sesiones informadas por la plataforma | **Definición distinta por plataforma** |
| Conversión | Órdenes / Sesiones, **solo** en filas sucursal×plataforma×día con sesiones informadas | No mezclar órdenes sin sesiones |
| Open Time | Σ minutos disponible para recibir pedidos | — |
| Disponibilidad % | Open Time / minutos programados de apertura | — |
| Indisponibilidad | Minutos programados − Open Time | — |

## Resarcimientos y cancelaciones

| KPI | Fórmula |
|---|---|
| Resarcimientos (cantidad y $) | Cantidad y Σ monto de `adjustments` |
| Resarcimientos % venta | Σ resarcimientos / Venta bruta |
| Cancelaciones | Órdenes con estado cancelada o rechazada |
| Tasa de cancelación | Canceladas / (Completadas + Canceladas) |
| Venta afectada | Σ `gross_amount` de órdenes canceladas |

## Períodos comparables (homogéneos)

| Comparación | Período actual | Base |
|---|---|---|
| Día | D | D − 7 (mismo día de la semana) y D − 1 |
| Semana acumulada | lunes..D | lunes−7..D−7 (**misma altura**) |
| Semana completa | — | lunes−7..domingo−7 |
| Mes acumulado | 1..D | 1..día(D) del mes anterior (recortado si el mes anterior es más corto) |
| Mes completo | — | mes anterior completo |

## Run rate (proyección)

Con al menos 3 semanas completas de historia se usa el **patrón por día de semana**:

```
w_d        = venta promedio del día de semana d en las últimas 8 semanas completas
Proyección = Real acumulado × Σ_{todos los días del período} w_d  /  Σ_{días transcurridos} w_d
```

Ejemplo: si lunes a jueves pesan 1 y viernes a domingo pesan 3, y al jueves llevamos $400, la
proyección es 400 × 13 / 4 = **$1.300** (no $700 como daría la regla lineal 400 × 7/4).

Sin historia suficiente se usa la **proyección lineal** (Real × días del período / días transcurridos)
y el panel lo indica ("método: lineal").

Se proyectan: venta, órdenes, sesiones, resarcimientos, cancelaciones y Open Time. Las tasas se derivan:
ticket proyectado = venta proy. / órdenes proy.; conversión proyectada = órdenes proy. / sesiones proy.;
resarcimientos % proyectado = resarcimientos proy. / venta proy.

El mismo cálculo se hace por sucursal y por plataforma (si un grupo no tiene historia propia, usa la
forma semanal del total). Pendiente: calendario de feriados.

## ¿Qué explica una variación? (descomposición)

```
Venta = Sesiones × Conversión × Ticket
ΔVenta = aporte_sesiones + aporte_conversión + aporte_ticket      (método LMDI, suma exacta)
aporte_x = L(V1, V0) × ln(x1 / x0),   con L(a, b) = (a − b) / (ln a − ln b)
```

Así el sistema puede decir "la caída se explica por menos tráfico (sesiones) y no por conversión".
También se calcula qué sucursal y qué plataforma aportan cuánto a la variación total.

## Mix de productos

| KPI | Fórmula |
|---|---|
| Participación % | Venta del producto / venta total del período |
| Variación | Unidades y venta vs período base; Δ participación en puntos porcentuales |
| Divergencia de una sucursal | ½ Σ |p_sucursal − p_resto| (0% = mismo mix que el resto; 100% = totalmente distinto) |

## Alertas

Línea base: **promedio del mismo día de la semana en las últimas 4 semanas**. Una alerta se dispara si el
desvío supera el umbral (`config/settings.yaml`) **y** es estadísticamente distinguible del ruido:

- Conteos (órdenes, conversión, cancelaciones): z de Poisson = (observado − esperado) / √esperado.
- Montos y ticket: error estándar aproximado con coeficiente de variación del ticket (`ticket_cv`).
- ROJO exige |z| ≥ 3; AMARILLO |z| ≥ 2.
- Producto que dejó de venderse: solo si, con el volumen del día, se esperaban ≥ 4,6 unidades
  (probabilidad de vender 0 por azar < 1%).

| Regla | ROJO | AMARILLO | VERDE |
|---|---|---|---|
| Venta sucursal vs su promedio | ≤ −25% | ≤ −12% | ≥ +15% |
| Sesiones (sucursal × plataforma) | ≤ −25% | ≤ −12% | — |
| Conversión | ≤ −20% | ≤ −10% | — |
| Ticket | ≤ −15% | ≤ −8% | — |
| Disponibilidad | < 85% | < 95% | — |
| Resarcimientos % venta | ≥ 2× base o ≥ 3% | ≥ 1,5× base | — |
| Cancelaciones | ≥ 2× base o ≥ 5% | ≥ 1,5× base | — |
| Plataforma, semana vs misma altura | ≤ −15% | ≤ −8% | ≥ +10% |
| Run rate semanal vs objetivo (o vs semana anterior) | < 90% | < 97% | > 105% |
| Producto top sin ventas | esperado ≥ 6,9 u | esperado ≥ 4,6 u | — |
