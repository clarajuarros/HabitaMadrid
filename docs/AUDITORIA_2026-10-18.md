# HabitaMadrid · revisión de navegación y datos

Fecha de revisión: 18 de octubre de 2026. Aplicación: https://habitamadrid.streamlit.app/ · Repositorio: https://github.com/clarajuarros/HabitaMadrid

## Resultados y alcance

Se han contrastado los 179 municipios del maestro por código, nombre oficial y población. Los 179 padrones de 2025 coinciden con el INE, sin duplicados ni diferencias. Continúan siendo elegibles 155 municipios de hasta 50.000 habitantes. No se ha sustituido el padrón por estimaciones provisionales de 2026.

Se incorporan a las fichas de los 155 municipios grupos de edad definitivos del Censo anual de 2025, hogares y viviendas principales/no principales del Censo 2021. Los archivos preparados contienen los 179 municipios. Cada indicador conserva año, publicación, consulta, URL y huella de origen; no se consulta al INE al abrir la ficha.

Padrón y Censo anual son operaciones distintas: sus totales difieren en 166 municipios del maestro. No es un error de unión. Por ejemplo, Rozas de Puerto Real tiene 579 habitantes en el padrón y 582 en el Censo anual de 2025. Los porcentajes por edad usan el total censal, no el padrón. La interfaz lo explica. Los recuentos de hogares y viviendas conservan su referencia de 2021, sin presentarse como inventarios actuales.

## Navegación

La cabecera nativa de Streamlit, aunque transparente y con altura cero declarada, interceptaba los clics por encima de la barra propia. Se elimina ese destino invisible de puntero; los botones nativos y el logotipo de siete estrellas se conservan.

El callback del mapa actualizaba municipio y URL, pero no solicitaba cambiar a Fichas municipales. Ahora solicita esa vista antes de reconstruir los controles. Se renueva únicamente la capa de selección nativa para permitir abrir de nuevo el mismo polígono; la identidad del gráfico y su cámara se conservan. Los eventos retenidos no se vuelven a aplicar en cada render.

Reiniciar limpia también la modalidad del Top 5, además de selección, cuestionario, filtros, prioridades, comparación, cámara y plan compartido. Inicio conserva preferencias cuando se utiliza como navegación normal. Las pruebas cubren cambios repetidos de vista y selección, ambos perfiles, códigos excluidos, eventos nativos de Streamlit y ausencia de selección inicial.

La recarga automática de Cloud actualizó el código principal pero una sesión abierta mantuvo un callback anterior. Se comprobó el comportamiento real, se reinició el proceso y se reconectó la sesión. Para futuras publicaciones, deben comprobarse los registros de actualización y los clics reales; una pantalla con texto nuevo no demuestra que todos los callbacks se hayan renovado.

## Rozas de Puerto Real: 3,76 €/m²/mes

El valor se conserva porque coincide exactamente con el Excel oficial: **3,7572254335260116**, hoja **Municipios**, celda **JJ4415**, código **28128**, variable **ALQM2_LV_M_VU_24**. Es la **mediana del alquiler declarado de vivienda habitual**, tipología **unifamiliar o rural**, año **2024**, publicada por MIVAU/SERPAVI. No es media, oferta de anuncios, precio de venta ni alquiler de un nuevo contrato.

El mismo registro publica 13 testigos, una mediana mensual de 320 € y superficie mediana de 117 m². Los percentiles por superficie son 2,1828358208955225 y 5,328285975407112 €/m²/mes. No hay que dividir las medianas de renta y superficie para reconstruir la mediana de cocientes: son estadísticas diferentes.

La metodología publica estas estadísticas a partir de un mínimo de diez testigos. Trece observaciones cumplen ese umbral pero constituyen una base pequeña. La ficha incorpora una nota para referencias con menos de 25 observaciones; ese aviso es una decisión prudencial de presentación, no un nuevo umbral oficial ni una exclusión del índice.

No se dispone de la fecha de inicio de los contratos ni de microdatos para explicar causalmente el precio bajo. No se atribuye a contratos antiguos sin evidencia. La serie VU 2020–2024 publica aproximadamente 3,92; sin estadística en 2021 (9 testigos); 3,71; 3,85; 3,76 €/m²/mes. La modalidad colectiva de 2024 no publica mediana (un testigo); se mantiene como ausencia.

El IPVA del INE no ofrece un precio municipal comparable para esta localidad de menos de 10.000 habitantes y sus unidades son índices, no euros por superficie. Los datos independientes del Censo aportan contexto residencial (692 viviendas, 259 principales y 433 no principales en 2021), pero no verifican el precio de un alquiler nuevo. Por tanto, el valor está verificado como reproducción de la estadística oficial; no equivale a certificar una oferta actual ni su representatividad para cada hogar.

## Auditoría automática

Se han ejecutado **88 controles** y registrado **562 señales**, sin modificaciones automáticas de observaciones. El registro adjunto identifica municipio, indicador, año, tipología, celda o registro y estado de revisión. Una misma localidad puede tener varias señales.

- 50.120 celdas numéricas y las identidades de 5.012 registros de vivienda (179 municipios × 14 años × dos tipologías) contrastados con el Excel. Cero diferencias; 9.941 celdas sin estadística publicada conservadas como ausencias.
- 5.191 registros de evolución municipal coinciden con el INE en los años comunes. La historia anterior conservada de la Comunidad de Madrid se distingue del contraste directo con el INE.
- Control de códigos, duplicados, uniones, importes negativos, unidades, superficies, percentiles, umbrales de publicación, saltos temporales y atípicos por año y tipología.
- Centros, hospitales y paradas contrastados por identidad, municipio, coordenadas y localización respecto al límite oficial, con jurisdicciones compartidas y tolerancia de control de 30 m. Los 55 centros educativos sin coordenadas operativas no se utilizan para calcular distancias.
- 2.325 distancias publicadas recalculadas en metros, en EPSG:25830. Se han recalculado también las mínimas de los tres servicios del IVP sobre los catálogos y tipologías pertinentes, comprobando el municipio del destino.
- Migraciones: suma de cuatro flujos, signo y denominador de la tasa por mil. Los saldos pueden ser negativos; no son porcentajes de 0–100 ni variaciones totales de población.
- Vivienda principal contrastada con descarga directa del INE, admitiendo únicamente el redondeo a dos decimales de la publicación de la Comunidad de Madrid.
- 775 observaciones del IVP, 930 filas de dimensiones y 310 filas del índice general revisadas. Se contrastan sus alquileres originales, las tres normalizaciones, anclas, ausencias, pesos iguales, empates, posiciones y Top 5. Los pesos personalizados siguen usando referencias fijas; no se reajustan a los filtros.
- Consulta oficial de transporte por los 155 municipios: 704 relaciones municipio-línea con etiqueta y enlace verificados. Los horarios descargados siguen siendo un feed parcial con calendario explícito; no se confunden con una certificación de frecuencia actual de toda la red.

Las señales incluyen 284 atípicos de alquiler que coinciden con el Excel, un salto anual de alquiler también publicado, 39 referencias de 2024 con pocas observaciones, 112 saltos poblacionales (64 contrastados directamente con INE y 48 anteriores o sin contraparte directa), 55 centros sin coordenadas y 38 registros con coincidencia de nombre y coordenadas. Estos últimos mantienen identificadores distintos: no se deduplican por su nombre o posición, pues puede haber varias entidades en un mismo complejo. La coocupación física y la vigencia de cada unidad no quedan certificadas por este control.

## Actualidad sectorial y límites que permanecen

Se volvieron a descargar los ficheros oficiales de educación y migraciones: coinciden byte a byte con la edición procesada. Se consultaron de nuevo los 431 registros del directorio institucional de centros de salud y consultorios: sus identidades, nombres, categorías y puntos no han cambiado; 425 destinos están dentro del ámbito y seis fuera. Las dos reparaciones documentadas de coordenadas siguen conservando su evidencia y valores originales.

El registro general de autorizaciones sanitarias sí ha cambiado: pasa de 15.887 a 15.865 identificadores. Hay 33 identidades con cambios: 25 retiradas del fichero, cinco modificaciones y tres incorporaciones. Es una actualización de una fuente distinta, no prueba por sí sola un cierre o apertura. Se conserva un registro separado sin NIF. El IVP y los listados actuales de atención primaria usan el directorio institucional contrastado, no ese registro de autorizaciones; no se reemplaza su catálogo por clínicas privadas ni se regenera artificialmente el IVP. La instantánea histórica de investigación del registro de autorizaciones conserva su fecha del 15 de octubre; el contraste del 18 queda archivado aparte.

No se certifica apertura efectiva, capacidad, horarios o plazas de cada centro. Los hospitales son una capa parcial sin vigencia operacional confirmada. La existencia de paradas o líneas no prueba frecuencia ni servicio en todo momento. Tampoco se certifica una vivienda disponible a partir de una estadística de alquiler declarado o de viviendas no principales. Estos límites siguen visibles en la aplicación.

## Efecto sobre IVP y ámbito

No se ha demostrado ningún error en las entradas vigentes del IVP. Las cinco huellas de referencia del índice, sus puntuaciones y rankings permanecen iguales. **Cambios de IVP: cero; cambios de elegibilidad: cero; cambios de población del maestro: cero.** Los nuevos indicadores demográficos son contexto. Se conserva la separación de la propuesta de vivienda experimental, que no está activada.

## Fuentes oficiales

| Indicador | Referencia | Publicación de la tabla | Fuente |
|---|---|---|---|
| Padrón municipal | 1/1/2025 | 11/12/2025 | [INE 2881](https://www.ine.es/jaxiT3/Tabla.htm?t=2881) |
| Grupos de edad, totales por sexo y nacimiento | 1/1/2025, definitivo | 2/12/2025 | [INE 68540](https://www.ine.es/jaxiT3/Tabla.htm?t=68540) |
| Hogares por tamaño | Censo 2021 | 29/6/2023 | [INE 59543](https://www.ine.es/jaxi/Tabla.htm?tpx=59543) |
| Viviendas principales/no principales | Censo 2021 | 29/6/2023 | [INE 59525](https://www.ine.es/jaxi/Tabla.htm?tpx=59525) |
| Alquiler declarado | 2011–2024 | Edición SERPAVI 2026 | [MIVAU y descargas](https://www.mivau.gob.es/vivienda/alquila-bien-es-tu-derecho/serpavi) |
| Definiciones de alquiler, tipologías, testigos y unidades | Metodología 18/3/2026 | 18/3/2026 | [Documento oficial](https://cdn.mivau.gob.es/portal-web-mivau/vivienda/serpavi/2026-03-18_Metodologia_SERPAVI.pdf) |
| Atención primaria | Consulta 18/10/2026 | Cada ficha conserva su fecha | [Directorio de la Comunidad de Madrid](https://www.comunidad.madrid/centros/buscador-centros-sanitarios) |
| Líneas municipales | Consulta 18/10/2026 | Consulta oficial | [CRTM](https://crtm.es/tu-transporte-publico/info-por-municipio/lineas/) |

Las fechas de descarga, URL exacta con filtros, bytes y SHA-256 están en los manifiestos. La fecha de incorporación a HabitaMadrid no sustituye el año de referencia.

## Archivos e instrucciones de reproducción

Cambios de navegación: `assets/app.css`, `src/app_selection.py`, `src/app_state.py`, `app.py`, pruebas de mapa y navegación.

INE: `src/ine_municipal.py`, `src/app_ine_ui.py`, scripts `download_ine_municipal.py` y `prepare_ine_municipal.py`, `data/raw/ine_municipal/`, `data/processed/ine_municipal/` y sus pruebas.

Auditoría: `src/data_quality.py`, scripts `audit_data_quality.py`, `verify_housing_source.py` y `verify_sector_sources.py`, `data/processed/data_quality/` y sus pruebas. Exportación: `src/runtime_snapshot.py` y `scripts/prepare_cloud_release.py`; incluye las nuevas fuentes agregadas y el archivo de distancias necesario para reproducir la auditoría en el repositorio publicado.

En la copia de investigación, con el entorno de preparación:

```sh
python scripts/download_ine_municipal.py
python scripts/prepare_ine_municipal.py
python scripts/verify_housing_source.py
python scripts/verify_sector_sources.py
python scripts/audit_data_quality.py
python -m unittest discover -s tests
```

El verificador del Excel necesita el original archivado y openpyxl; no es una dependencia para abrir la web. Las descargas sectoriales generan un contraste en `reports/`, sin reemplazar la edición sellada ni publicar identificadores del archivo bruto de autorizaciones. Una nueva edición que cambie población o elegibilidad detiene la preparación hasta actualizar coherentemente el maestro y las fases dependientes.

La auditoría del repositorio público se reproduce sin el archivo Excel excluido mediante el registro de contraste de celdas, su huella y la edición procesada. El contraste independiente completo de celdas se repite en la copia de investigación con el Excel original. La auditoría nueva escribe por defecto en `reports/data_quality/`, para no alterar los archivos de datos sellados que usa la web.

Para publicar una nueva edición: verificar la investigación, exportar a una carpeta nueva con `prepare_cloud_release.py`, revisar la lista de archivos, sincronizar el checkout publicado, ejecutar las pruebas de ejecución, hacer fetch y comprobar divergencias, commit y push normal. Nunca copiar toda la carpeta de investigación ni sus entornos/archivos brutos al repositorio. Las nuevas fuentes INE son datos municipales agregados oficiales. Streamlit utiliza `main/app.py`; tras la actualización verificar el registro de despliegue y, si hay callbacks antiguos en una sesión abierta, reiniciar el proceso y reconectar.

## Verificación y publicación

Las pruebas y las comprobaciones públicas finales, junto con el commit, se completan en el registro de entrega. El QR PNG/SVG codifica exclusivamente https://habitamadrid.streamlit.app/ y se ha decodificado con una biblioteca independiente. La revisión móvil utiliza una ventana de 390 × 844 y la de ordenador una ventana amplia; no equivale a pruebas físicas en iPhone/Android o en una red ajena.
