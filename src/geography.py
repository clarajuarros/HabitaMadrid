"""Fase 4: capas verificadas y proximidad geométrica reproducible, sin red."""
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import json
import platform
import shutil
import tempfile

import geopandas as gpd
import pandas as pd
import numpy as np

from src.geo_common import assign_points, require_metric_crs
from src.integration import load_validated_master, issue_summary, validate_output_location, canonical_source_id
from src.integration_common import IntegrationError, ISSUE_COLUMNS, file_sha256


def _record(path, expected=None, **extra):
    path = Path(path).resolve()
    digest = file_sha256(path)
    if expected and digest != expected:
        raise IntegrationError("La entrada no coincide con su huella registrada: " + str(path))
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size, **extra}


def verify_geographic_inputs(root):
    root = Path(root)
    master, master_validation = load_validated_master(root / "data/processed/municipios")
    integration = root / "data/processed/integracion"
    phase3_path = integration / "validacion_integracion.json"
    phase3 = json.loads(phase3_path.read_text())
    if phase3.get("fase") != 3 or phase3.get("regla") != "poblacion <= 50000":
        raise IntegrationError("Se requieren los derivados validados de la fase 3.")
    inputs = [_record(phase3_path), _record(root / "data/processed/municipios/validacion.json")]
    for name, record in phase3["archivos"].items():
        if Path(name).name != name:
            raise IntegrationError("Nombre de entrada inválido en el manifiesto de fase 3.")
        inputs.append(_record(integration / name, record["sha256"], tipo="derivado_fase_3"))
    if file_sha256(integration / "municipios.csv") != phase3["maestro_entrada"]["sha256"]:
        raise IntegrationError("El maestro de fase 3 difiere de su referencia validada.")
    if file_sha256(root / "data/processed/municipios/maestro_municipios.csv") != phase3["maestro_entrada"]["sha256"]:
        raise IntegrationError("El maestro cambió desde la fase 3; regenera la integración.")
    inputs.append(_record(root / "data/processed/municipios/maestro_municipios.csv"))
    catalog_path = root / "research/catalog/geography_transport_status.json"
    inputs.append(_record(catalog_path))
    catalog = json.loads(catalog_path.read_text())
    sources = []
    for filename, source_id in [("divisiones_administrativas.geojson", "idem_divisiones_administrativas"),
                                ("nucleos_poblacion_eiel_2023.geojson", "idem_nucleos_eiel_2023")]:
        item = next((r for r in catalog if r.get("name") == filename), None)
        if item is None:
            raise IntegrationError("Falta el manifiesto cartográfico de " + filename)
        inputs.append(_record(root / item["path"], item["sha256"], tipo="cartografia_original"))
        sources.append({"fuente": source_id, "archivo": item["path"], "url": item["url"],
                        "sha256": item["sha256"], "fecha_descarga_utc": item["retrieved_at"],
                        "crs": "EPSG:4326", "nota": "Salida WFS solicitada en EPSG:4326; cálculo EPSG:25830."})
    evidence_path = root / "research/geografia_2026-10-15/evidencia_crs.json"
    inputs.append(_record(evidence_path, tipo="evidencia_crs"))
    evidence = json.loads(evidence_path.read_text())
    supports = list(evidence.get("metadatos", {}).get("soportes_descargados", []))
    supports += [r for area in ("educacion", "sanidad") for r in evidence.get(area, {}).get("soportes", [])]
    for support in supports:
        inputs.append(_record(root / support["path"], support["sha256"], tipo="soporte_crs"))
    health_dir = root / "research/sanidad_geografica_2026-10-15"
    health_manifest = health_dir / "descargas.json"
    inputs.append(_record(health_manifest))
    records = json.loads(health_manifest.read_text())["registros"]
    for item in records:
        inputs.append(_record(health_dir / item["archivo"], item["sha256"], tipo="sanidad_geografica_oficial"))
        if item["archivo"] in {"centros_salud.geojson", "hospitales.geojson"}:
            sources.append({"fuente": "idem_centros_salud" if item["archivo"].startswith("centros") else "idem_hospitales",
                            "archivo": str((health_dir / item["archivo"]).relative_to(root)),
                            "url": item["url"], "sha256": item["sha256"],
                            "fecha_descarga_utc": item["descargado_utc"], "crs": "EPSG:4326",
                            "nota": "Capa oficial independiente; sin enlace supuesto al registro de autorizaciones."})
    for item in phase3["fuentes"]:
        if item["fuente"] in {"centros_educativos", "centros_servicios_establecimientos_sanitarios", "crtm_interurbanos_gtfs"}:
            sources.append({"fuente": item["fuente"], "archivo": item["path"], "url": item["url"],
                            "sha256": item["sha256"], "fecha_descarga_utc": item["fecha_descarga_utc"],
                            "crs": "EPSG:25830 según visor oficial" if item["fuente"] == "centros_educativos" else
                                   "EPSG:4326" if item["fuente"] == "crtm_interurbanos_gtfs" else "sin confirmar; no operativo",
                            "nota": "Procedencia y licencia completas en la validación de fase 3."})
    # Un soporte puede pertenecer a varias cadenas documentales: se verifica una vez.
    unique = {record["path"]: record for record in inputs}
    return master, master_validation, evidence, list(unique.values()), sources


def validate_geographic_results(master, boundaries, services, tables):
    from src.geo_proximity import CATEGORIES
    origins = boundaries["origins"]
    eligible = set(master.loc[master.elegible_hasta_50000, "codigo_ine"])
    if not origins.codigo_ine.is_unique or set(origins.codigo_ine) != eligible:
        raise IntegrationError("Se requiere exactamente un origen por municipio elegible.")
    assigned = assign_points(origins.rename(columns={"origen_id": "registro_id"})[["registro_id", "geometry"]],
                             boundaries["municipalities"], boundaries["shared_areas"])
    if not assigned.estado_asignacion_espacial.eq("municipio_unico").all():
        raise IntegrationError("Algún origen no tiene pertenencia municipal única.")
    expected = origins.set_index("origen_id").codigo_ine
    if not assigned.codigo_ine_espacial.eq(assigned.registro_id.map(expected)).all():
        raise IntegrationError("Algún origen no pertenece al municipio que representa.")
    require_metric_crs(services, "servicios")
    if not services.servicio_id.is_unique or services.servicio_id.eq("").any():
        raise IntegrationError("Identificadores de servicios vacíos o duplicados.")
    if (services.geometry.isna().any() or services.geometry.is_empty.any() or
            not services.geometry.is_valid.all() or not services.geometry.geom_type.eq("Point").all()):
        raise IntegrationError("Los servicios operativos deben tener puntos válidos.")
    if len(services) and not np.isfinite(np.column_stack([services.geometry.x, services.geometry.y])).all():
        raise IntegrationError("Hay servicios operativos con coordenadas no finitas.")
    if not set(services.codigo_ine).issubset(set(master.codigo_ine)):
        raise IntegrationError("Un servicio carece de municipio confirmado.")
    for filename in ("proximidad_servicios.csv", "proximidad_servicios_otros_municipios.csv"):
        table = tables[filename]
        if not set(table.codigo_ine_origen).issubset(eligible) or not set(table.servicio_id).issubset(set(services.servicio_id)):
            raise IntegrationError("Referencia incorrecta en " + filename)
        if len(table) and (not np.isfinite(table.distancia_m).all() or table.distancia_m.lt(0).any()):
            raise IntegrationError("Distancia negativa o no finita en " + filename)
        if table.duplicated(["origen_id", "categoria", "puesto"]).any():
            raise IntegrationError("Posición de proximidad repetida en " + filename)
    coverage = tables["cobertura_proximidad.csv"]
    expected_pairs = {(code, category) for code in eligible for category in CATEGORIES}
    actual_pairs = set(zip(coverage.codigo_ine_origen, coverage.categoria))
    if coverage.duplicated(["codigo_ine_origen", "categoria"]).any() or actual_pairs != expected_pairs:
        raise IntegrationError("La cobertura debe contener una fila por municipio elegible y categoría.")


def geographic_json(frame):
    """GeoJSON RFC 7946 en longitud/latitud; rechaza geometrías no finitas."""
    require_metric_crs(frame, "exportación")
    usable = frame.geometry.notna() & ~frame.geometry.is_empty
    if not frame.loc[usable].geometry.is_valid.all():
        raise IntegrationError("La exportación contiene geometrías inválidas.")
    geographic = frame.to_crs(4326)
    for geometry in geographic.loc[usable].geometry:
        if not np.isfinite(geometry.bounds).all():
            raise IntegrationError("Coordenadas no finitas en GeoJSON.")
    data = json.loads(geographic.to_json(na="null", drop_id=True, to_wgs84=True))
    # JSON estricto: también las propiedades deben estar libres de NaN/Infinity.
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def publish_geography(tables, layers, issues, validation, output_dir):
    output = Path(output_dir)
    validate_output_location(output, validation["entradas"])
    reserved = {"incidencias_geograficas.csv", "fuentes_geograficas.csv"}
    for name, frame in {**tables, **layers}.items():
        if Path(name).name != name or name in reserved or not frame.columns.is_unique:
            raise IntegrationError("Tabla o capa de salida inválida: " + name)
        if name not in layers and not name.endswith(".csv") or name in layers and not name.endswith(".geojson"):
            raise IntegrationError("Extensión de salida inválida: " + name)
    if output.exists() and not output.is_dir():
        raise IntegrationError("La salida geográfica ya existe y no es una carpeta.")
    if output.exists() and any(output.iterdir()):
        prior_path = output / "validacion_geografia.json"
        if not prior_path.exists():
            raise IntegrationError("La salida contiene archivos ajenos a la fase 4.")
        prior = json.loads(prior_path.read_text())
        allowed = set(prior.get("archivos", {})) | {"validacion_geografia.json"}
        if prior.get("fase") != 4 or set(p.name for p in output.iterdir()) - allowed:
            raise IntegrationError("La salida contiene archivos ajenos al conjunto regenerable de fase 4.")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".geografia-", dir=output.parent))
    backup = None
    try:
        tables = {**tables, "incidencias_geograficas.csv": pd.DataFrame(issues, columns=ISSUE_COLUMNS),
                  "fuentes_geograficas.csv": pd.DataFrame(validation["fuentes"])}
        schema = {}
        for name, frame in tables.items():
            frame.to_csv(stage / name, encoding="utf-8-sig", index=False)
            schema[name] = {"filas": len(frame), "columnas": [{"nombre": str(c), "tipo_en_memoria": str(frame[c].dtype)} for c in frame]}
        for name, layer in layers.items():
            (stage / name).write_text(geographic_json(layer), encoding="utf-8")
            schema[name] = {"features": len(layer), "crs": "EPSG:4326"}
        (stage / "esquema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# Geografía y proximidad — fase 4", "",
                 "Límites, orígenes y servicios con procedencia y controles explícitos.",
                 "Distancias euclídeas desde un punto del núcleo principal; no son recorridos ni minutos de viaje.",
                 "Los servicios de municipios mayores se conservan como destinos.", "",
                 "| Archivo | Registros |", "|---|---:|"]
        lines.extend(f"| {name} | {len(frame)} |" for name, frame in {**tables, **layers}.items())
        lines.extend(["", "## Incidencias", "", "| Fuente | Tipo | Casos |", "|---|---|---:|"])
        lines.extend(f"| {r['fuente']} | {r['tipo']} | {r['casos']} |" for r in validation["resumen_incidencias"])
        lines.extend(["", "## Límites de interpretación", ""] + validation["limitaciones"])
        (stage / "RESUMEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for item in validation["entradas"]:
            if file_sha256(item["path"]) != item["sha256"]:
                raise IntegrationError("Una entrada cambió durante la fase 4: " + item["path"])
        completed = {**validation, "generado_utc": datetime.now(timezone.utc).isoformat(), "archivos": {}}
        for path in sorted(stage.iterdir()):
            completed["archivos"][path.name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size,
                                                **schema.get(path.name, {})}
        (stage / "validacion_geografia.json").write_text(
            json.dumps(completed, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=".geografia-anterior-", dir=output.parent))
            backup.rmdir()
            output.rename(backup)
        try:
            stage.rename(output)
        except OSError:
            if backup:
                backup.rename(output)
                backup = None
            raise
        if backup:
            shutil.rmtree(backup)
        return completed
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def run_geography(root, output_dir=None, progress=None, k=3):
    from src.geo_boundaries import prepare_boundaries
    from src.geo_centers import prepare_centers_geography
    from src.geo_transport import prepare_stop_geography
    from src.geo_official_health import prepare_official_health
    from src.geo_proximity import compute_proximity

    root = Path(root).resolve()
    output = Path(output_dir) if output_dir else root / "data/processed/geografia"
    notify = progress if progress else lambda message: None
    notify("Verificando las fases anteriores y las evidencias geográficas…")
    master, master_validation, evidence, inputs, sources = verify_geographic_inputs(root)
    validate_output_location(output, inputs)
    notify("Preparando límites, enclaves, jurisdicciones y orígenes…")
    boundaries = prepare_boundaries(master, root / "data/raw/recopilacion_2026-10-15")
    municipalities, shared = boundaries["municipalities"], boundaries["shared_areas"]
    notify("Comprobando coordenadas educativas y asignando municipios a las paradas…")
    integration = root / "data/processed/integracion"
    centers = prepare_centers_geography(master, integration, municipalities, shared, evidence)
    transport = prepare_stop_geography(master, integration, municipalities, shared)
    notify("Preparando capas sanitarias oficiales independientes…")
    health = prepare_official_health(master, municipalities, shared, root / "research/sanidad_geografica_2026-10-15")
    services = gpd.GeoDataFrame(pd.concat([centers["services"], transport["services"], health["services"]],
                                         ignore_index=True), geometry="geometry", crs=25830)
    services["fuente"] = services.fuente.map(canonical_source_id)
    notify("Calculando los servicios más próximos en línea recta…")
    proximity = compute_proximity(boundaries["origins"], services, master, k=k)
    parts = {"limites": boundaries, "centros": centers, "transporte": transport,
             "sanidad_oficial": health, "proximidad": proximity}
    tables, issues = {}, []
    for result in parts.values():
        for name, frame in result["tables"].items():
            if name in tables:
                raise IntegrationError("Nombre de tabla repetido: " + name)
            tables[name] = frame
        issues.extend(result["issues"])
    issue_aliases = {"divisiones_administrativas.geojson": "idem_divisiones_administrativas",
                     "nucleos_poblacion_eiel_2023.geojson": "idem_nucleos_eiel_2023",
                     "centros_salud": "idem_centros_salud", "hospitales": "idem_hospitales"}
    for item in issues:
        item["fuente"] = canonical_source_id(issue_aliases.get(item["fuente"], item["fuente"]))
        if item["fuente"] == "sanidad_oficial_geografica":
            item["fuente"] = "idem_centros_salud" if str(item["registro"]).startswith("idem_salud:") else "idem_hospitales"
    catalog = services.copy()
    wgs = catalog.to_crs(4326)
    catalog["longitud"], catalog["latitud"] = wgs.geometry.x, wgs.geometry.y
    catalog["x_25830"], catalog["y_25830"] = catalog.geometry.x, catalog.geometry.y
    codes = master.set_index("codigo_ine")
    catalog["municipio"] = catalog.codigo_ine.map(codes.municipio)
    catalog["elegible_hasta_50000"] = catalog.codigo_ine.map(codes.elegible_hasta_50000)
    tables["catalogo_servicios_geograficos.csv"] = catalog.drop(columns="geometry")
    tables["origenes_municipales.csv"] = boundaries["origins"].drop(columns="geometry")
    layers = {"limites_municipales.geojson": municipalities,
              "jurisdicciones_compartidas.geojson": shared,
              "nucleos_eiel.geojson": boundaries["nuclei"],
              "origenes_municipales.geojson": boundaries["origins"],
              "servicios_geograficos.geojson": catalog,
              "paradas_geograficas.geojson": transport["points"]}
    validate_geographic_results(master, boundaries, services, tables)
    used_sources = set(services.fuente)
    if not used_sources.issubset({r["fuente"] for r in sources}):
        raise IntegrationError("Hay servicios sin procedencia registrada: " + str(sorted(used_sources - {r['fuente'] for r in sources})))
    validation = {
        "schema_version": 1, "fase": 4, "poblacion_anio": master_validation["poblacion_anio"],
        "regla": "poblacion <= 50000", "municipios": len(master),
        "municipios_elegibles": int(master.elegible_hasta_50000.sum()),
        "entradas": inputs, "fuentes": sources, "evidencia_crs": evidence,
        "modulos": {key: result["summary"] for key, result in parts.items()},
        "incidencias_total": len(issues), "resumen_incidencias": issue_summary(issues),
        "versiones": {"python": platform.python_version(), **{p: version(p) for p in ["pandas", "numpy", "geopandas", "shapely", "pyproj"]}},
        "controles": {"huellas_entrada_verificadas": True, "origenes_con_pertenencia_unica": True,
                      "servicios_con_municipio_confirmado": True, "distancias_en_metros_no_en_grados": True,
                      "municipios_mayores_como_destinos": True},
        "limitaciones": [
            "Puntos de referencia de núcleos EIEL 2023; no representan todos los domicilios ni ponderaciones poblacionales exhaustivas.",
            "Proximidad euclídea en EPSG:25830; no se calculan trayectos por carretera, minutos, isócronas ni accesibilidad por horarios.",
            "El registro sanitario de autorizaciones conserva coordenadas originales sin CRS confirmado y no aporta puntos operativos.",
            "Las capas sanitarias oficiales complementarias son fuentes independientes, sin cruce supuesto por número de autorización.",
            "Los directorios sanitarios y el GTFS tienen cobertura limitada; ausencia de candidato no acredita ausencia real de servicio.",
            "Todas las discrepancias municipales y las asignaciones ambiguas se excluyen de la proximidad; no se ajustan posiciones para encajarlas.",
        ], "tiempos_viaje_calculados": False, "ivp_calculado": False,
    }
    notify("Verificando y guardando tablas, capas y procedencia…")
    return publish_geography(tables, layers, issues, validation, output)
