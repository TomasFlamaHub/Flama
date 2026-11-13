# 1. Investigación: posibilidades reales de integración

> Fecha de la investigación: 13/11/2026. Fuentes públicas (documentación oficial y centros de ayuda).
> Desde el entorno de desarrollo no se pudo abrir el detalle de `docs.getjusto.com` ni de
> `developer.uber.com` (la red los bloqueó). Lo que no está confirmado figura como **A VERIFICAR**.

## Conclusión ejecutiva

Las tres plataformas **no** dan el mismo nivel de acceso:

| | Justo | Rappi | Uber Eats |
|---|---|---|---|
| **Qué es para vos** | Tu canal propio (tienda online + delivery) | Marketplace | Marketplace |
| **API oficial** | Sí. API v3 con token Bearer creado por el propio comercio | Sí, pero pensada para **integradores de pedidos** (POS): recibir, aceptar y rechazar órdenes | Sí ("Marketplace APIs"), con una **Reporting API** (CSV), pero el acceso **requiere aprobación escrita de Uber** y convenio (NDA + licencia) |
| **¿La puede usar el comercio por su cuenta?** | Sí (token desde el portal) | En la práctica no: requiere onboarding y certificación con el equipo de integraciones | En la práctica no: es para partners aprobados (POS/integradores) |
| **Reportes descargables** | Panel de Justo (A VERIFICAR qué reportes y formato) | Rappi Partners › Pagos › "Relación de ventas" (Excel: Índice, Resumen, Detalle, **Compensaciones**) + sección de nivel de servicio (cancelaciones, tiempo sin disponibilidad, demoras) | Uber Eats Manager › Informes (CSV): pagos/ventas, pedidos, **Downtime** (minutos posibles vs. fuera de línea), errores/reembolsos |
| **Sesiones / conversión** | Es tu tienda: se pueden medir con Google Analytics/Meta Pixel si están instalados (A VERIFICAR) | Visibles en el portal (A VERIFICAR la definición y si se exporta) | Visibles en Uber Eats Manager (A VERIFICAR definición y exportación) |
| **Open Time / indisponibilidad** | A VERIFICAR | "Tiempo sin disponibilidad" en nivel de servicio | Reporte Downtime |
| **Resarcimientos** | N/A o mínimo (canal propio) | Pestaña "Compensaciones" de la relación de ventas | Ajustes/reembolsos en reportes de pago |
| **Nivel de automatización alcanzable hoy** | **Alto** (API + webhooks firmados con `X-Hub-Signature`) | **Medio** (reportes por correo/descarga; API solo vía integrador) | **Medio** (reportes CSV; API vía partner aprobado; los datos se asientan hasta 72 h) |

## Qué NO vamos a hacer

- **Scraping o robots que se loguean en los portales** (Rappi Partners / Uber Eats Manager) simulando
  a una persona: suelen violar los términos de uso, se rompen con cada cambio de pantalla, chocan con
  la verificación en dos pasos y ponen en riesgo la cuenta. Solo lo consideraríamos con autorización
  escrita de la plataforma.
- Simular integraciones: los conectores que dependen de datos no confirmados están **desactivados**
  (`verified: false`) y no cargan nada hasta validarlos con un archivo o respuesta real.

## Estrategia por fases

**Fase 1 (inmediata, semi-automática, ~5 min/día de una persona):**
1. **Justo → API** (automático): token del comercio + endpoint de órdenes.
2. **Rappi y Uber Eats → reportes**: alguien del equipo descarga los reportes cada mañana desde el
   celular o la PC y (a) los sube en la pantalla **Cargar reportes**, o (b) los reenvía a un
   **buzón dedicado** (p. ej. `reportes@tunegocio.cl`) que el sistema lee solo. Si las plataformas
   permiten **programar el envío** de reportes por email, el paso humano desaparece (A VERIFICAR en cada portal).
3. Todo lo que llega se deduplica por ID de orden, se asigna a la sucursal y se valida.

**Fase 2 (automatización total de órdenes):**
- **Middleware de pedidos certificado** (p. ej. integradores que ya tienen API oficial con Rappi,
  Uber Eats y Justo y exponen los pedidos por su propia API). Da órdenes, ítems y cancelaciones en tiempo
  real para las tres plataformas. Normalmente **no** trae sesiones/conversión: esas siguen saliendo de
  los portales.
- **Uber Eats Reporting API**: solicitar acceso como comercio, o a través del integrador.
- **Rappi**: consultar al ejecutivo de cuenta si existe exportación programada o acceso de reportes.

**Fase 3:** WhatsApp, objetivos por sucursal, feriados en la proyección y costos (CMV) para margen real.

## Preguntas para hacerle a cada plataforma (ejecutivo de cuenta / soporte)

**Rappi**
1. ¿Puedo programar el envío automático por email de la Relación de ventas y del reporte de nivel de servicio?
2. ¿El portal exporta **visitas/sesiones** y **conversión** por tienda y día? ¿Cómo se define "visita"?
3. ¿Hay acceso de API de reportes para comercios, o solo vía integrador certificado?
4. ¿Qué campos trae la pestaña Compensaciones (motivo, ID de orden, fecha)?

**Uber Eats**
1. ¿Puedo programar informes por email desde Uber Eats Manager?
2. ¿Cómo solicito acceso a la Reporting API (scope `eats.report`) siendo comercio con 6 locales?
3. ¿Qué informe trae embudo/visitas y conversión por tienda? ¿Definición de "sesión"?
4. Confirmar latencia: ¿los informes de ayer están completos a las 7:00 o conviene recargar 3 días?

**Justo**
1. URL base y endpoint v3 para **listar órdenes por rango de fechas** (con ítems, descuentos y estado).
2. Webhooks de órdenes disponibles (eventos y payload).
3. ¿Reportes de visitas/conversión de la tienda, o recomiendan Google Analytics?
4. ¿Registro de horas abiertas/pausadas por local?

## Fuentes

- Uber Eats Marketplace APIs (introducción, Store/Order/Reporting): https://developer.uber.com/docs/eats/introduction
- Uber Eats – Reporting guide: https://developer.uber.com/docs/eats/guides/reporting
- Uber Eats – Set Restaurant Status: https://developer.uber.com/docs/eats/references/api/v1/post-eats-stores-storeid-status
- Uber – datos de clientes y pedidos en Uber Eats Manager: https://help.uber.com/merchants-and-restaurants/article/understanding-customer-and-order-data-in-uber-eats-manager?nodeId=49fc4e14-cd2e-4224-99e5-3b922cf78dc8
- Rappi Developer Portal (stores / orders): https://dev-portal.rappi.com/es/api-reference/stores/ · https://dev-portal.rappi.com/es/api-reference/orders/
- Rappi – cómo descargar relaciones de venta: https://merchants.rappi.com/es-mx/como-descargar-mis-relaciones-de-venta-en-rappi
- Rappi – secciones del portal Rappi Partners: https://merchants.rappi.com/es-co/en-que-secciones-se-divide-el-portal-de-rappi-partners
- Justo Docs – autenticación y webhooks: https://docs.getjusto.com/docs/main/authentication · token info: https://docs.getjusto.com/docs/main/endpoints/me
- Justo – logística / APIs: https://www.getjusto.com/productos/logistica
