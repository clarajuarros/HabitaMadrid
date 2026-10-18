"""Service locator: official town reference points and on-demand driving routes."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from pyproj import Transformer

from src.app_presenter import esc, number
from src.app_services import service_listing
from src.service_origins import load_urban_origins
from src.service_routes import coordinates, get_driving_route, straight_distance

ROOT = Path(__file__).resolve().parents[1]
LABELS = {"sanidad": "Atención primaria", "hospitales": "Hospitales · catálogo parcial",
          "educacion": "Educación · todas las tipologías", "transporte": "Autobús interurbano · fuente parcial"}


@st.cache_data(show_spinner=False)
def urban_origins(root, signature, _map_data):
    return load_urban_origins(root, _map_data)


def service_map_choices(tables, code, kind, *, origin=None):
    """Use the same official origin for candidate ordering and displayed distance.

    Only this locator changes origin. Published IVP snapshots and other lists
    retain their original reference and calculation methodology.
    """
    if origin is not None:
        source = coordinates([origin["longitud"], origin["latitud"]])
        x, y = Transformer.from_crs(4326, 25830, always_xy=True).transform(*source)
        metric_origins = tables["origenes"].copy()
        metric_origins.loc[metric_origins.codigo_ine.eq(code), ["x_etrs89_utm30", "y_etrs89_utm30"]] = [x, y]
        tables = {**tables, "origenes": metric_origins}
    listing = service_listing(tables, code, kind, nearby_limit=5)
    choices = pd.concat([listing["local"], listing["nearby"]], ignore_index=True)
    valid = np.isfinite(choices[["longitud", "latitud", "distancia_m"]]).all(axis=1)
    choices = choices.loc[valid].copy()
    if origin is not None:
        choices["distancia_m"] = [straight_distance(source, [row.longitud, row.latitud]) for row in choices.itertuples()]
    return choices.reset_index(drop=True)


def service_map_payload(origin, service, kind, route):
    if kind not in LABELS:
        raise ValueError("Categoría desconocida")
    return {"origin": {"name": str(origin["municipio"]), "place_name": str(origin["nombre_ign"]),
                       "id": str(origin["identificador_ign"]),
                       "position": coordinates([origin["longitud"], origin["latitud"]])},
            "destination": {"id": str(service["servicio_id"]), "name": str(service["nombre"]) or "Nombre no publicado",
                            "category": LABELS[kind], "position": coordinates([service["longitud"], service["latitud"]])},
            "route": route}


def build_service_map_html(origin, service, kind, route, *, root=ROOT):
    root = Path(root)
    payload = service_map_payload(origin, service, kind, route)
    # No source-provided name can close a script block or inject markup.
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    template = (root / "assets/service_map.html").read_text(encoding="utf-8")
    vendor = root / "assets/vendor/leaflet"
    return (template.replace("__LEAFLET_CSS__", (vendor / "leaflet.css").read_text())
            .replace("__LEAFLET_JS__", (vendor / "leaflet.js").read_text())
            .replace("__MAP_DATA__", data))


def km(metres):
    return number(metres / 1000, 2) + " km"


@st.fragment
def render_service_map(tables, map_data, code):
    panel = st.expander("Localiza un servicio y su distancia desde el municipio", expanded=False,
                        key="localizador_servicios_abierto", on_change="rerun")
    if not panel.open:
        return
    with panel:
        directory = ROOT / "data/reference/urban_centres"
        try:
            signature = ((directory / "centros_urbanos.csv").stat().st_mtime_ns,
                         (directory / "manifest.json").stat().st_mtime_ns)
            origins = urban_origins(str(ROOT), signature, map_data)
            origin = origins.set_index("codigo_ine").loc[code]
        except (OSError, ValueError, KeyError):
            st.error("No hay un centro urbano verificado disponible. No se utilizará el centroide municipal como sustituto.")
            return
        st.markdown("**Origen: " + str(origin["municipio"]) + " · centro urbano**")
        st.caption("Punto de referencia de la localidad publicado por el IGN: " + str(origin["nombre_ign"]) + ".")
        kind = st.selectbox("Categoría para localizar", list(LABELS), format_func=LABELS.get, key="servicio_mapa_categoria")
        choices = service_map_choices(tables, code, kind, origin=origin)
        if choices.empty:
            st.info("No hay datos disponibles de servicios con coordenadas verificadas para esta selección.")
            return
        rows = choices.set_index("servicio_id")
        def label(identifier):
            row = rows.loc[identifier]
            return (str(row["nombre"]) or "Nombre no publicado") + " · " + str(row["municipio"])
        selected = st.selectbox("Servicio para mostrar", choices["servicio_id"].tolist(), format_func=label,
                                key=f"servicio_mapa_{code}_{kind}")
        row = rows.loc[selected].copy()
        row["servicio_id"] = selected
        source, target = [float(origin.longitud), float(origin.latitud)], [float(row.longitud), float(row.latitud)]
        with st.spinner("Consultando el recorrido en coche…"):
            route = get_driving_route(source, target, ROOT / "data/cache/service_routes")
        st.markdown("**" + str(row["nombre"]) + "** · " + LABELS[kind])
        if str(row.get("direccion", "")).strip():
            st.caption(str(row["direccion"]))
        if route["status"] == "ok":
            duration = "Menos de 1 min" if route["duration_s"] < 60 else str(math.ceil(route["duration_s"] / 60)) + " min"
            left, right = st.columns(2)
            left.metric("Recorrido en coche", km(route["distance_m"]))
            right.metric("Tiempo estimado", duration)
            st.caption("Estimación sin tráfico en tiempo real. " + km(row["distancia_m"]) + " en línea recta.")
            if max(route["snap_distances_m"]) > 10:
                st.caption("Ruta entre accesos de la red viaria: el origen queda a " + number(route["snap_distances_m"][0])
                           + " m y el servicio a " + number(route["snap_distances_m"][1])
                           + " m de esos accesos. Los tramos discontinuos no se incluyen en el tiempo en coche.")
        else:
            st.metric("Distancia en línea recta", km(row["distancia_m"]))
            st.info(route["reason"] + " Se muestra únicamente la línea recta; tiempo de viaje no disponible.")
        st.iframe(build_service_map_html(origin, row, kind, route), height=485,
                  alt="Mapa del centro urbano y del servicio seleccionado con su recorrido")
        st.caption("Rutas: [OSRM / FOSSGIS](https://routing.openstreetmap.de/about.html) · "
                   "Datos © [OpenStreetMap](https://www.openstreetmap.org/copyright) · "
                   "[Corregir el mapa](https://www.openstreetmap.org/fixthemap)")
        st.caption("Origen: [Nomenclátor Geográfico del IGN](https://www.ign.es/web/rcc-nomenclator-nacional). "
                   "Se reutiliza el mismo punto urbano en cada consulta de este localizador. "
                   "Puede diferir del punto mostrado por Google Maps.")
        with st.expander("Coordenadas, origen de los datos y alcance"):
            st.write("Origen (latitud, longitud): " + f"{source[1]:.7f}, {source[0]:.7f}")
            st.write("Destino (latitud, longitud): " + f"{target[1]:.7f}, {target[0]:.7f}")
            st.caption(str(origin.identificador_ign) + " · " + str(origin.tipo_ign))
            if route["status"] == "ok":
                st.caption("Ruta consultada: " + route["fetched_at"] + ". Caché máxima: 24 horas.")
            st.caption("Se ofrecen los servicios locales y cinco de otros municipios próximos en línea recta; "
                       "el recorrido en coche se consulta para el destino elegido. Los registros sin coordenadas "
                       "se conservan en los listados de la ficha. Las rutas y estos centros urbanos no recalculan el IVP publicado.")
            if kind == "educacion":
                st.caption("Este selector incluye todas las tipologías educativas; el IVP utiliza diez tipologías generales.")
