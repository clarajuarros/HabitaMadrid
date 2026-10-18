import os
import pandas as pd
import requests

# Diccionario centralizado con todas las fuentes de datos oficiales de HabitaMadrid
FUENTES_DATOS = {
    "sanidad_municipios": {
        "url": "https://datos.comunidad.madrid/dataset/6dc2b24b-ebfd-4ad6-bde0-eb4edca6f860/resource/631ebd15-c19f-4984-9ad5-bb4b8554b5e0/download/recursos-y-asistencia-sanitaria.-centros-por-tipo-de-centro.-municipios.csv",
        "output": "data/raw/sanidad_municipios.csv",
    },
    "establecimientos_detallados": {
        "url": "https://datos.comunidad.madrid/dataset/d8a0a444-adf5-4c04-8999-0eac3de52cb7/resource/2948b4da-8b39-42b7-b667-779a5284f39d/download/centros_servicios_establecimientos_sanitarios.csv",
        "output": "data/raw/establecimientos_sanitarios_detallado.csv",
    },
    "centros_educativos": {
        "url": "https://datos.comunidad.madrid/dataset/12e0fac3-e092-42aa-ab98-10f970ae1793/resource/2cc0fa65-ae43-4665-a859-02461a364ea2/download/total-centros-escolares-por-ensenanza-que-imparten.csv",
        "output": "data/raw/centros_educativos.csv",
    },
    "transporte_municipios": {
        "url": "https://datos.comunidad.madrid/dataset/d09d19c6-26bf-4f72-9888-1bb2d743b3f0/resource/97f5a0dc-c5a5-43c5-b2e3-3e62fc07c322/download/transporte-urbano-publico.-infraestructura.-municipios.csv",
        "output": "data/raw/transporte_municipios.csv",
    },
}


def descargar_dataset(nombre_clave):
  """Función genérica para descargar cualquier dataset registrado en el diccionario."""
  if nombre_clave not in FUENTES_DATOS:
    print(f"Error: La fuente '{nombre_clave}' no está registrada.")
    return

  info = FUENTES_DATOS[nombre_clave]
  print(f"Descargando [{nombre_clave}]...")

  try:
    response = requests.get(info["url"])
    if response.status_code == 200:
      os.makedirs("data/raw", exist_ok=True)
      with open(info["output"], "wb") as f:
        f.write(response.content)
      print(f"¡Éxito! Guardado en {info['output']}")
    else:
      print(f"Error HTTP {response.status_code} al descargar {nombre_clave}")
  except Exception as e:
    print(f"Ocurrió un error en la descarga de {nombre_clave}: {e}")


def descargar_todo():
  """Ejecuta la descarga de todas las fuentes disponibles."""
  for clave in FUENTES_DATOS:
    descargar_dataset(clave)


if __name__ == "__main__":
  # Puedes elegir si quieres descargar todo de golpe o invocar una específica:
  # descargar_dataset('sanidad_municipios')
  # descargar_dataset('establecimientos_detallados')

  print("=== INICIANDO INGESTA DE DATOS DE HABITAMADRID ===")
  descargar_todo()
