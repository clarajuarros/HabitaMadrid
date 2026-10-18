"""Revisión sanitaria offline, conservadora ante ubicaciones contradictorias.

Usa una sola identidad de directorio oficial y sus dos categorías publicadas;
no mezcla instalaciones IDEM ni infiere coordenadas desde direcciones o nombres.
"""
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

import geopandas as gpd
import numpy as np
import pandas as pd

from src.geo_common import assign_points
from src.integration_common import IntegrationError
from src.municipalities import normalized_name


SOURCE_ID = "cm_directorio_atencion_primaria"
INDICATOR_ID = "distancia_atencion_primaria_directorio_m"
DIRECTORY_PATH = "/centros/buscador-centros-sanitarios"
CATEGORIES = {"centros_salud": ("687", "centro_salud"), "consultorios": ("688", "consultorio")}


def _json(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise IntegrationError("Evidencia sanitaria JSON inválida.") from exc


def _input(path):
    content = path.read_bytes()
    return {"path": str(path.resolve()), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _verified(root, record):
    relative = Path(record["archivo"])
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()):
        raise IntegrationError("Ruta sanitaria fuera de la evidencia autorizada.")
    observed = _input(path)
    if any(observed[key] != record.get(key) for key in ["bytes", "sha256"]):
        raise IntegrationError("Ha cambiado la evidencia sanitaria " + record["archivo"])
    return path.read_bytes(), observed


def parse_directory(content):
    """Lee exclusivamente puntos del mapa público embebido, sin ejecutar HTML."""
    text = content.decode("utf-8")
    match = re.search(r'<script type="application/json" data-drupal-selector="drupal-settings-json">(.*?)</script>', text, re.S)
    if not match:
        raise IntegrationError("El directorio no contiene ajustes Leaflet publicados.")
    maps = list(_json(match.group(1)).get("leaflet", {}).values())
    if len(maps) != 1 or not isinstance(maps[0].get("features"), list):
        raise IntegrationError("No se identifica un único mapa sanitario completo.")
    rows = []
    for item in maps[0]["features"]:
        link = re.search(r'<a href="([^"]+)"[^>]*>(.*?)</a>', item.get("popup", {}).get("value", ""), re.S)
        identifier = str(item.get("entity_id", "")).strip()
        if not link or not identifier:
            raise IntegrationError("Punto sanitario sin identidad y ficha verificable.")
        url = "https://www.comunidad.madrid" + html.unescape(link.group(1))
        if not urlparse(url).path.startswith("/centros/"):
            raise IntegrationError("Enlace sanitario ajeno al directorio oficial.")
        rows.append({"registro_id": "cm_directorio:" + identifier, "entity_id": identifier,
                     "nombre": html.unescape(re.sub("<[^>]+>", "", link.group(2))).strip(),
                     "url_ficha": url, "latitud_directorio": item.get("lat"),
                     "longitud_directorio": item.get("lon"), "tipo_geometria_directorio": item.get("type", "")})
    if not rows or len({r["registro_id"] for r in rows}) != len(rows):
        raise IntegrationError("Directorio vacío o con identidad sanitaria duplicada.")
    return rows


def parse_health_card(content):
    documents = [_json(text) for text in re.findall(r'<script type="application/ld\+json">(.*?)</script>', content.decode("utf-8"), re.S)]
    candidates = [d for d in documents if isinstance(d, dict) and d.get("geo")]
    if len(candidates) != 1:
        raise IntegrationError("Ficha sanitaria sin un único objeto geográfico JSON-LD.")
    document = candidates[0]
    if document.get("@context") != "https://schema.org" or document["geo"].get("@type") != "GeoCoordinates":
        raise IntegrationError("CRS sanitario no acreditado por GeoCoordinates WGS84.")
    modified = re.search(r'<meta property="article:modified_time" content="([^"]+)"', content.decode("utf-8"))
    locality = re.search(r'<div class="field field--name-term-madrid-municipality[^>]*>(.*?)</div>', content.decode("utf-8"), re.S)
    municipality = html.unescape(re.sub("<[^>]+>", "", locality.group(1))).strip() if locality else ""
    return {"municipio_publicado": municipality,
            "localidad_jsonld": document.get("address", {}).get("addressLocality", ""),
            "nombre_ficha": document.get("name", ""), "latitud_publicada": document["geo"].get("latitude"),
            "longitud_publicada": document["geo"].get("longitude"),
            "fecha_modificacion_ficha": modified.group(1) if modified else ""}


def load_health_evidence(research_dir):
    root = Path(research_dir).resolve()
    manifest_path = root / "descargas.json"
    cards_manifest_path = root / "fichas_descargas.json"
    manifest, card_manifest = _json(manifest_path.read_text()), _json(cards_manifest_path.read_text())
    records = {r["archivo"]: r for r in manifest.get("registros", [])}
    if len(records) != len(manifest.get("registros", [])):
        raise IntegrationError("Manifiesto sanitario duplicado.")
    inputs = [_input(manifest_path), _input(cards_manifest_path)]
    full_record = records.get("directorio_completo.html")
    if not full_record:
        raise IntegrationError("Falta el universo del directorio sanitario.")
    full_content, item = _verified(root, full_record)
    inputs.append(item)
    full = {r["registro_id"]: r for r in parse_directory(full_content)}
    card_records = {r["url"]: r for r in card_manifest.get("registros", [])}
    if len(card_records) != len(card_manifest.get("registros", [])):
        raise IntegrationError("Fichas sanitarias duplicadas.")
    rows, dates = [], []
    for key, (category_number, category) in CATEGORIES.items():
        record = records.get(key + ".html")
        if not record:
            raise IntegrationError("Falta la categoría sanitaria " + key)
        parsed_url = urlparse(record.get("url", ""))
        if (parsed_url.hostname != "www.comunidad.madrid" or parsed_url.path != DIRECTORY_PATH
                or parse_qs(parsed_url.query).get("f[0]") != ["tipo_centro_paragraph:" + category_number]):
            raise IntegrationError("La selección sanitaria no acredita su categoría oficial.")
        content, item = _verified(root, record)
        inputs.append(item)
        category_rows = parse_directory(content)
        published = re.search(r'data-drupal-facet-item-value="' + category_number + r'" data-drupal-facet-item-count="(\d+)"', full_content.decode())
        if (not published or len(category_rows) != int(published.group(1))
                or len(category_rows) != record.get("registros")):
            raise IntegrationError("El mapa sanitario no cubre el conteo oficial de su categoría.")
        dates.append(record.get("descargado_utc", ""))
        for row in category_rows:
            if row["registro_id"] not in full or row != full[row["registro_id"]]:
                raise IntegrationError("Las instantáneas sanitarias de categoría y universo difieren.")
            card_record = card_records.get(row["url_ficha"])
            if not card_record:
                raise IntegrationError("Falta una ficha sanitaria para verificar municipio y CRS.")
            packed, item = _verified(root, card_record)
            inputs.append(item)
            try:
                card = gzip.decompress(packed)
            except (OSError, EOFError) as exc:
                raise IntegrationError("Ficha sanitaria comprimida no válida.") from exc
            if (len(card) != card_record.get("bytes_descomprimidos") or
                    hashlib.sha256(card).hexdigest() != card_record.get("sha256_descomprimido")):
                raise IntegrationError("Ha cambiado el contenido descomprimido de una ficha sanitaria.")
            rows.append({**row, **parse_health_card(card), "categoria": category, "fuente": SOURCE_ID})
    if len({r["registro_id"] for r in rows}) != len(rows):
        raise IntegrationError("Una instalación aparece en las dos categorías sanitarias.")
    day_set = {d[:10] for d in dates if len(d) >= 10}
    if len(day_set) != 1:
        raise IntegrationError("No hay una fecha de consulta sanitaria común acreditada.")
    return pd.DataFrame(rows), inputs, "consulta_" + next(iter(day_set))


def apply_verified_repairs(points, research_dir):
    """Sustituye geometría por identidad oficial exacta, conservando la original."""
    root = Path(research_dir).resolve()
    folder = root / "reparaciones"
    points = points.copy()
    points["latitud_calculo"] = points.latitud_publicada
    points["longitud_calculo"] = points.longitud_publicada
    points["reparacion_aplicada"] = False
    points["metodo_geometria"] = "jsonld_geocoordinates_wgs84"
    points["url_fuente_geometria"] = points.url_ficha
    points["alertas_geometria_json"] = "[]"
    if not (folder / "normalizacion_sugerida.json").exists():
        return points, [], []
    manifest_path, normalization_path = folder / "descargas.json", folder / "normalizacion_sugerida.json"
    records = {r["archivo"]: r for r in _json(manifest_path.read_text()).get("registros", [])}
    repairs = _json(normalization_path.read_text()).get("registros", [])
    inputs = [_input(manifest_path), _input(normalization_path)]
    # También se incorpora la evidencia contextual que documenta direcciones y vigencia.
    contents = {}
    for filename, record in records.items():
        content, observed = _verified(folder, record)
        contents[filename] = content
        inputs.append(observed)
    identifiers, applied = set(), []
    for repair in repairs:
        identifier = repair.get("servicio_id", "")
        selected = points.registro_id.eq(identifier)
        if selected.sum() != 1 or identifier in identifiers:
            raise IntegrationError("Reparación sanitaria sin identidad única en el directorio.")
        identifiers.add(identifier)
        point = points.loc[selected].iloc[0]
        if repair.get("url_ficha") != point.url_ficha:
            raise IntegrationError("La reparación sanitaria apunta a otra ficha de centro.")
        filename = repair.get("archivo", "")
        record = records.get(filename, {})
        if record.get("sha256") != repair.get("sha256_archivo") or record.get("url") != repair.get("fuente_url"):
            raise IntegrationError("La reparación no coincide con la descarga oficial verificada.")
        method = repair.get("metodo")
        if method == "idem_url_ficha_exacta":
            source_url = urlparse(record["url"])
            collection = _json(contents[filename])
            features = collection.get("features", [])
            if (source_url.hostname != "idem.comunidad.madrid" or parse_qs(source_url.query).get("outSR") != ["4326"]
                    or len(features) != 1 or collection.get("properties", {}).get("exceededTransferLimit")):
                raise IntegrationError("La reparación IDEM no tiene CRS o universo explícito.")
            feature = features[0]
            if (feature.get("geometry", {}).get("type") != "Point"
                    or feature.get("properties", {}).get("url") != point.url_ficha):
                raise IntegrationError("IDEM no enlaza exactamente la misma ficha sanitaria.")
            longitude, latitude = feature["geometry"]["coordinates"]
        elif method == "libre_eleccion_enlace_ficha_exacto":
            source_url = urlparse(record["url"])
            ficha_path = root / "fichas" / (str(point.entity_id) + ".html.gz")
            packed = ficha_path.read_bytes()
            if hashlib.sha256(packed).hexdigest() != repair.get("sha256_ficha_archivada"):
                raise IntegrationError("La ficha que enlaza Libre Elección ha cambiado.")
            card = html.unescape(gzip.decompress(packed).decode())
            linked_urls = [urlparse(u) for u in re.findall(r'href="([^"]+)"', card)]
            direct = any(u.hostname == source_url.hostname and u.path == source_url.path for u in linked_urls)
            if source_url.hostname != "www.libreeleccion.sanidadmadrid.org" or not direct:
                raise IntegrationError("Libre Elección no está enlazado por identidad desde la ficha oficial.")
            iframe_urls = [urlparse(html.unescape(u)) for u in re.findall(r'<iframe[^>]+src="([^"]+)"', contents[filename].decode())]
            coordinates = [parse_qs(u.query).get("q", []) for u in iframe_urls if u.hostname == "maps.google.com"]
            if len(coordinates) != 1 or len(coordinates[0]) != 1:
                raise IntegrationError("Libre Elección no publica un único punto en su mapa.")
            try:
                latitude, longitude = [float(n) for n in coordinates[0][0].split(",")]
            except (ValueError, TypeError) as exc:
                raise IntegrationError("Coordenadas del mapa sanitario no válidas.") from exc
        else:
            raise IntegrationError("Método de reparación sanitaria no acreditado.")
        if (not np.isfinite([latitude, longitude]).all() or not (-90 <= latitude <= 90 and -180 <= longitude <= 180)
                or not np.isclose(latitude, repair.get("latitud"), atol=1e-10, rtol=0)
                or not np.isclose(longitude, repair.get("longitud"), atol=1e-10, rtol=0)):
            raise IntegrationError("La geometría de reparación no coincide con el punto publicado.")
        points.loc[selected, ["latitud_calculo", "longitud_calculo", "reparacion_aplicada", "metodo_geometria", "url_fuente_geometria", "alertas_geometria_json"]] = [
            latitude, longitude, True, method, record["url"], json.dumps(repair.get("alertas", []), ensure_ascii=False)]
        applied.append({"servicio_id": identifier, "metodo": method, "url_ficha": point.url_ficha,
                        "fuente_url": record["url"], "sha256_archivo": record["sha256"],
                        "latitud_original": point.latitud_publicada, "longitud_original": point.longitud_publicada,
                        "latitud_calculo": latitude, "longitud_calculo": longitude,
                        "direccion_fuente": repair.get("direccion_fuente", ""),
                        "alertas_json": json.dumps(repair.get("alertas", []), ensure_ascii=False)})
    return points, inputs, applied


def evaluate_health_catalog(master, points, municipalities, shared, origins, local_statistics, period):
    """Calcula distancias auditables; cualquier conflicto regional bloquea su uso global."""
    master = master.copy()
    master["codigo_ine"] = master.codigo_ine.astype(str)
    if master.codigo_ine.duplicated().any():
        raise IntegrationError("El maestro sanitario contiene municipios duplicados.")
    eligible = master.loc[master.elegible_hasta_50000.eq(True), "codigo_ine"].tolist()
    if origins.codigo_ine.duplicated().any() or set(origins.codigo_ine) != set(eligible):
        raise IntegrationError("Los orígenes sanitarios no coinciden con los municipios elegibles.")
    if points.registro_id.duplicated().any():
        raise IntegrationError("El catálogo sanitario contiene instalaciones duplicadas.")
    names = {}
    for _, row in master.iterrows():
        for column in ["municipio", "municipio_padron"]:
            value = str(row.get(column, "")).strip()
            if value:
                key = normalized_name(value)
                if key in names and names[key] != row.codigo_ine:
                    raise IntegrationError("Nombres municipales ambiguos en sanidad.")
                names[key] = row.codigo_ine
    # Correspondencias explícitas, nunca coincidencia aproximada ni arrastre espacial.
    # BOE-A-2009-16301 acredita el cambio de denominación de Horcajo.
    # La propia ficha 5271 titula Villavieja DEL Lozoya y escribe DE en municipio.
    aliases = {"horcajo de la sierra": "28070", "villavieja de lozoya": "28182"}
    for alias, code in aliases.items():
        if code in set(master.codigo_ine):
            names[alias] = code
    points = points.copy()
    if "latitud_calculo" not in points:
        points["latitud_calculo"] = points.latitud_publicada
        points["longitud_calculo"] = points.longitud_publicada
    points["codigo_ine_declarado"] = points.municipio_publicado.map(lambda n: names.get(normalized_name(str(n)), ""))
    points["estado_nombre_municipal"] = points.municipio_publicado.map(
        lambda n: "alias_documentado" if normalized_name(str(n)) in aliases else
        "no_publicado" if not str(n).strip() else "publicado")
    for column in ["latitud_publicada", "longitud_publicada", "latitud_directorio", "longitud_directorio", "latitud_calculo", "longitud_calculo"]:
        points[column] = pd.to_numeric(points[column], errors="coerce")
    finite = np.isfinite(points[["latitud_publicada", "longitud_publicada", "latitud_directorio", "longitud_directorio"]]).all(axis=1)
    consistent = (np.isclose(points.latitud_publicada, points.latitud_directorio, atol=1e-9, rtol=0) &
                  np.isclose(points.longitud_publicada, points.longitud_directorio, atol=1e-9, rtol=0))
    valid = (finite & points.latitud_publicada.between(-90, 90) & points.longitud_publicada.between(-180, 180)
             & consistent & points.tipo_geometria_directorio.eq("point"))
    valid &= np.isfinite(points[["latitud_calculo", "longitud_calculo"]]).all(axis=1) & points.latitud_calculo.between(-90, 90) & points.longitud_calculo.between(-180, 180)
    geometry = [gpd.points_from_xy([r.longitud_calculo], [r.latitud_calculo])[0] if good else None
                for r, good in zip(points.itertuples(), valid)]
    geographic = gpd.GeoDataFrame(points, geometry=geometry, crs=4326).to_crs(25830)
    assignment = assign_points(geographic, municipalities, shared)
    geographic = geographic.merge(assignment, on="registro_id", validate="one_to_one")
    unique = geographic.estado_asignacion_espacial.eq("municipio_unico")
    known = geographic.codigo_ine_declarado.ne("")
    conflict = unique & known & geographic.codigo_ine_declarado.ne(geographic.codigo_ine_espacial)
    exterior = ~known & geographic.estado_asignacion_espacial.eq("sin_coincidencia") & valid
    missing_declaration = geographic.municipio_publicado.astype(str).str.strip().eq("")
    geographic["motivo_revision"] = ""
    geographic.loc[exterior, "motivo_revision"] = "fuera_ambito_179_municipios"
    geographic.loc[~valid, "motivo_revision"] = "coordenadas_ausentes_o_contradictorias"
    geographic.loc[~exterior & ~unique & valid, "motivo_revision"] = geographic.loc[~exterior & ~unique & valid, "estado_asignacion_espacial"]
    geographic.loc[unique & ~known & ~missing_declaration, "motivo_revision"] = "municipio_declarado_no_reconocido"
    geographic.loc[conflict, "motivo_revision"] = "conflicto_municipio_declarado_espacial"
    geographic["apto_proximidad"] = unique & (known | missing_declaration) & ~conflict & valid
    geographic["codigo_ine"] = geographic.codigo_ine_espacial.where(geographic.apto_proximidad, "")
    geographic["utm_x_25830"] = geographic.geometry.x
    geographic["utm_y_25830"] = geographic.geometry.y
    geographic["crs_original"] = "EPSG:4326"
    accepted = geographic.loc[geographic.apto_proximidad].sort_values("registro_id")
    diagnostic = geographic.loc[~geographic.apto_proximidad, ["registro_id", "nombre", "municipio_publicado", "codigo_ine_declarado", "codigo_ine_espacial", "estado_asignacion_espacial", "motivo_revision", "url_ficha"]].copy()
    unresolved = diagnostic.loc[diagnostic.motivo_revision.ne("fuera_ambito_179_municipios")]
    # La exclusión de un destino de Madrid puede alterar el mínimo para cualquier origen.
    usable = len(unresolved) == 0 and len(accepted) > 0
    distances, observations = [], []
    for _, origin in origins.sort_values("codigo_ine").iterrows():
        coords = np.asarray([origin.x_etrs89_utm30, origin.y_etrs89_utm30], dtype=float)
        if not np.isfinite(coords).all():
            raise IntegrationError("Origen sanitario no finito.")
        if len(accepted):
            values = np.hypot(accepted.utm_x_25830.to_numpy() - coords[0], accepted.utm_y_25830.to_numpy() - coords[1])
            position = int(np.argmin(values))
            destination = accepted.iloc[position]
            distance = float(values[position])
            trace = {"origen_id": origin.get("origen_id", ""), "servicio_id": destination.registro_id,
                     "nombre_servicio": destination.nombre, "codigo_ine_destino": destination.codigo_ine,
                     "url_ficha": destination.url_ficha, "metodo": "distancia_euclidiana_epsg25830",
                     "catalogo_apto_ivp": bool(usable)}
        else:
            distance, trace = float("nan"), {"catalogo_apto_ivp": False}
        state = "disponible" if np.isfinite(distance) else "sin_destinos_validos"
        note = "Proximidad al directorio público; no mide tiempo, atención asignada, horario, disponibilidad ni capacidad."
        if not usable:
            note += " Uso IVP bloqueado globalmente por ubicaciones regionales sin resolver."
        observations.append({"codigo_ine": origin.codigo_ine, "valor": distance, "estado": state, "periodo": period,
                             "fuentes_json": json.dumps([SOURCE_ID]), "detalle_json": json.dumps(trace, ensure_ascii=False, sort_keys=True), "nota": note})
        distances.append({"codigo_ine": origin.codigo_ine, "distancia_m": distance, **trace})
    contrast = local_statistics.loc[local_statistics.codigo_ine.isin(eligible), ["codigo_ine", "centros_consultorios_publicados_2024"]].copy()
    if len(contrast) != len(eligible) or contrast.codigo_ine.duplicated().any():
        raise IntegrationError("Contraste sanitario municipal incompleto.")
    contrast["puntos_primaria_directorio_validos"] = contrast.codigo_ine.map(accepted.codigo_ine.value_counts()).fillna(0).astype(int)
    contrast["periodo_directorio"] = period
    contrast["diferencia_directorio_menos_estadistica"] = contrast.puntos_primaria_directorio_validos - contrast.centros_consultorios_publicados_2024
    contrast["interpretacion"] = "Fechas y universos distintos; la diferencia no prueba cierres ni ausencia real de atención."
    summary = {"usable": bool(usable), "indicador_id": INDICATOR_ID, "fuente": SOURCE_ID, "periodo": period,
               "registros_directorio": len(points), "categorias": {str(k): int(v) for k, v in points.categoria.value_counts().items()},
               "destinos_validos": len(accepted), "municipios_destinos_validos": int(accepted.codigo_ine.nunique()),
               "municipios_elegibles_con_punto": int(accepted.loc[accepted.codigo_ine.isin(eligible), "codigo_ine"].nunique()),
               "destinos_fuera_ambito": int(exterior.sum()), "destinos_regionales_sin_resolver": len(unresolved),
               "bloqueos": unresolved[["registro_id", "motivo_revision", "url_ficha"]].to_dict("records"),
               "regla_global": "Cualquier instalación regional con ubicación inválida o contradictoria impide usar todas las distancias en IVP.",
               "crs_evidencia": "Las fichas oficiales declaran schema.org/GeoCoordinates, latitude/longitude WGS84; se exige igualdad con el mapa Leaflet.",
               "crs_fuente_documentacion": "https://schema.org/GeoCoordinates", "crs_calculo": "EPSG:25830",
               "municipios_con_estadistica_mayor": int(contrast.diferencia_directorio_menos_estadistica.lt(0).sum()),
               "limitaciones": "Directorio de centros de salud y consultorios; no prueba apertura, atención asignada, horarios, capacidad o calidad. Se excluyen puntos exteriores a los 179 municipios."}
    return {"tables": {"sanidad_catalogo_revisado.csv": pd.DataFrame(geographic.drop(columns="geometry")),
                       "sanidad_distancias_revisadas.csv": pd.DataFrame(distances),
                       "sanidad_contraste_revisado.csv": contrast.sort_values("codigo_ine"),
                       "sanidad_diagnostico_revisado.csv": diagnostic},
            "observations": pd.DataFrame(observations), "summary": summary, "usable": bool(usable)}


def prepare_health_review(master, geography_dir, research_dir):
    geography_dir = Path(geography_dir).resolve()
    points, inputs, period = load_health_evidence(research_dir)
    points, repair_inputs, repairs = apply_verified_repairs(points, research_dir)
    inputs.extend(repair_inputs)
    filenames = ["limites_municipales.geojson", "jurisdicciones_compartidas.geojson", "origenes_municipales.csv"]
    inputs.extend(_input(geography_dir / filename) for filename in filenames)
    municipalities = gpd.read_file(geography_dir / filenames[0]).to_crs(25830)
    shared = gpd.read_file(geography_dir / filenames[1]).to_crs(25830)
    municipalities["codigo_ine"] = municipalities.codigo_ine.astype(str)
    for field in ["codigo_ine_1", "codigo_ine_2"]:
        shared[field] = shared[field].astype(str)
    if set(municipalities.codigo_ine) != set(master.codigo_ine.astype(str)):
        raise IntegrationError("Los límites sanitarios no coinciden con el maestro municipal completo.")
    origins = pd.read_csv(geography_dir / filenames[2], dtype={"codigo_ine": str}, keep_default_na=False)
    statistics_path = geography_dir.parent / "indicadores" / "alertas_cobertura_cruzada.csv"
    inputs.append(_input(statistics_path))
    statistics = pd.read_csv(statistics_path, dtype={"codigo_ine": str}, keep_default_na=False)
    result = evaluate_health_catalog(master, points, municipalities, shared, origins, statistics, period)
    result["summary"]["reparaciones_geograficas"] = len(repairs)
    result["summary"]["reparaciones"] = repairs
    result["tables"]["sanidad_reparaciones_aplicadas.csv"] = pd.DataFrame(repairs)
    result["inputs"] = list({r["path"]: r for r in inputs}.values())
    return result
