# Fuentes, atribuciones y revisión de redistribución

Revisión de preparación: 18 de octubre de 2026. No se ha publicado el contenido. El permiso de acceso a una URL no se toma como licencia de redistribución.

| Recurso | Procedencia y condiciones | Estado |
| --- | --- | --- |
| Tablas del catálogo abierto de la Comunidad de Madrid | El catálogo de preparación registra CC BY 4.0 y los enlaces, fechas y huellas por recurso. [Licencia CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.es). | Conservar atribución y fechas. |
| Directorios sanitarios y capas IDEM | [Condiciones de reutilización de la Comunidad de Madrid](https://www.comunidad.madrid/atencion-ciudadano/aviso-legal-privacidad). Las capas y directorios conservan su procedencia; no se les asigna automáticamente la licencia de otra tabla. | Conservar procedencia y fechas; no se han identificado restricciones adicionales en los registros preparados de las capas utilizadas. Se excluyen las páginas HTML originales. |
| Transporte CRTM | [Licencia de datos estáticos](https://www.crtm.es/licencia-de-uso/). Autoriza copia y datos derivados con las condiciones descritas. Atribución visible «Powered by CRTM», enlace y fechas conservados. La identificación de reutilizadores se recomienda. | Atribución añadida. Comprobar vigencia de los horarios en la fecha de presentación. |
| Centros urbanos IGN | [Política de datos](https://www.ign.es/web/politica-datos). Obra derivada del IGN, Nomenclátor Geográfico Básico de España. | Conservar procedencia y condiciones CC BY 4.0 compatibles. |
| Contexto de vivienda y renta INE | [Aviso legal del INE](https://www.ine.es/aviso_legal). Recursos, fechas y metadatos en `data/reference/housing_expansion/sources.json`. | Conservar atribuciones. |
| Alquiler MIVAU/SERPAVI | La [página oficial SERPAVI](https://www.mivau.gob.es/vivienda/alquila-bien-es-tu-derecho/serpavi) enlaza el recurso 2011–2024 utilizado. El [aviso legal del portal MIVAU](https://www.mivau.gob.es/ministerio/aviso-legal), apartados de propiedad intelectual y reutilización, autoriza la reproducción, distribución, modificación y comunicación, con fines comerciales y no comerciales, salvo indicación contraria. | Verificado el 18/10/2026. Conservar origen MIVAU, fecha de actualización y metadatos; no desnaturalizar ni sugerir patrocinio. No se asigna una licencia CC distinta. No se incluye el XLSX bruto. |
| Fotografías | Autor, fuente, URL de licencia, fecha y transformación por foto en `data/reference/municipality_photos/catalog.json`. | Conservar catálogo y atribuciones visibles. Las licencias de las imágenes no cubren el código. |
| Leaflet | Licencia del proveedor incluida con los archivos locales en `assets/vendor/leaflet/`. | Conservar avisos. |

Las tablas del IVP incluyen resultados derivados de las fuentes anteriores; excluir un fichero bruto no resuelve por sí solo las condiciones de sus datos derivados. Se ha comprobado la correspondencia del recurso utilizado con el enlace del portal oficial y sus condiciones generales de reutilización. Las tablas distribuidas son estadísticas municipales agregadas y conservan la fuente y los periodos. La autorización de publicación de la propietaria se recibió antes de la subida.

## Servicios de mapas y rutas

La aplicación utiliza proveedores externos indicados en los componentes de mapa y `src/service_routes.py`. Las atribuciones del mapa deben mantenerse. El endpoint OSRM público calcula rutas orientativas cuando el usuario las pide, con caché local; no debe tratarse como un servicio con SLA ni usarse para descargas masivas. [Política del servicio de rutas FOSSGIS](https://routing.openstreetmap.de/).

## Privacidad y metadatos

Se excluyen secretos, archivos de autenticación, cachés, entornos virtuales, temporales, el historial de investigación descargado y archivos locales de URL pública. En el catálogo de fuentes se convierten los prefijos de rutas personales a referencias relativas, conservando URLs, fechas, licencias y huellas de origen. El inventario identifica esta transformación de metadatos. Los valores municipales y del IVP permanecen iguales.
