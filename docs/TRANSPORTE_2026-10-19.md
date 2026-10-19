# Transporte programado · Edición IVP 2.0

La edición activa `ivp-2.0.0-transporte-programado` sustituye la distancia a una parada como componente de transporte. Combina **expediciones intermunicipales por día** y **destinos municipales directos por día**, cada uno normalizado con el mismo peso. La semana fija de referencia es el **19–25 de octubre de 2026**. La activación de esta metodología ha sido autorizada por la propietaria; la comprobación de cada despliegue externo se realiza siguiendo [Despliegue](DESPLIEGUE.md).

## Qué mide

Una expedición cuenta una sola vez por municipio y fecha, aunque pase por varias paradas del mismo municipio. Debe permitir subir en él y bajar después en otro municipio de la Comunidad de Madrid. La conectividad cuenta los destinos municipales distintos alcanzables sin transbordos. Incluye como destinos los municipios de más de 50.000 habitantes, aunque estos no formen parte del ranking de los 155 municipios elegibles.

Se calculan medias diarias de la semana y se distinguen laborables, sábado y domingo. Son expediciones programadas, no una estimación de intervalo de espera entre servicios. No se acreditan puntualidad, capacidad, duración del trayecto ni funcionamiento en tiempo real. No se incluyen conexiones con transbordos ni destinos de otras provincias.

Cada componente utiliza referencias fijas de normalización sobre los municipios puntuables de esta edición; los filtros posteriores no cambian esas referencias. Una oferta mayor da una puntuación mayor. Transporte es la media de las puntuaciones de frecuencia y conectividad, con pesos 50/50. Es una decisión explícita del modelo, no una equivalencia empírica entre una expedición adicional y un destino adicional.

El IVP general conserva el reparto igual entre sanidad, educación, transporte y vivienda. Solo cambia la dimensión de transporte. La proximidad a una parada se mantiene como contexto y filtro auxiliar, sin contribuir a esa puntuación ni reemplazar los horarios ausentes.

## Fuentes y contraste

- **CRTM:** GTFS vigentes de autobuses interurbanos, urbanos de otros municipios y Metro Ligero. Los feeds de Metro y Cercanías del CRTM localizados estaban caducados y se excluyen. [Portal oficial](https://datos.crtm.es/).
- **Renfe:** [Horarios de Cercanías](https://data.renfe.com/dataset/horarios-cercanias), descargados el 19/10/2026, con calendario vigente para la semana de referencia. Renfe publica este conjunto bajo [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). HabitaMadrid transforma calendarios y recorridos en agregados municipales, conserva la procedencia y no implica respaldo de Renfe. No se distribuye el ZIP GTFS bruto.
- **PDF del CRTM:** horarios enlazados desde las fichas oficiales de las líneas. Se descargaron 44 documentos; 36 tienen transcripción estructurada de horarios o reglas y ocho quedan pendientes de transcripción. Es una selección auditada, no todos los PDF de la red. [Transporte público CRTM](https://www.crtm.es/tu-transporte-publico/).

Las secuencias GTFS se contrastan con la geometría municipal, los calendarios y los permisos de subida y bajada. El mismo viaje no se multiplica por sus paradas. Cuando un suplemento PDF sustituye una línea, se excluye antes la misma línea del GTFS para evitar duplicados. Se conservan URLs y huellas de las fuentes, transcripciones y decisiones de cobertura.

Los PDF distinguen trayectos cortos, temporadas, días lectivos y noches. Los recuentos ordinarios parciales se conservan como diagnóstico y no autorizan una puntuación hasta resolver calendarios y variantes. Los intervalos variables permanecen como rangos; no se inventan horas exactas. Aparecer en el buscador municipal del CRTM tampoco prueba que una parada se encuentre dentro del término, porque puede listar servicios próximos.

Renfe declara 31 viajes activos de Madrid sin secuencia de paradas en C5/C8b. No se inventan recorridos ni se consideran completos los municipios afectados.

## Cobertura y ausencias

Hay **118 municipios puntuables en transporte y 37 sin puntuación suficiente**. La ausencia expresa falta de evidencia suficiente para este cálculo, no ausencia real de transporte. Que no se detecte una brecha en los primeros no certifica exhaustividad de toda la red.

| IVP completo | Base histórica 1.0 | Edición activa 2.0 |
| --- | ---: | ---: |
| Vivienda colectiva (VC) | 113 | 78 |
| Vivienda unifamiliar o rural (VU) | 139 | 102 |

Las 155 fichas siguen disponibles. Si falta una dimensión con peso positivo, no hay IVP completo y no se reparten sus pesos entre las demás dimensiones. La personalización permite dar peso cero a una dimensión; el resultado conserva su identificación como índice de alcance parcial.

Los rankings de ambas ediciones tienen referencias y cobertura distintas. Un cambio de puesto no demuestra una mejora o empeoramiento real del municipio.

## Archivos e integridad

La base IVP 1.0 permanece físicamente sin alterar en `data/processed/ivp/`. Sus valores y huellas sirven como referencia histórica; no son los rankings activos 2.0. La revisión se guarda en `data/processed/transporte_oferta/` y se declara expresamente mediante `active_transport_edition` en `data/runtime_snapshot.json`.

`edition.json` registra versión, indicador, huellas de la base, entradas de preparación, archivos derivados, fuentes y metodología. `offer_daily.json` conserva evidencia diaria y agregados; `coverage_decisions.json`, las brechas pendientes; `oferta_municipal.csv`, componentes y puntuaciones. Las tablas derivadas incluyen ambos perfiles de vivienda, las tres normalizaciones y los escenarios de sensibilidad. Las huellas detectan alteraciones o ausencias; no constituyen una firma ni certifican por sí solas la exactitud de la fuente.

La propuesta histórica de vivienda continúa archivada con su referencia IVP 1.0 y se rechaza mezclarla con esta edición. La base SQLite del localizador de horarios conserva su fecha de descarga del 15/10/2026 y su versión anterior: es un servicio complementario distinto del cálculo de transporte 2.0.

El [informe del 18 de octubre](AUDITORIA_2026-10-18.md) conserva su valor histórico. Sus afirmaciones sobre ausencia de cambios del IVP describen aquel momento y quedan actualizadas, en transporte, por este documento.

## Verificación

La revisión preparada superó 201 pruebas automatizadas y las seis pruebas de integración se repitieron después de incorporar la evidencia al snapshot. Se comprueban calendarios, duplicados, referencias, ausencias, medias diarias, normalización, índices, puestos, filtros, informes y navegación. La aritmética del IVP se contrasta sin promedios que ignoren dimensiones ausentes.

La revisión visual local cubrió escritorio y anchos de móvil, sin desbordamiento horizontal a 360, 390 y 414 px. No equivale a pruebas en dispositivos físicos ni a una prueba de carga. Cada publicación debe actualizar código, tablas y manifiestos juntos, reiniciar Cloud y comprobar la web pública según [Despliegue](DESPLIEGUE.md).
