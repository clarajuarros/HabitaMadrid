# Despliegue de HabitaMadrid

## Antes de publicar

La primera publicación requiere revisión del inventario exacto de archivos y autorización de la propietaria. No sobrescribir repositorios ni cambiar visibilidad sin esa confirmación. Resolver también los puntos pendientes de licencia del informe de fuentes. El repositorio preferido es `clarajuarros/HabitaMadrid`; su existencia y los repositorios privados deben verificarse con la cuenta conectada.

## Streamlit Community Cloud

1. Iniciar sesión en [Streamlit Community Cloud](https://share.streamlit.io/) y autorizar el acceso al repositorio de GitHub.
2. Seleccionar el repositorio autorizado, rama `main` y archivo de entrada `app.py`.
3. En Advanced settings seleccionar **Python 3.14**. Las versiones de `requirements.txt` están fijadas para esta versión.
4. No copiar secretos al repositorio. Si en el futuro se requieren, configurarlos en los ajustes seguros del proveedor.
5. Elegir un subdominio disponible; no se presupone que `habitamadrid.streamlit.app` esté libre.
6. Desplegar y revisar los registros. Las rutas se resuelven desde el proyecto, sin depender de macOS.
7. Abrir la URL HTTPS asignada y realizar la comprobación pública indicada abajo.

La configuración no fuerza la dirección ni el puerto del ordenador local. Se conservan los colores de HabitaMadrid. No requiere paquetes apt ni servicios de pago.

## Comprobación pública pendiente

Verificar Inicio, las siete estrellas, Top 5, mapa, búsqueda, ficha, cuestionario, preferencias, comparación, vivienda, localizador, reinicio y enlaces compartidos. Revisar una ventana de escritorio y pantallas de iPhone/Safari y Android/Chrome. Comprobar las teselas y una ruta externa desde el servidor desplegado. Repetir acceso desde otro dispositivo y conexión. Los ensayos locales no sustituyen estas pruebas.

Tras obtener una URL real, añadirla al README. El botón de compartir utiliza la URL del navegador; opcionalmente `HABITAMADRID_PUBLIC_URL` permite establecer una URL canónica. No incluir `.streamlit/public_url.txt`, que puede contener una dirección local o temporal.

Generar el QR solo después de verificar el destino HTTPS y leer el QR desde otro dispositivo.

## Actualizaciones

Modificar y validar en local. Actualizar los datos mediante la cadena de preparación original y volver a exportar el snapshot con `scripts/prepare_cloud_release.py` desde el proyecto completo. Revisar cambios y el inventario antes de incorporar datos. Subir commits a la rama `main`; Community Cloud actualizará la app. No editar manualmente las huellas para encubrir un dato cambiado.

## Recursos y servicios externos

La base SQLite de horarios se abre en modo lectura; las consultas no necesitan descargar el GTFS completo. Los datos principales usan la caché de Streamlit. Las rutas se guardan en una caché local prescindible que el hosting puede borrar.

Las teselas/cartografía de terceros, enlaces de las fuentes y el servidor público OSRM dependen de su disponibilidad y condiciones. No proporcionan una garantía de servicio para tráfico masivo. Si se esperan muchos usuarios, revisar la capacidad y acordar un proveedor antes de cambiar la configuración. No realizar descargas masivas de teselas ni precarga de rutas.

Documentación de referencia:
- [Desplegar una app](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).
- [Dependencias y entorno Linux](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).
- [Límites de archivos GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
