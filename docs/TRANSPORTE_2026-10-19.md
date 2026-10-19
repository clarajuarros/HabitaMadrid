# Transporte programado · IVP 2.0 · revisión de cobertura

Tras investigar individualmente los 38 municipios pendientes, **37 pasan a disponer de puntuación de transporte: 154/155 en total**. San Fernando de Henares conserva la ausencia de puntuación. Sus servicios están confirmados; falta el recuento exacto de MetroEste en las franjas diurnas laborables. Las 155 fichas siguen disponibles.

La fórmula `ivp-2.0.0-transporte-programado` no cambia: transporte es la media, con pesos 50/50, de la puntuación normalizada de expediciones intermunicipales por día y destinos municipales directos por día. La semana sigue siendo **19–25/10/2026**. La revisión de datos es `2026-10-19-cobertura-completada`.

## Alcance del cálculo

Cada viaje publicado cuenta una vez por municipio y fecha, aunque tenga varias paradas locales. Debe permitir subir y bajar después en otro municipio madrileño. Se respetan las restricciones de subida y bajada, los recorridos cortos y los calendarios. Se calculan medias de los siete días, con laborables, sábado y domingo separados. Las horas superiores a 24 pertenecen al día de servicio de origen.

Los 179 municipios pueden ser destinos; los 155 de hasta 50.000 habitantes forman el universo elegible. Las referencias de normalización se ajustan sobre los municipios puntuables de esta edición antes de aplicar filtros. Los filtros no recalculan las puntuaciones. Si falta una dimensión con peso positivo, el IVP permanece ausente; no se redistribuyen pesos ni se vuelve a la distancia a una parada.

Se mide **oferta publicada**, no puntualidad, ocupación, funcionamiento observado o tiempos reales. No se incluyen transbordos ni destinos de otras provincias. Una correspondencia de línea contrastada no acredita exhaustividad absoluta de la red. Las paradas sin hora publicada conservan esa ausencia; no se interpolan horas para presentar tiempos de viaje.

## Fuentes y decisiones de integración

- [CRTM · datos abiertos](https://datos.crtm.es/): GTFS vigentes de autobuses interurbanos, urbanos y Metro Ligero. Se aplican calendarios, excepciones y permisos de subida/bajada.
- [CRTM · itinerarios y estaciones oficiales](https://services5.arcgis.com/UxADft6QPcvFyDU1/arcgis/rest/services/M8_Red/FeatureServer): se contrastan 4.118 relaciones de parada/itinerario de las líneas investigadas y 8.686 estaciones oficiales. Las correcciones 640/665/691 conservan la **variante exacta**, con municipio publicado y sin inventar horas intermedias. La parada 18122 pertenece a Zarzalejo; las 18124 y 10222 corresponden a otros municipios y no se trasladan a él. La 691 sí tiene paradas en el término de Cercedilla.
- [CRTM · horarios de viajeros](https://www.crtm.es/tu-transporte-publico/): se vuelven a descargar 44 PDF y se utilizan 2.893 expediciones fechadas de 15 líneas, con sus dos sentidos, temporadas, lectivos y noches. Cada línea incorporada mediante PDF se **excluye entera del GTFS antes de reemplazarla**, sin sumar ambos horarios. Los documentos conservan URL y SHA-256.
- **337:** los dos horarios actuales enlazados por [La Veloz](https://laveloz.es/horarios-la-veloz-crtm/) tienen texto idéntico a los PDF vigentes de CRTM. Se prioriza esta publicación de viajeros corroborada frente a variantes solapadas/discrepantes del GTFS. El conflicto no se oculta: `line337_origin_comparison.json` conserva por fecha y sentido los recuentos y horas discrepantes. No se elige el mayor ni se suman las fuentes. La nota de la salida desde Villaconejos, 15 minutos antes de Chinchón a las 9:00, se comprueba visualmente en ambos carteles.
- [Renfe · Cercanías](https://data.renfe.com/dataset/horarios-cercanias) y [horarios Regionales](https://data.renfe.com/dataset/horarios-de-alta-velocidad-larga-distancia-y-media-distancia): los **31 viajes activos de Madrid sin paradas** se reconcilian individualmente, por número de tren y fecha, con un único horario Regional activo. Se incorpora únicamente el tramo hasta el extremo declarado en Cercanías (Cercedilla/Fuenlabrada), sin volver a contar la continuación ya publicada. El Regional 17707 no para en Humanes. También se contrasta el buscador público de Renfe para siete días en Humanes–Fuenlabrada y Cercedilla–Chamartín, en ambos sentidos. Fuentes Renfe bajo [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), con procedencia y transformación identificadas, sin respaldo oficial a HabitaMadrid.
- [Calendario laboral de la Comunidad](https://www.comunidad.madrid/empleo/calendario-laboral-comunidad-madrid-municipios), [calendario escolar](https://www.comunidad.madrid/docs/2026-06/calendario_escolar_2026-2027.pdf) y [calendario académico UAM](https://www.uam.es/uam/media/doc/1607016943336/calendario-academico-2026-2027.pdf): se revisa la semana concreta, incluidos los servicios lectivos. El festivo local de La Cabrera del 19/10 se registra y no se utiliza para cancelar arbitrariamente servicios ordinarios con calendario de origen Madrid.

La 721 no duplica los viajes de su tabla penitenciaria, que son un subconjunto. N103 se trata como un único vehículo circular en su rama a Cobeña. FS1 y N908 incorporan sus paradas oficiales de Majadahonda y Villanueva de la Cañada; N908 respeta la prohibición de viajes Madrid–Las Rozas y su ampliación del sábado. Los servicios t de la 635 empiezan/terminan en Torrelodones, no en Madrid.

SD1 está confirmado como servicio a demanda con reserva. No hay un número fijo publicado de viajes ni una ruta garantizada; se describe aparte de las expediciones programadas en **los nueve municipios de su ámbito**. La demanda desconocida no se considera cero. El Álamo no recibe las expediciones de la 528 por aparecer en el buscador: sus paradas están en otros municipios. Las paradas de las líneas 287/289 llamadas Estación de San Fernando y Hospital del Henares están en Coslada.

## San Fernando de Henares: servicio confirmado, puntuación pendiente

La página actual de [Metro · Línea 7](https://www.metromadrid.es/es/linea/linea-7#line-timetable) indica circulación normal y publica ambos carteles **26.A** de MetroEste. Se han descargado y revisado. Publican primeros/últimos trenes y salidas concretas de fines de semana y noche, pero los laborables diurnos se describen mediante intervalos variables —por ejemplo, 4,5–6,5 minutos—. No contienen todas las expediciones exactas necesarias para este cálculo.

El GTFS de Metro localizado en ArcGIS caducó el 27/05/2026. El NAP anuncia otro conjunto y requiere cuenta para descargarlo; no se ha creado una cuenta ni se ha reutilizado el fichero caducado. Los rangos no se convierten en salidas ficticias, ni se modifica la fórmula para completar artificialmente 155/155. El recuento observado de autobuses se conserva como parcial y no genera puntuación de transporte ni IVP completo.

## Efecto y conservación de la base

| Cobertura | Publicación anterior | Revisión actual |
| --- | ---: | ---: |
| Transporte | 117/155 | 154/155 |
| IVP completo · VC | 77 | 112 |
| IVP completo · VU | 101 | 138 |

Se recalculan las tres normalizaciones, índices generales y escenarios de sensibilidad de **todos** los municipios. Mapa, Top 5, fichas, ranking, cuestionario, comparaciones e informes consumen la misma edición. Un cambio de puntos o puesto no demuestra una mejora real: cambian evidencia y cobertura. Vivienda, sanidad y educación conservan aquí sus fuentes y puntuaciones anteriores.

La base física IVP 1.0 permanece sin alterar en `data/processed/ivp/`. La revisión activa está en `data/processed/transporte_oferta/`, declarada por `active_transport_edition` en `data/runtime_snapshot.json`. La propuesta histórica de ampliación de vivienda sigue archivada y no se mezcla con esta edición.

## Evidencia reproducible

`edition.json` registra las huellas de la base, entradas, archivos derivados y fuentes. `gtfs_component_daily.json` conserva por separado el recuento GTFS; `dated_pdf_journeys.json`, cada vehículo PDF y su recorrido municipal. La aplicación verifica que la suma de expediciones y la unión de destinos de ambos reproducen exactamente `offer_daily.json`, antes de comprobar medias, normalización, IVP y puestos. Los SHA detectan alteraciones, pero no son una firma ni certifican por sí solos la verdad del dato original.

`municipal_service_audit.json` documenta individualmente los 38 casos, resoluciones, horarios enlazados y ausencias. `renfe_prefix_repairs.json` conserva las 31 correspondencias fechadas; `topology_repairs.json`, cada variante corregida. Las transcripciones anteriores de diagnóstico permanecen como historial y no autorizan los recuentos activos.

La base SQLite del localizador complementario de horarios conserva su descarga del 15/10/2026 y versión anterior, rotuladas en la interfaz. No sustituye a la edición del IVP. Los enlaces actuales a horarios contrastados aparecen en las fichas investigadas.
