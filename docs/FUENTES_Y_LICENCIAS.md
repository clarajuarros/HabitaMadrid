# Fuentes, atribuciones y revisión de redistribución

Revisión de preparación: 18 de octubre de 2026. No se ha publicado el contenido. El permiso de acceso a una URL no se toma como licencia de redistribución.

| Recurso | Procedencia y condiciones | Estado |
| --- | --- | --- |
| Tablas del catálogo abierto de la Comunidad de Madrid | El catálogo de preparación registra CC BY 4.0 y los enlaces, fechas y huellas por recurso. [Licencia CC BY 4.0](https://creativecommons.org/licenses/by/4.0/legalcode.es). | Conservar atribución y fechas. |
| Directorios sanitarios y capas IDEM | [Condiciones de reutilización de la Comunidad de Madrid](https://www.comunidad.madrid/atencion-ciudadano/aviso-legal-privacidad). Las capas y directorios conservan su procedencia; no se les asigna automáticamente la licencia de otra tabla. | Revisar las excepciones específicas de cada capa antes de publicar; se excluyen las páginas HTML originales. |
| Transporte CRTM | [Licencia de datos estáticos](https://www.crtm.es/licencia-de-uso/). Autoriza copia y datos derivados con las condiciones descritas. Atribución visible «Powered by CRTM», enlace y fechas conservados. La identificación de reutilizadores se recomienda. | Atribución añadida. Comprobar vigencia de los horarios en la fecha de presentación. |
| Centros urbanos IGN | [Política de datos](https://www.ign.es/web/politica-datos). Obra derivada del IGN, Nomenclátor Geográfico Básico de España. | Conservar procedencia y condiciones CC BY 4.0 compatibles. |
| Contexto de vivienda y renta INE | [Aviso legal del INE](https://www.ine.es/aviso_legal). Recursos, fechas y metadatos en `data/reference/housing_expansion/sources.json`. | Conservar atribuciones. |
| Alquiler MIVAU/SERPAVI | La fuente original está identificada en `data/processed/indicadores/fuentes_indicadores.csv`. El registro heredado dice «Consultar condiciones del MIVAU; no heredar licencia CKAN». La [respuesta oficial del Gobierno al Senado de 23/02/2026](https://www.senado.es/web/expedientdocblobservlet?id=255512&legis=15) describe la descarga libre y reutilización de las tablas estadísticas SERPAVI y su actualización anual. Es evidencia favorable a la reutilización estadística; no declara una licencia CC ni detalla expresamente la redistribución del recurso XLSX de marzo de 2026. El aviso de la sede electrónica no se extrapola al dataset del portal. | **Pendiente de confirmar las condiciones concretas de redistribución del recurso actual antes de publicar sus datos derivados.** No se incluye el XLSX bruto. |
| Fotografías | Autor, fuente, URL de licencia, fecha y transformación por foto en `data/reference/municipality_photos/catalog.json`. | Conservar catálogo y atribuciones visibles. Las licencias de las imágenes no cubren el código. |
| Leaflet | Licencia del proveedor incluida con los archivos locales en `assets/vendor/leaflet/`. | Conservar avisos. |

Las tablas del IVP incluyen resultados derivados de las fuentes anteriores; excluir un fichero bruto no resuelve por sí solo las condiciones de sus datos derivados. La copia de revisión contiene esas tablas para validar funcionalidad, pero **no constituye una autorización para publicarlas**.

## Servicios de mapas y rutas

La aplicación utiliza proveedores externos indicados en los componentes de mapa y `src/service_routes.py`. Las atribuciones del mapa deben mantenerse. El endpoint OSRM público calcula rutas orientativas cuando el usuario las pide, con caché local; no debe tratarse como un servicio con SLA ni usarse para descargas masivas. [Política del servicio de rutas FOSSGIS](https://routing.openstreetmap.de/).

## Privacidad y metadatos

Se excluyen secretos, archivos de autenticación, cachés, entornos virtuales, temporales, el historial de investigación descargado y archivos locales de URL pública. En el catálogo de fuentes se convierten los prefijos de rutas personales a referencias relativas, conservando URLs, fechas, licencias y huellas de origen. El inventario identifica esta transformación de metadatos. Los valores municipales y del IVP permanecen iguales.
