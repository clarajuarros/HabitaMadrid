# HabitaMadrid

La edición activa es **IVP 2.0 · transporte programado**
(`ivp-2.0.0-transporte-programado`).
La metodología activa se declara en `data/runtime_snapshot.json` y en
`data/processed/transporte_oferta/edition.json`. El baseline IVP 1.0 se conserva
sin alterar en `data/processed/ivp/`; no representa los rankings de la edición activa.
La revisión combina, con pesos iguales, las puntuaciones de expediciones
intermunicipales por día y destinos municipales directos por día, con la
semana fija 19–25/10/2026. Incluye Cercanías de Renfe, autobuses y Metro Ligero
de los feeds vigentes. Los recuentos PDF son parciales y no autorizan una
puntuación hasta reconciliar calendarios y variantes. No hay imputación por
distancia a una parada, seguimiento en tiempo real ni estimación de transbordos.

En esta revisión hay 117 municipios con transporte puntuable y 38 sin
puntuación suficiente; el IVP completo cubre 77 municipios en VC y 101 en VU.
La propuesta histórica de vivienda sigue archivada con su referencia IVP 1.0;
se rechaza mezclarla con la nueva edición de transporte.
[Metodología, cobertura y conservación de la base](docs/TRANSPORTE_2026-10-19.md).

Encuentra el municipio de la Comunidad de Madrid que mejor encaja contigo.
HabitaMadrid es una plataforma independiente en Python y Streamlit para explorar y comparar los **155 municipios de hasta 50.000 habitantes**. El mapa conserva los 179 términos municipales como contexto.

## Qué puedes hacer

- Explorar el mapa, buscar municipios y abrir sus fichas con fotografías reales.
- Consultar servicios educativos, sanitarios, transporte, población y vivienda.
- Adaptar las prioridades del IVP, completar el cuestionario y comparar hasta tres municipios.
- Localizar servicios y consultar rutas orientativas, horarios publicados y enlaces a las fuentes.
- Descargar resultados e informes y compartir una selección.

## Metodología

El Índice de Vida Ponderado (IVP) combina sanidad, educación, transporte y vivienda. El índice general asigna la misma importancia a las cuatro dimensiones. La edición activa **2.0** utiliza oferta y conectividad directa programada en transporte; sanidad, educación y vivienda conservan sus datos y referencias anteriores. Las ausencias de datos se conservan y no se sustituyen por ceros ni se redistribuyen sus pesos. Es una herramienta orientativa, no una medida absoluta de calidad de vida.

La ampliación de vivienda incluye contexto y una propuesta separada de revisión. Permanece marcada como `propuesta_no_activada` y no sustituye al IVP general. Las fuentes, unidades, periodos y limitaciones se muestran en «Cómo se calcula y fuentes» y en la pestaña Vivienda.

## Ejecutar

Requiere Python **3.14**. Desde la raíz del repositorio:

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

En Windows, activa el entorno con `.venv\Scripts\activate`. No se necesitan claves API para la consulta normal. Los mapas y rutas requieren acceso a Internet para sus servicios externos.

```bash
python -m unittest discover -s tests
```

## Estructura y datos elaborados

- `app.py`: entrada de la aplicación.
- `src/`: navegación, interfaz, lectores y cálculos.
- `assets/`: estilos, identidad de siete estrellas y Leaflet con su licencia.
- `data/processed/`: tablas municipales, IVP, geometrías y vivienda elaboradas.
- `data/reference/`: fotografías con atribución, descripciones, centros urbanos y horarios.
- `data/runtime_snapshot.json`: tamaños y SHA-256 de los datos desplegados.
- `data/runtime_provenance.json`: referencias y huellas de la cadena de preparación verificada al exportar.
- `docs/`: despliegue, inventario de fuentes y condiciones de reutilización.
- `tests/`: comprobaciones de la edición distribuida.

La copia de ejecución contiene los datos consumidos por la web. Los archivos brutos, páginas descargadas, entornos virtuales y archivos intermedios de investigación permanecen en el proyecto de preparación. No se ejecutan procesos de recopilación al arrancar el servidor. Las tablas de cálculo se conservan byte por byte. En el catálogo de fuentes se reemplazan únicamente los prefijos de rutas personales por rutas relativas.

Las huellas detectan alteraciones o archivos ausentes; no constituyen una firma digital ni una prueba independiente de autenticidad. Las fuentes conservan sus fechas: el servicio no es información en tiempo real.

## Fuentes y condiciones

Comunidad de Madrid, INE, CRTM, Renfe, IGN y MIVAU/SERPAVI, además de las fotografías atribuidas individualmente en `data/reference/municipality_photos/catalog.json`. Consulta `docs/FUENTES_Y_LICENCIAS.md` antes de redistribuir.

**[Powered by CRTM](https://www.crtm.es/)**. Información de transporte elaborada a partir de datos del CRTM. Los centros urbanos incluyen obra derivada del IGN, Nomenclátor Geográfico Básico de España. HabitaMadrid no es una herramienta oficial ni implica patrocinio de estos organismos.

Cercanías: [Horarios de Renfe](https://data.renfe.com/dataset/horarios-cercanias), descarga del 19/10/2026, bajo [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). HabitaMadrid transforma los recorridos y calendarios en agregados municipales; no modifica el fichero de origen ni implica respaldo de Renfe. El ZIP bruto no se incluye en esta distribución.

Los datos y recursos de terceros conservan sus propias condiciones. Esta preparación no concede automáticamente una licencia abierta al código original de HabitaMadrid.

## Despliegue y actualizaciones

Preparado para Streamlit Community Cloud: rama `main`, archivo `app.py`, Python 3.14. Consulta `docs/DESPLIEGUE.md`. Los nuevos commits en la rama desplegada se aplican mediante el mecanismo de actualización del proveedor.

**Web pública:** [Abrir HabitaMadrid](https://habitamadrid.streamlit.app/).

Repositorio: [clarajuarros/HabitaMadrid](https://github.com/clarajuarros/HabitaMadrid).

## Revisión municipal del 18 de octubre de 2026

[Informe de navegación, contraste INE y calidad de datos](docs/AUDITORIA_2026-10-18.md). Se mantienen las 179 identidades oficiales y los 155 municipios elegibles; los datos de edad, hogares y viviendas son contexto y no modifican el IVP.

Este informe documenta la edición del 18 de octubre. Sus afirmaciones sobre conservar el IVP 1.0 describen aquel estado; la [revisión de transporte del 19 de octubre](docs/TRANSPORTE_2026-10-19.md) declara el cambio posterior de metodología y cobertura.

La auditoría offline puede repetirse con `python scripts/audit_data_quality.py`; escribe un informe nuevo en `reports/data_quality/`. Las fuentes INE agregadas se conservan comprimidas, con fechas y huellas, en `data/raw/ine_municipal/`. El contraste completo con el Excel original de SERPAVI requiere la copia de investigación.


### Reinicio requerido al publicar

El servidor usa `fileWatcherType = "none"` para impedir la expulsión de módulos durante un `git pull` con sesiones concurrentes. Después de publicar y de que Cloud haya descargado el commit, usar **Manage app → Reboot app → Reboot** y recargar la página. El reinicio carga código y manifiestos como una edición completa. Las actualizaciones de datos conservan la invalidación de caché por firma de archivos. Para desarrollo local con recarga de código, puede ejecutarse `streamlit run app.py --server.fileWatcherType=poll`. No se deben publicar archivos de datos sellados por separado de su manifiesto.

`scripts/prepare_cloud_release.py` exporta intencionalmente la base histórica IVP 1.0. Para preparar una distribución con la edición 2.0 hay que aplicar después `scripts/prepare_transport_revision.py` con las evidencias y decisiones de cobertura verificadas, `--publication-status active` y `--activate-local`. El procedimiento completo figura en [Despliegue](docs/DESPLIEGUE.md); la exportación de la base por sí sola no conserva la revisión activa.
