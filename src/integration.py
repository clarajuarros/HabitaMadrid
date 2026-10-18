"""Orquesta la fase 3 sobre instantáneas verificadas y el maestro de fase 2."""
from datetime import datetime, timezone
from pathlib import Path
import json
from importlib.metadata import version
import platform
import shutil
import tempfile

import pandas as pd

from src.integration_common import (
    IntegrationError, ISSUE_COLUMNS, file_sha256, read_csv_strings,
)


def load_validated_master(master_dir):
    master_dir = Path(master_dir)
    path = master_dir / "maestro_municipios.csv"
    validation = json.loads((master_dir / "validacion.json").read_text(encoding="utf-8"))
    expected = validation.get("archivos", {}).get(path.name, {}).get("sha256")
    if not expected or file_sha256(path) != expected:
        raise IntegrationError("El maestro no coincide con su validación. Regenera la fase 2.")
    if validation.get("fase") != 2 or validation.get("regla") != "poblacion <= 50000":
        raise IntegrationError("Se requiere el maestro inclusivo validado de la fase 2.")
    master = read_csv_strings(path, sep=",").drop(columns="fila_fuente")
    required = {"codigo_ine", "codigo_municipio", "codigo_almudena", "municipio",
                "poblacion", "poblacion_anio", "limite_poblacion", "elegible_hasta_50000"}
    if not required.issubset(master):
        raise IntegrationError("El maestro carece de columnas obligatorias.")
    if (master.empty or not master.codigo_ine.is_unique or
            not master.codigo_ine.str.fullmatch(r"28[0-9]{3}").all() or
            not master.codigo_almudena.is_unique or
            not master.codigo_almudena.str.fullmatch(r"[0-9]{4}").all()):
        raise IntegrationError("Claves municipales inválidas o duplicadas en el maestro.")
    for column in ["poblacion", "poblacion_anio", "limite_poblacion"]:
        if not master[column].str.fullmatch(r"[0-9]+").all():
            raise IntegrationError("Valor no entero en el maestro: " + column)
        master[column] = master[column].astype("int64")
    if not master.elegible_hasta_50000.isin(["True", "False"]).all():
        raise IntegrationError("La bandera de elegibilidad debe ser booleana.")
    master["elegible_hasta_50000"] = master.elegible_hasta_50000.eq("True")
    if (not master.limite_poblacion.eq(50000).all() or
            not master.elegible_hasta_50000.eq(master.poblacion.le(50000)).all() or
            not master.poblacion_anio.eq(validation["poblacion_anio"]).all() or
            len(master) != validation["municipios_total"] or
            int(master.elegible_hasta_50000.sum()) != validation["municipios_elegibles"]):
        raise IntegrationError("La población o elegibilidad no cumple la validación de la fase 2.")
    return master, validation


def verify_inputs(root, raw_dir, housing_dir):
    """Verifica el contenido exacto antes de transformar; no descarga ni modifica."""
    root, raw_dir, housing_dir = map(Path, [root, raw_dir, housing_dir])
    catalog = root / "research/catalog"
    downloads = json.loads((catalog / "downloads.json").read_text())
    packages = {p["name"]: p for p in json.loads((catalog / "selected_metadata.json").read_text())}
    from src.clean_statistics import STATISTICAL_SOURCES
    from src.clean_centers import EDUCATION_FILE, HEALTH_FILE
    required_sources = set(STATISTICAL_SOURCES) | {Path(EDUCATION_FILE).stem, Path(HEALTH_FILE).stem}
    if not required_sources.issubset(packages):
        raise IntegrationError("Faltan metadatos de las fuentes requeridas por la integración.")
    records = []
    for item in downloads:
        name = item["name"]
        if name not in required_sources:
            continue
        package = packages[name]
        path = raw_dir / (name + ".csv")
        records.append({"fuente": name, "path": str(path.resolve()),
                        "sha256": item["sha256"], "url": item["url"],
                        "resource_id": item.get("resource_id", ""),
                        "titulo": package.get("title", name),
                        "licencia": package.get("license_title", ""),
                        "url_licencia": package.get("license_url", ""),
                        "fecha_descarga_utc": item.get("retrieved_at", "")})
    if {record["fuente"] for record in records} != required_sources:
        raise IntegrationError("Faltan descargas registradas para las fuentes requeridas.")
    geography = json.loads((catalog / "geography_transport_status.json").read_text())
    gtfs = next((item for item in geography if item.get("name") == "crtm_interurbanos_gtfs.zip"), None)
    if gtfs is None:
        raise IntegrationError("Falta el manifiesto de descarga GTFS.")
    records.append({"fuente": "crtm_interurbanos_gtfs", "path": str((raw_dir / gtfs["name"]).resolve()),
                    "sha256": gtfs["sha256"], "url": gtfs["url"], "resource_id": "885399f83408473c8d815e40c5e702b7",
                    "titulo": "CRTM autobuses interurbanos, feed parcial", "licencia": "Licencia CRTM datos estáticos",
                    "url_licencia": "https://www.crtm.es/licencia-de-uso", "fecha_descarga_utc": gtfs.get("retrieved_at", "")})
    housing = json.loads((housing_dir / "serpavi_descarga.json").read_text())
    records.append({"fuente": "MIVAU_SERPAVI_2011_2024", "path": str((housing_dir / Path(housing["path"]).name).resolve()),
                    "sha256": housing["sha256"], "url": housing["url"], "resource_id": "",
                    "titulo": housing["source"], "licencia": "Consultar condiciones del MIVAU; no heredar licencia CKAN",
                    "url_licencia": "", "fecha_descarga_utc": housing.get("retrieved_at", "")})
    if len({r["fuente"] for r in records}) != len(records):
        raise IntegrationError("Identificadores de fuente duplicados en los manifiestos.")
    for record in records:
        path = Path(record["path"])
        if file_sha256(path) != record["sha256"]:
            raise IntegrationError("La fuente no coincide con su descarga registrada: " + path.name)
        record["bytes"] = path.stat().st_size
    metadata_paths = [catalog / "downloads.json", catalog / "selected_metadata.json",
                      catalog / "geography_transport_status.json", housing_dir / "serpavi_descarga.json"]
    metadata = [{"path": str(p.resolve()), "sha256": file_sha256(p)} for p in metadata_paths]
    return records, metadata


def check_references(tables, master):
    codes = set(master.codigo_ine)
    eligibility = master.set_index("codigo_ine").elegible_hasta_50000
    for name, frame in tables.items():
        if not name.endswith(".csv") or Path(name).name != name:
            raise IntegrationError("Nombre de tabla de salida inválido: " + name)
        if not frame.columns.is_unique:
            raise IntegrationError("Columnas duplicadas en " + name)
        if "codigo_ine" not in frame:
            continue
        linked = frame.codigo_ine.notna() & frame.codigo_ine.ne("")
        unknown = set(frame.loc[linked, "codigo_ine"]) - codes
        if unknown:
            raise IntegrationError("Claves ajenas al maestro en " + name + ": " + str(sorted(unknown)))
        if "elegible_hasta_50000" in frame:
            flags = frame.elegible_hasta_50000.astype("boolean")
            if flags.loc[~linked].notna().any():
                raise IntegrationError("Elegibilidad inventada para un registro sin municipio en " + name)
            expected = frame.loc[linked, "codigo_ine"].map(eligibility).astype("boolean")
            if flags.loc[linked].isna().any() or not flags.loc[linked].eq(expected).all():
                raise IntegrationError("Elegibilidad discrepante en " + name)


def issue_summary(issues):
    if not issues:
        return []
    frame = pd.DataFrame(issues, columns=ISSUE_COLUMNS)
    return frame.groupby(["fuente", "tipo", "severidad"], dropna=False).size().reset_index(name="casos").to_dict("records")


def canonical_source_id(value):
    aliases = {"centros_educativos.csv": "centros_educativos",
               "centros_servicios_establecimientos_sanitarios.csv": "centros_servicios_establecimientos_sanitarios",
               "GTFS_CRTM_bus_interurbanos": "crtm_interurbanos_gtfs",
               "GTFS_CRTM": "crtm_interurbanos_gtfs", "SERPAVI": "MIVAU_SERPAVI_2011_2024"}
    return aliases.get(value, value)


def validate_output_location(output_dir, inputs):
    output = Path(output_dir).resolve()
    if Path(output_dir).is_symlink():
        raise IntegrationError("La carpeta de derivados no puede ser un enlace simbólico.")
    for item in inputs:
        path = Path(item["path"]).resolve()
        parent = path.parent
        if output == parent or parent in output.parents or output in path.parents or output == path:
            raise IntegrationError("La salida no puede sustituir ni quedar dentro de las carpetas de entrada.")


def publish_outputs(tables, issues, validation, output_dir):
    """Prepara una carpeta completa antes de sustituir los derivados regenerables."""
    reserved = {"incidencias.csv", "fuentes.csv"}
    for name, frame in tables.items():
        if not name.endswith(".csv") or Path(name).name != name or name in reserved:
            raise IntegrationError("Nombre de tabla de salida inválido o reservado: " + name)
        if not frame.columns.is_unique:
            raise IntegrationError("Columnas duplicadas en " + name)
    output_dir = Path(output_dir)
    inputs = validation["fuentes"] + validation["metadatos_entrada"] + [validation["maestro_entrada"]]
    validate_output_location(output_dir, inputs)
    if output_dir.exists() and not output_dir.is_dir():
        raise IntegrationError("La ruta de salida ya existe y no es una carpeta.")
    if output_dir.exists() and any(output_dir.iterdir()):
        previous_path = output_dir / "validacion_integracion.json"
        if not previous_path.exists():
            raise IntegrationError("La carpeta de salida contiene archivos ajenos a esta fase; elige otra.")
        previous = json.loads(previous_path.read_text())
        allowed = set(previous.get("archivos", {})) | {"validacion_integracion.json"}
        if previous.get("fase") != 3 or set(p.name for p in output_dir.iterdir()) - allowed:
            raise IntegrationError("La salida contiene archivos ajenos al conjunto regenerable de fase 3.")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".integracion-", dir=output_dir.parent))
    backup = None
    try:
        output_tables = {**tables, "incidencias.csv": pd.DataFrame(issues, columns=ISSUE_COLUMNS)}
        output_tables["fuentes.csv"] = pd.DataFrame(validation["fuentes"])
        schema = {}
        for name, frame in output_tables.items():
            frame.to_csv(stage / name, index=False, encoding="utf-8-sig")
            schema[name] = {"filas": len(frame), "columnas": [{"nombre": str(c), "tipo_en_memoria": str(frame[c].dtype)} for c in frame]}
        (stage / "esquema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# Integración de HabitaMadrid — fase 3", "",
                 f"Padrón {validation['poblacion_anio']}: {validation['municipios']} municipios, "
                 f"{validation['municipios_elegibles']} elegibles con población <= 50.000.", "",
                 "Los CSV conservan datos ausentes como celdas vacías. Códigos e identificadores se leen como texto.",
                 "Las incidencias y la cobertura deben consultarse antes de interpretar una ausencia como cero.",
                 "Las coordenadas de centros requieren confirmar CRS; las paradas GTFS aún no tienen municipio asignado.",
                 "No se han calculado tiempos de viaje, indicadores IVP ni puntuaciones.", "", "| Tabla | Filas |", "|---|---:|"]
        lines.extend(f"| {name} | {len(frame)} |" for name, frame in output_tables.items())
        lines.extend(["", "## Incidencias por fuente y tipo", "", "| Fuente | Tipo | Casos |", "|---|---|---:|"])
        lines.extend(f"| {r['fuente']} | {r['tipo']} | {r['casos']} |" for r in validation["resumen_incidencias"])
        (stage / "RESUMEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for item in inputs:
            if file_sha256(item["path"]) != item["sha256"]:
                raise IntegrationError("Una entrada ha cambiado durante el procesamiento: " + item["path"])
        completed = {**validation, "generado_utc": datetime.now(timezone.utc).isoformat(), "archivos": {}}
        for path in sorted(stage.iterdir()):
            completed["archivos"][path.name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size,
                                                **({"filas": len(output_tables[path.name])} if path.name in output_tables else {})}
        (stage / "validacion_integracion.json").write_text(
            json.dumps(completed, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if output_dir.exists():
            backup = Path(tempfile.mkdtemp(prefix=".integracion-anterior-", dir=output_dir.parent))
            backup.rmdir()
            output_dir.rename(backup)
        try:
            stage.rename(output_dir)
        except OSError:
            if backup is not None:
                backup.rename(output_dir)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
        return completed
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def run_integration(root, output_dir=None, progress=None):
    from src.clean_centers import prepare_centers
    from src.clean_statistics import prepare_statistics
    from src.clean_housing_transport import prepare_housing_transport

    root = Path(root).resolve()
    output_dir = Path(output_dir) if output_dir else root / "data/processed/integracion"
    raw = root / "data/raw/recopilacion_2026-10-15"
    housing = root / "data/raw/vivienda_2026-10-15"
    notify = progress if progress is not None else lambda message: None
    master_path = root / "data/processed/municipios/maestro_municipios.csv"
    master_validation_path = master_path.parent / "validacion.json"
    master_record = {"path": str(master_path), "sha256": file_sha256(master_path)}
    notify("Verificando maestro y huellas de las fuentes locales…")
    master, master_validation = load_validated_master(master_path.parent)
    sources, metadata = verify_inputs(root, raw, housing)
    metadata.append({"path": str(master_validation_path), "sha256": file_sha256(master_validation_path)})
    validate_output_location(output_dir, sources + metadata + [master_record])
    tables, issues, summaries = {"municipios.csv": master}, [], {}
    tasks = [
        ("centros", "Limpiando centros y recuperando direcciones…", lambda: prepare_centers(master, raw)),
        ("estadisticas", "Normalizando históricos y cobertura por fuente y año…", lambda: prepare_statistics(master, raw)),
        ("vivienda_transporte", "Extrayendo SERPAVI y relacionando rutas y paradas GTFS…", lambda: prepare_housing_transport(master, raw, housing)),
    ]
    for key, message, prepare in tasks:
        notify(message)
        group, found_issues, summary = prepare()
        for name, frame in group.items():
            name = name if name.endswith(".csv") else name + ".csv"
            if name in tables:
                raise IntegrationError("Nombre de tabla duplicado: " + name)
            tables[name] = frame
        issues.extend(found_issues)
        summaries[key] = summary
    source_ids = {source["fuente"] for source in sources}
    for name, frame in tables.items():
        if "fuente" in frame:
            frame["fuente"] = frame["fuente"].map(canonical_source_id)
            if not set(frame["fuente"]).issubset(source_ids):
                raise IntegrationError("Fuente sin procedencia registrada en " + name)
    for issue in issues:
        issue["fuente"] = canonical_source_id(issue["fuente"])
        if issue["fuente"] not in source_ids:
            raise IntegrationError("Incidencia sin procedencia registrada: " + issue["fuente"])
    notify("Comprobando referencias y preparando derivados y registro de incidencias…")
    check_references(tables, master)
    validation = {
        "schema_version": 1, "fase": 3, "poblacion_anio": int(master_validation["poblacion_anio"]),
        "regla": "poblacion <= 50000", "municipios": len(master),
        "municipios_elegibles": int(master.elegible_hasta_50000.sum()),
        "maestro_entrada": master_record, "fuentes": sources, "metadatos_entrada": metadata,
        "versiones": {"python": platform.python_version(), "pandas": pd.__version__,
                      "numpy": version("numpy"), "openpyxl": version("openpyxl")},
        "modulos": summaries, "incidencias_total": len(issues), "resumen_incidencias": issue_summary(issues),
        "controles": {"huellas_entrada_verificadas": True, "maestro_inclusivo_validado": True,
                      "referencias_municipales_verificadas": True, "servicios_de_municipios_mayores_conservados": True},
        "fuentes_no_integradas": {
            "1903452": "Estadística nacional sin desglose municipal; no apta para el análisis municipal.",
            "atencion_social_registro_centros": "Directorio auxiliar fuera de las cuatro dimensiones iniciales; original conservado.",
            "cartografia": "Conservada para la fase 4, sin asignaciones espaciales en esta etapa."},
        "pendiente_fase_4": ["Confirmar CRS de centros antes de transformar coordenadas.",
                             "Completar denominaciones sanitarias mediante una fuente oficial y un cruce seguro.",
                             "Preparar límites con enclaves y jurisdicciones compartidas.",
                             "Asignar municipio a las paradas mediante geografía verificada.",
                             "Definir orígenes y método de proximidad y red de transporte."],
        "ivp_calculado": False,
    }
    return publish_outputs(tables, issues, validation, output_dir)
