# Despliegue de HabitaMadrid

## Configuración de la aplicación

Repositorio autorizado: [clarajuarros/HabitaMadrid](https://github.com/clarajuarros/HabitaMadrid), rama `main`, archivo de entrada `app.py`. Dirección pública: [HabitaMadrid](https://habitamadrid.streamlit.app/). Streamlit Community Cloud utiliza **Python 3.14** y las versiones fijadas en `requirements.txt`.

La propietaria ha autorizado activar la edición IVP 2.0 de transporte programado con 117 municipios puntuables en transporte y 38 sin puntuación suficiente. Esta documentación describe la edición y su procedimiento de despliegue; la verificación externa se registra después de publicar y reiniciar.

Conservar las atribuciones y condiciones del [inventario de fuentes](FUENTES_Y_LICENCIAS.md). No incluir secretos ni archivos locales de autenticación. Si se requieren claves en el futuro, configurarlas en los ajustes seguros del proveedor. Las rutas se resuelven desde el proyecto, sin depender de macOS; no se fuerza la dirección ni el puerto del ordenador local.

## Preparar una edición completa

1. Validar la investigación y exportar a una carpeta nueva con `scripts/prepare_cloud_release.py` desde el proyecto completo. Este exportador conserva intencionalmente el **baseline IVP 1.0**, no activa por sí solo el transporte 2.0.
2. En la copia de ejecución que se va a distribuir, aplicar `scripts/prepare_transport_revision.py` con las evidencias diarias y las decisiones de cobertura verificadas. Añadir `--publication-status active`, `--activate-local` y los archivos de evidencia usados en la revisión. El script actualiza conjuntamente las tablas derivadas, el manifiesto de la edición y su declaración en el snapshot.
3. Ejecutar las pruebas y revisar el inventario, las huellas, las ausencias y la coherencia de la documentación. No sustituir las ausencias por distancia a una parada ni limpiar brechas de cobertura solo para ampliar el ranking.
4. Incorporar código, datos y manifiestos en el mismo commit. Subir normalmente a `main`, sin sobrescribir historial ajeno. No editar huellas manualmente para encubrir un dato cambiado.

Ejemplo de la segunda fase, desde la raíz de la copia de ejecución:

```bash
python scripts/prepare_transport_revision.py \
  --offer RUTA/offer_daily.json \
  --coverage RUTA/coverage_decisions.json \
  --evidence RUTA/audited_pdf_supplement.json \
  --evidence RUTA/feed_audit.json \
  --publication-status active \
  --activate-local
python -m unittest discover -s tests
```

Las rutas del ejemplo son los archivos de preparación ya contrastados, no descargas nuevas ni rutas privadas para el repositorio. El paso de activación afecta a la copia local; **no publica ni reinicia Cloud**. Las fuentes originales y las decisiones se describen en [Transporte, 19 de octubre de 2026](TRANSPORTE_2026-10-19.md).

## Aplicar la actualización en Streamlit Community Cloud

Después de que Cloud haya descargado el commit completo, usar **Manage app → Reboot app → Reboot** y recargar la página. El servidor usa `fileWatcherType = "none"` para evitar la expulsión de módulos durante actualizaciones con sesiones concurrentes. El reinicio carga la edición completa de código y datos; ver texto nuevo no demuestra por sí solo que se hayan renovado los callbacks de una sesión anterior.

Revisar los registros y comprobar que la metodología activa es `ivp-2.0.0-transporte-programado`. No publicar datos sellados por separado de sus manifiestos. Para desarrollo local con recarga de código puede usarse `streamlit run app.py --server.fileWatcherType=poll`.

## Comprobación tras cada despliegue

Verificar Inicio, las siete estrellas, Top 5, mapa, búsqueda, ficha, cuestionario, preferencias, comparación, vivienda, localizador, reinicio y enlaces compartidos. En transporte, revisar un municipio puntuable y otro con cobertura insuficiente, la semana 19–25/10/2026 y la distinción entre horarios programados y servicio real. Comprobar que faltas de puntuación no se convierten en ceros y que los botones siguen funcionando después de navegar.

Revisar una ventana de escritorio y anchos de móvil, las teselas y una ruta externa desde el servidor desplegado. Los ensayos locales no sustituyen la comprobación pública. Registrar por separado las pruebas en dispositivos físicos o conexiones ajenas cuando se realicen, sin inferirlas de una ventana estrecha.

El botón de compartir utiliza la URL del navegador; opcionalmente `HABITAMADRID_PUBLIC_URL` establece la URL canónica. No incluir `.streamlit/public_url.txt`, que puede contener una dirección local o temporal. El QR debe apuntar al destino HTTPS verificado y leerse desde otro dispositivo.

## Recursos y servicios externos

La base SQLite de horarios complementarios se abre en modo lectura y conserva su fecha original; no es la tabla de cálculo de la nueva edición de transporte. Las consultas no necesitan descargar el GTFS completo. Los datos principales usan la caché de Streamlit. Las rutas se guardan en una caché local prescindible que el hosting puede borrar.

Las teselas/cartografía de terceros, enlaces de las fuentes y el servidor público OSRM dependen de su disponibilidad y condiciones. No proporcionan una garantía de servicio para tráfico masivo. No realizar descargas masivas de teselas ni precarga de rutas.

Documentación de referencia:

- [Desplegar una app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).
- [Dependencias y entorno Linux](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
- [Límites de archivos GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
