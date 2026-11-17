# 8. Embudo semanal (sesiones y conversión) — instrucciones para Claude en Chrome

Las plataformas no entregan sesiones por día. Para ver la evolución semana a semana se descarga
(o se lee) el embudo de cada tienda por semana. Pegar en Claude en Chrome:

---
Necesito el embudo de conversión **semanal** de las 6 tiendas Kobo, de las últimas 12 semanas
(lunes a domingo, desde el lunes 17-08-2026). Solo lectura y descarga; no cambies nada.

**Uber Eats Manager** › Desempeño › Ventas › *Embudo de conversión de usuarios*:
para cada tienda (una a la vez) y cada semana, elegí el rango lunes–domingo y hacé clic en
**Descargar** (CSV). Son 6 tiendas × 12 semanas. No hace falta renombrar los archivos.

**Rappi Partners** › Rendimiento › Ventas: para cada tienda y cada semana, anotá la
**Tasa de conversión total** que muestra la pantalla. Entregámelo como CSV con este formato exacto:

```
plataforma,tienda,desde,hasta,vieron_tienda,vieron_menu,agregaron_carrito,pedidos,conversion_pct
rappi,Kobo Sushi - La Reina,2026-08-17,2026-08-23,,,,,18
```
(una fila por tienda y semana; conversion_pct sin el signo %). Nombre del archivo: `plantilla_embudo_rappi.csv`.
---

Luego subir todo (CSV de Uber + plantilla de Rappi) en la pantalla **Cargar reportes** o en un .zip al chat.
