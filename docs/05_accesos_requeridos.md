# 5. Lo que necesito de tu parte

Ordenado por prioridad. Las credenciales **no** se mandan por chat ni por email: se cargan como
*Secrets* en GitHub / Streamlit (te guío en el momento).

## A. Datos del negocio (para `config/settings.yaml`)

- [ ] Nombre real de las 6 sucursales (y cómo aparece cada una en cada plataforma).
- [ ] **ID o nombre de tienda** de cada sucursal en Justo, Rappi y Uber Eats (18 combinaciones).
- [ ] Horario de apertura de cada sucursal (para medir disponibilidad).
- [ ] Hora de corte del día comercial (¿a qué hora cierra el último local?).
- [ ] ¿La "venta" principal es la bruta o la neta?
- [ ] % de comisión contractual por plataforma (solo si los reportes no traen la comisión real).
- [ ] Objetivos de venta semanales/mensuales por sucursal (opcional).
- [ ] Lista de productos con categoría (opcional; ayuda a unificar nombres entre plataformas).

## B. Archivos de ejemplo (lo más importante para avanzar)

Necesito **un ejemplo real de cada reporte** (puede ser de 1 día y de una sola sucursal) para
configurar su lectura. Con eso activo los mapeos que hoy están en "pendiente":

**Rappi Partners**
- [ ] Pagos › Relación de ventas (Excel completo, con las pestañas Detalle y Compensaciones).
- [ ] Reporte de nivel de servicio / tiempo sin disponibilidad / cancelaciones (si se exporta).
- [ ] Reporte con visitas o sesiones y conversión (si existe en tu portal).

**Uber Eats Manager**
- [ ] Informe de pagos / detalle de pedidos (CSV).
- [ ] Informe de tiempo de inactividad (Downtime).
- [ ] Informe con visitas/embudo/conversión, y uno de reembolsos o errores de pedidos.

**Justo**
- [ ] Exportación de pedidos del panel (si existe) mientras configuramos la API.

## C. Accesos

| Acceso | Para qué | Cómo |
|---|---|---|
| **Token API de Justo** | Carga automática de pedidos | Portal de desarrolladores de Justo (o Plataforma › API › API Keys). Confirmar con Justo el endpoint de órdenes |
| **Buzón dedicado** (p. ej. reportes@tunegocio.cl) con IMAP y contraseña de aplicación | Recibir reportes automáticamente | Crear la cuenta; programar en Rappi/Uber el envío ahí si lo permiten, o reenviar manualmente |
| **Cuenta en GitHub** (repositorio privado) | Corrida diaria automática | Te creo el repositorio; vos sos el dueño |
| **Base Postgres** (Supabase o Neon, plan gratuito) | Historial centralizado | Crear el proyecto; cargar la URL de conexión como Secret |
| **Clave API de Anthropic** | Informe IA y preguntas | console.anthropic.com › API Keys (con límite de gasto mensual) |
| **Cuenta Streamlit Community Cloud** o Render | Publicar el dashboard | Conectar con GitHub |
| **SMTP** (puede ser el mismo Gmail/Workspace con contraseña de aplicación) | Email diario | Usuario + contraseña de aplicación |

## D. Historia

- [ ] Exportaciones de las **últimas 8–12 semanas** de cada plataforma, para que el run rate use el
      patrón por día de semana desde el primer día y las alertas tengan línea base.

## E. Preguntas a las plataformas

Ver la lista en `docs/01_investigacion_integraciones.md` (sección "Preguntas para hacerle a cada plataforma").
