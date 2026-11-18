# 9. Ads y promociones por campaña — instrucciones para Claude en Chrome

Objetivo: saber cuánto vende cada campaña de Ads y cada oferta/promoción (ROAS, costo por pedido,
clientes nuevos). Período: 01-08-2026 a 15-11-2026. Resultado: un CSV `plantilla_campanas.csv`
(formato abajo) + los archivos que el portal permita descargar.

Ver el bloque para copiar en la conversación del 18-11-2026 o más abajo ("INSTRUCCIONES").

Formato del CSV (una fila por campaña × tienda × período):

    plataforma,campana,tipo,tienda,desde,hasta,inversion,impresiones,clics,pedidos,ventas,clientes_nuevos,roas
    rappi,Campaña 27358198,ads,Kobo Sushi - Manuel Montt,2026-10-07,2026-10-16,240285,,1520,96,2870000,31,11.9
    ubereats,30% en Kobo ONE,promocion,TODAS,2026-10-01,2026-10-31,1250000,,,410,14350000,85,

- tipo: ads | promocion
- tienda: nombre como en el portal, o TODAS si el portal no separa por tienda
- inversion: Ads = lo invertido; promoción = costo del descuento para el local (si el portal lo muestra)
- dejar vacío lo que el portal no muestre; no estimar
