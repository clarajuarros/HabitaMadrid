"""Fase 5: matriz de medidas, trazabilidad y aptitud previa a un futuro IVP."""
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import json
import math
import platform
import shutil
import tempfile

import pandas as pd

from src.integration import load_validated_master, issue_summary, validate_output_location
from src.integration_common import IntegrationError, ISSUE_COLUMNS, file_sha256, make_issue

OBS_REQUIRED = {"codigo_ine", "indicador_id", "valor", "anio", "periodo", "estado",
                "fuentes_json", "detalle_json", "nota"}
DEF_REQUIRED = {"indicador_id", "dimension", "nombre", "unidad", "sentido", "rol", "formula",
                "fuentes_json", "limitaciones", "grupo_redundancia"}
METHODOLOGY = [
    {"titulo": "Indicador de Renta Disponible Bruta Municipal, Comunidad de Madrid",
     "url": "https://web.comunidad.madrid/iestadis/fijas/estructu/economicas/datosimpositivos/irm15.htm"},
    {"titulo": "Metodología SERPAVI, MIVAU",
     "url": "https://cdn.mivau.gob.es/portal-web-mivau/vivienda/serpavi/2026-03-18_Metodologia_SERPAVI.pdf"},
    {"titulo": "Estadística de Migraciones y Cambios de Residencia, INE",
     "url": "https://www.ine.es/dynt3/metadatos/RespuestaPrint.htm?oper=455"},
    {"titulo": "Handbook on Constructing Composite Indicators, OECD/JRC",
     "url": "https://www.oecd.org/en/publications/handbook-on-constructing-composite-indicators-methodology-and-user-guide_9789264043466-en.html"},
]


def _record(path, expected=None):
    path = Path(path).resolve()
    digest = file_sha256(path)
    if expected and digest != expected:
        raise IntegrationError("La entrada cambió respecto a su validación: " + str(path))
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size}


def load_indicator_inputs(root):
    root = Path(root)
    master, validation = load_validated_master(root / "data/processed/municipios")
    inputs = [_record(root / "data/processed/municipios/maestro_municipios.csv"),
              _record(root / "data/processed/municipios/validacion.json")]
    manifests = {}
    for folder, filename, phase in [("integracion", "validacion_integracion.json", 3),
                                     ("geografia", "validacion_geografia.json", 4)]:
        path = root / "data/processed" / folder / filename
        inputs.append(_record(path))
        manifest = json.loads(path.read_text())
        if manifest.get("fase") != phase or manifest.get("regla") != "poblacion <= 50000":
            raise IntegrationError("Falta una fase previa validada e inclusiva: " + str(phase))
        if manifest["poblacion_anio"] != validation["poblacion_anio"]:
            raise IntegrationError("Las fases previas utilizan padrones diferentes.")
        for name, details in manifest["archivos"].items():
            if Path(name).name != name:
                raise IntegrationError("Nombre de derivado inválido en un manifiesto previo.")
            inputs.append(_record(path.parent / name, details["sha256"]))
        manifests[phase] = manifest
    if manifests[3]["maestro_entrada"]["sha256"] != file_sha256(root / "data/processed/municipios/maestro_municipios.csv"):
        raise IntegrationError("El maestro cambió después de la integración; regenera las fases anteriores.")
    for item in manifests[4]["entradas"]:
        inputs.append(_record(item["path"], item["sha256"]))
    # Se conserva la procedencia original sin volver a descargar las fuentes.
    sources = {item["fuente"]: {"fuente": item["fuente"], "archivo": item["path"], "url": item["url"],
                "sha256": item["sha256"], "fecha_descarga_utc": item["fecha_descarga_utc"],
                "licencia": item.get("licencia", ""), "nota": "Procedencia de fase 3"}
               for item in manifests[3]["fuentes"]}
    for item in manifests[4]["fuentes"]:
        if item["fuente"] not in sources:
            sources[item["fuente"]] = {**item, "licencia": "Consultar atribución y condiciones de la fuente geográfica; no heredar licencia CKAN."}
    return master, validation, list({item["path"]: item for item in inputs}.values()), sources


def validate_indicator_contract(master, observations, definitions):
    if not OBS_REQUIRED.issubset(observations) or not DEF_REQUIRED.issubset(definitions):
        raise IntegrationError("El módulo no cumple el contrato de observaciones o definiciones.")
    if definitions.empty or observations.empty or definitions.indicador_id.duplicated().any():
        raise IntegrationError("Se necesitan indicadores únicos y observaciones no vacías.")
    if observations.duplicated(["codigo_ine", "indicador_id"]).any():
        raise IntegrationError("Hay observaciones duplicadas por municipio e indicador.")
    if set(observations.indicador_id) != set(definitions.indicador_id):
        raise IntegrationError("Definiciones y observaciones no describen los mismos indicadores.")
    codes = set(master.loc[master.elegible_hasta_50000, "codigo_ine"])
    for identifier, group in observations.groupby("indicador_id"):
        if set(group.codigo_ine) != codes:
            raise IntegrationError("La malla municipal debe conservar ausencias explícitas: " + identifier)
    numeric = pd.to_numeric(observations.valor, errors="raise").astype(float)
    if numeric.map(lambda value: pd.notna(value) and not math.isfinite(value)).any():
        raise IntegrationError("Los valores de los indicadores deben ser finitos o ausentes.")
    if not numeric.notna().eq(observations.estado.eq("disponible")).all():
        raise IntegrationError("La disponibilidad y el valor numérico son incoherentes.")
    if not definitions.rol.isin(["candidato_ivp", "contexto"]).all():
        raise IntegrationError("Rol desconocido en el catálogo de indicadores.")
    if not definitions.sentido.isin(["menor_mejor", "mayor_mejor", "contexto"]).all():
        raise IntegrationError("Sentido desconocido en el catálogo de indicadores.")
    for text in [*definitions.fuentes_json, *observations.fuentes_json]:
        sources = json.loads(text)
        if not isinstance(sources, list) or not sources or any(not isinstance(item, str) or not item for item in sources):
            raise IntegrationError("Cada indicador requiere una lista de fuentes identificadas.")
    for text in observations.detalle_json:
        if not isinstance(json.loads(text), dict):
            raise IntegrationError("La trazabilidad del indicador debe ser un objeto JSON.")


def add_review_status(observations, definitions, contrast):
    """Una medida calculable no equivale a una medida apta para puntuar."""
    obs, defs = observations.copy(), definitions.copy()
    lookup = defs.set_index("indicador_id")
    obs["uso_ivp_propuesto"] = "contexto"
    candidate = obs.indicador_id.map(lookup.rol).eq("candidato_ivp")
    obs.loc[candidate, "uso_ivp_propuesto"] = "candidatura_condicionada"
    obs.loc[candidate & ~obs.estado.eq("disponible"), "uso_ivp_propuesto"] = "sin_dato"
    health = obs.indicador_id.eq("distancia_atencion_primaria_m")
    obs.loc[health, "uso_ivp_propuesto"] = "requiere_revision_cobertura"
    obs["advertencia_comparabilidad"] = ""
    obs.loc[health, "advertencia_comparabilidad"] = (
        "Revisión global del catálogo sanitario: la brecha de puntos locales puede alterar el destino "
        "más cercano de cualquier municipio. La distancia al subcatálogo sigue siendo calculable.")
    local = contrast.set_index("codigo_ine").revision_ivp_requerida
    obs["alerta_contraste_local_primaria"] = health & obs.codigo_ine.map(local).fillna(False)
    defs["decision_ivp_fase5"] = defs.rol.map({"contexto": "contexto", "candidato_ivp": "candidatura_condicionada"})
    defs.loc[defs.indicador_id.eq("distancia_atencion_primaria_m"), "decision_ivp_fase5"] = "requiere_revision_cobertura"
    if "condicion_uso_ivp" not in defs:
        defs["condicion_uso_ivp"] = ""
    defs["condicion_uso_ivp"] = defs.condicion_uso_ivp.fillna("Contexto; no seleccionado para el IVP.")
    defs["periodos_datos_json"] = defs.indicador_id.map(
        obs.groupby("indicador_id").periodo.agg(lambda x: json.dumps(sorted(set(x)), ensure_ascii=False)))
    return obs, defs


def dimension_coverage(observations, definitions):
    candidate_defs = definitions.loc[definitions.rol.eq("candidato_ivp")]
    rows = []
    for dimension, group in candidate_defs.groupby("dimension"):
        selected = observations.loc[observations.indicador_id.isin(group.indicador_id)]
        for code, values in selected.groupby("codigo_ine"):
            available = values.estado.eq("disponible")
            review = values.uso_ivp_propuesto.eq("requiere_revision_cobertura")
            rows.append({"codigo_ine": code, "dimension": dimension, "candidatos_definidos": len(group),
                         "candidatos_con_valor": int(available.sum()),
                         "candidatos_con_revision_global": int(review.sum()),
                         "estado_calculo": "alguna_medida_calculable" if available.any() else "sin_medida_calculable",
                         "estado_para_ivp": "requiere_revision_cobertura" if review.any() else
                                            "candidatura_condicionada" if available.any() else "sin_dato",
                         "nota": "Computabilidad, no exhaustividad ni puntuación. VC y VU son alternativas; no se exige ni se promedia su presencia conjunta."})
    return pd.DataFrame(rows)


def publish_indicators(tables, validation, output_dir):
    output = Path(output_dir)
    validate_output_location(output, validation["entradas"])
    for name, frame in tables.items():
        if Path(name).name != name or not name.endswith(".csv") or not frame.columns.is_unique:
            raise IntegrationError("Nombre o columnas de salida inválidos: " + name)
    if output.exists() and not output.is_dir():
        raise IntegrationError("La salida de indicadores no es una carpeta.")
    if output.exists() and any(output.iterdir()):
        previous_path = output / "validacion_indicadores.json"
        if not previous_path.exists():
            raise IntegrationError("La salida contiene archivos ajenos a la fase 5.")
        previous = json.loads(previous_path.read_text())
        allowed = set(previous.get("archivos", {})) | {"validacion_indicadores.json"}
        if previous.get("fase") != 5 or set(p.name for p in output.iterdir()) - allowed:
            raise IntegrationError("La salida contiene archivos ajenos al conjunto regenerable de indicadores.")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".indicadores-", dir=output.parent))
    backup = None
    try:
        schema = {}
        for name, frame in tables.items():
            frame.to_csv(stage / name, index=False, encoding="utf-8-sig")
            schema[name] = {"filas": len(frame), "columnas": [{"nombre": str(c), "tipo_en_memoria": str(frame[c].dtype)} for c in frame]}
        (stage / "esquema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# Indicadores de HabitaMadrid — fase 5", "",
                 f"{validation['indicadores']} indicadores, {validation['observaciones']} observaciones y {validation['municipios_elegibles']} municipios.",
                 f"{validation['candidatos_ivp']} candidaturas condicionadas; no se han normalizado valores, asignado pesos, calculado IVP ni creado rankings.",
                 "Cobertura de cálculo significa que una medida se puede calcular; no demuestra exhaustividad del catálogo ni aptitud para el IVP.",
                 "La distancia a atención primaria requiere revisión global de cobertura antes de puntuar cualquiera de los municipios.",
                 "Las dos tipologías de alquiler son alternativas; sus medianas no se promedian ni rellenan unas con otras.", "",
                 "| Indicador | Rol | Con valor | Universo |", "|---|---|---:|---:|"]
        lines.extend(f"| {r.indicador_id} | {r.rol} | {r.con_valor} | {r.municipios_universo} |"
                     for r in tables["cobertura_indicadores.csv"].itertuples())
        lines.extend(["", "## Archivos", "", "| Tabla | Filas |", "|---|---:|"])
        lines.extend(f"| {name} | {len(frame)} |" for name, frame in tables.items())
        lines.extend(["", "## Decisiones pendientes antes del IVP", ""] + validation["pendiente_fase6"])
        (stage / "RESUMEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for item in validation["entradas"]:
            if file_sha256(item["path"]) != item["sha256"]:
                raise IntegrationError("Una entrada cambió durante la fase 5: " + item["path"])
        completed = {**validation, "generado_utc": datetime.now(timezone.utc).isoformat(), "archivos": {}}
        for path in sorted(stage.iterdir()):
            completed["archivos"][path.name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size,
                                                **schema.get(path.name, {})}
        (stage / "validacion_indicadores.json").write_text(
            json.dumps(completed, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=".indicadores-anteriores-", dir=output.parent))
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


def run_indicators(root, output_dir=None, progress=None):
    from src.indicator_services import prepare_service_indicators
    from src.indicator_housing_demography import prepare_housing_demography
    from src.indicator_statistics import prepare_statistical_indicators, cross_health_coverage
    from src.indicator_diagnostics import indicator_diagnostics

    root = Path(root).resolve()
    output = Path(output_dir) if output_dir else root / "data/processed/indicadores"
    notify = progress if progress else lambda message: None
    notify("Verificando las fases anteriores y sus fuentes…")
    master, master_validation, inputs, source_pool = load_indicator_inputs(root)
    validate_output_location(output, inputs)
    integration, geography = root / "data/processed/integracion", root / "data/processed/geografia"
    modules = [
        ("servicios", "Calculando proximidad y registros de servicios…", lambda: prepare_service_indicators(master, integration, geography)),
        ("vivienda_demografia", "Preparando vivienda y contexto demográfico…", lambda: prepare_housing_demography(master, integration)),
        ("estadisticas", "Seleccionando estadísticas por año, dimensiones y unidad…", lambda: prepare_statistical_indicators(master, integration)),
    ]
    observations, definitions, tables, issues, summaries = [], [], {}, [], {}
    for key, message, prepare in modules:
        notify(message)
        result = prepare()
        observations.extend(result["observations"])
        definitions.extend(result["definitions"])
        for name, frame in result["tables"].items():
            if name in tables:
                raise IntegrationError("Nombre de tabla de indicadores repetido: " + name)
            tables[name] = frame
        issues.extend(result["issues"])
        summaries[key] = result["summary"]
    observations, definitions = pd.DataFrame(observations), pd.DataFrame(definitions)
    validate_indicator_contract(master, observations, definitions)
    observations["valor"] = pd.to_numeric(observations.valor).astype(float)
    observations["anio"] = observations.anio.astype("Int64")
    notify("Contrastando cobertura sanitaria y diagnosticando las medidas…")
    contrast, contrast_issues = cross_health_coverage(observations, geography)
    issues.extend(contrast_issues)
    observations, definitions = add_review_status(observations, definitions, contrast)
    observations = observations.sort_values(["codigo_ine", "indicador_id"]).reset_index(drop=True)
    definitions = definitions.sort_values(["dimension", "indicador_id"]).reset_index(drop=True)
    diagnostics = indicator_diagnostics(observations, definitions)
    tables.update(diagnostics["tables"])
    summaries["diagnosticos"] = diagnostics["summary"]
    used_sources = set(item for text in [*observations.fuentes_json, *definitions.fuentes_json] for item in json.loads(text))
    if not used_sources.issubset(source_pool):
        raise IntegrationError("Indicadores con fuentes sin procedencia registrada: " + str(sorted(used_sources - source_pool.keys())))
    sources = [source_pool[key] for key in sorted(used_sources)]
    eligible = master.loc[master.elegible_hasta_50000].copy()
    wide = observations.pivot(index="codigo_ine", columns="indicador_id", values="valor").reset_index()
    wide.columns.name = None
    matrix = eligible.merge(wide, on="codigo_ine", how="left", validate="one_to_one").sort_values("codigo_ine")
    tables.update({"indicadores_municipales.csv": observations, "municipios_indicadores.csv": matrix,
                   "catalogo_indicadores.csv": definitions, "cobertura_dimensiones.csv": dimension_coverage(observations, definitions),
                   "alertas_cobertura_cruzada.csv": contrast,
                   "incidencias_indicadores.csv": pd.DataFrame(issues, columns=ISSUE_COLUMNS),
                   "fuentes_indicadores.csv": pd.DataFrame(sources)})
    validation = {
        "schema_version": 1, "fase": 5, "poblacion_anio": int(master_validation["poblacion_anio"]),
        "regla": "poblacion <= 50000", "municipios_elegibles": len(eligible),
        "indicadores": len(definitions), "observaciones": len(observations),
        "candidatos_ivp": int(definitions.rol.eq("candidato_ivp").sum()),
        "indicadores_contexto": int(definitions.rol.eq("contexto").sum()),
        "entradas": inputs, "fuentes": sources, "metodologia_referencias": METHODOLOGY,
        "modulos": summaries, "resumen_incidencias": issue_summary(issues),
        "contraste_sanitario": {"municipios_con_indicio_local": int(contrast.revision_ivp_requerida.sum()),
                                "sin_punto_local_con_estadistica_positiva": int(contrast.sin_punto_local_con_estadistica_positiva.sum()),
                                "alcance_revision_para_ivp": "Global, 155 observaciones de distancia a atención primaria; no solo municipios con brecha local."},
        "versiones": {"python": platform.python_version(), **{p: version(p) for p in ["pandas", "numpy"]}},
        "controles": {"malla_municipal_completa": True, "ausencias_distintas_de_cero": True,
                      "fuentes_y_unidades_documentadas": True, "sin_sustitucion_entre_tipologias": True,
                      "sin_relleno_con_otros_anios": True, "imputaciones": 0},
        "pendiente_fase6": [
            "Resolver la revisión global de cobertura de atención primaria o excluir justificadamente ese proxy; no puntuarlo automáticamente.",
            "Seleccionar indicadores y una tipología de alquiler común en cada comparación, con política explícita para ausencias.",
            "Elegir normalización, ponderaciones y análisis de sensibilidad, revisando redundancias semánticas y estadísticas.",
            "Separar IVP general y personalizado, con cobertura y limitaciones visibles."],
        "normalizacion_calculada": False, "pesos_asignados": False, "ivp_calculado": False,
        "ranking_calculado": False, "tiempos_viaje_calculados": False,
    }
    notify("Guardando matriz municipal, catálogo, cobertura y trazabilidad…")
    return publish_indicators(tables, validation, output)
