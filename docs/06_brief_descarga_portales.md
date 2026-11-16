# 6. Instrucciones para Claude en Chrome: relevamiento y descarga de los portales

Copiar el bloque completo en una conversación de **Claude en Chrome** (extensión en el navegador
donde ya están abiertas las sesiones de Uber Eats Manager, Rappi Partners y Justo).

---

## INSTRUCCIONES (pegar desde acá)

Sos mi asistente de datos. Estoy construyendo un centro de control comercial para dos marcas:
**Kobo** (6 tiendas en Justo, Rappi y Uber Eats) y **The Crust** (2 tiendas, solo Uber Eats).
Necesito dos cosas de cada portal: **(1) un inventario de qué métricas existen** y **(2) descargar los
reportes**. El análisis tiene que ser completo y por tienda: ventas, órdenes, ticket, **sesiones/visitas,
conversión (CVR)**, **tiempo abierto / tiempo fuera de línea**, cancelaciones, **resarcimientos /
compensaciones / reembolsos** y sus motivos, productos vendidos, calificaciones, tiempos de preparación
y entrega, y costos (comisiones, promociones, ads).

**Reglas estrictas**
- Solo LECTURA y DESCARGA. No cambies ninguna configuración, precio, menú, horario, promoción ni campaña.
  No aceptes términos, no respondas reclamos, no pauses tiendas. Si un botón puede modificar algo, no lo toques.
- No compartas ni copies credenciales. Si algo pide volver a iniciar sesión o un código de verificación, avisame.
- Si un reporte no existe o no se puede exportar, anotalo; no lo inventes.

### Paso 1 · Identificación de tiendas
En cada portal, anotá la lista de tiendas con: **nombre exacto, ID de tienda, dirección/comuna, marca**.
(Rappi ya conocemos el storeId CL900114436: ¿qué tienda es?)

### Paso 2 · Inventario de métricas (lo más importante)
Recorré todas las secciones de reportes/analítica/rendimiento/operaciones/finanzas/marketing de cada
portal. Para cada métrica que encuentres, completá una fila:

| Portal | Sección (ruta de menú) | Métrica | Definición que da el portal (texto exacto del tooltip/ayuda) | Nivel (tienda / marca / producto / hora) | Granularidad temporal (hora / día / semana) | Histórico disponible (cuánto atrás) | ¿Se puede exportar? (CSV/Excel/no) | ¿Se puede programar envío por email? |

Buscar en particular:
- **Embudo**: visitas/sesiones/impresiones → vistas de menú → agregados al carro → pedidos (CVR). Copiar la
  definición exacta de "sesión" o "visita" de cada plataforma.
- **Disponibilidad**: tiempo en línea, tiempo fuera de línea / pausas, motivo de la pausa, horario programado.
- **Calidad**: pedidos cancelados (y por quién: local/cliente/repartidor), pedidos con error, productos
  faltantes, **compensaciones/resarcimientos/reembolsos** con monto, motivo y número de pedido, calificación.
- **Operación**: tiempo de aceptación, tiempo de preparación, demoras.
- **Productos**: unidades y ventas por producto, productos agotados/pausados.
- **Finanzas**: venta bruta, descuentos (quién los paga), comisión, cargos, ads, liquidación/pago.
- **Marketing**: promociones activas, inversión en ads y su retorno.

### Paso 3 · Descargas
Para cada tienda (o todas juntas si el portal lo permite), descargar **todo lo exportable** de:
- **Período A:** ayer (sirve para configurar el formato).
- **Período B:** las **últimas 12 semanas** (historia para tendencias, run rate y alertas). Si el portal
  limita el rango, descargar por tramos (por ejemplo, mes a mes).

Prioridad por portal:
1. **Uber Eats Manager** (Kobo y The Crust): pedidos/ventas por pedido, pagos (payment details),
   **tiempo de inactividad (downtime)**, embudo/visitas o conversión, errores de pedido y reembolsos,
   ventas por producto, calificaciones, tiempos de preparación.
2. **Rappi Partners** (Kobo): **Pagos › Relación de ventas** (Excel con pestañas Detalle y Compensaciones),
   nivel de servicio (cancelaciones, tiempo sin disponibilidad, demoras), ventas/rendimiento, visitas y
   conversión si existen, productos, Rappi Ads.
3. **Justo** (Kobo): exportación de pedidos (con productos), reportes de ventas, analítica de la tienda
   (visitas/conversión si existe), y la sección de API/desarrolladores: anotá si existe y qué ofrece
   (NO generes tokens; solo describí la pantalla).

Nombrá los archivos así: `plataforma_marca_reporte_desde_hasta.ext`
(ej. `ubereats_kobo_downtime_2026-08-24_2026-11-15.csv`).

### Paso 4 · Entrega
Dame:
1. El **inventario** (tabla del paso 2) y la **lista de tiendas** (paso 1).
2. La lista de archivos descargados con: portal, reporte, tiendas incluidas, período, columnas.
3. Lo que **no** se pudo obtener y por qué.
4. Capturas de las pantallas de embudo/conversión y de disponibilidad de cada portal (para validar definiciones).

---

## Qué hacer con el resultado

Subir a la conversación del Centro de Control (Claude Code): los archivos descargados (o un .zip),
el inventario y las capturas. Con eso se configuran los lectores de cada reporte, se cargan las 12
semanas de historia y se reemplazan los datos de demostración por los reales.
