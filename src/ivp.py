"""Fase 6: edición reproducible del IVP con perfiles y decisiones explícitas."""
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import json
import platform
import shutil
import tempfile

import numpy as np
import pandas as pd

from src.indicators import load_indicator_inputs, validate_indicator_contract
from src.integration import load_validated_master, validate_output_location
from src.integration_common import IntegrationError, file_sha256
from src.ivp_calculation import (DIMENSIONS, calculate_index, fit_normalization,
                                 normalize_weights, transform_normalization)
from src.ivp_sensitivity import analyze_sensitivity

METHODS = ("minmax", "winsor_p05_p95", "percentil")
GENERAL_WEIGHTS = {dimension: 0.25 for dimension in DIMENSIONS}
PROFILES = {
    "VC": {"nombre": "Vivienda colectiva", "indicador": "vivienda_alquiler_vc_m2_mediana"},
    "VU": {"nombre": "Vivienda unifamiliar o rural", "indicador": "vivienda_alquiler_vu_m2_mediana"},
}
COMMON_INDICATORS = {
    "educacion": "distancia_centro_educativo_general_m",
    "transporte": "distancia_parada_bus_parcial_m",
}
HEALTH_ORIGINAL = "distancia_atencion_primaria_m"
HEALTH_REVIEWED = "distancia_atencion_primaria_directorio_m"
METHODOLOGY_VERSION = "ivp-1.0.0"


def _record(path, expected=None):
    path = Path(path).resolve()
    digest = file_sha256(path)
    if expected is not None and digest != expected:
        raise IntegrationError("Entrada modificada desde su validación: " + str(path))
    return {"path": str(path), "sha256": digest, "bytes": path.stat().st_size}


def load_ivp_inputs(root):
    """Valida la cadena de fases y conserva las ausencias de la malla de fase 5."""
    root = Path(root).resolve()
    master, master_validation, inputs, _ = load_indicator_inputs(root)
    folder = root / "data/processed/indicadores"
    manifest_path = folder / "validacion_indicadores.json"
    inputs.append(_record(manifest_path))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (manifest.get("fase") != 5 or manifest.get("regla") != "poblacion <= 50000"
            or manifest.get("poblacion_anio") != master_validation["poblacion_anio"]):
        raise IntegrationError("Se necesita la fase 5 validada con el mismo padrón inclusivo.")
    for name, detail in manifest["archivos"].items():
        if Path(name).name != name:
            raise IntegrationError("Archivo inválido en el manifiesto de indicadores.")
        inputs.append(_record(folder / name, detail["sha256"]))
    for item in manifest["entradas"]:
        inputs.append(_record(item["path"], item["sha256"]))
    observations = pd.read_csv(folder / "indicadores_municipales.csv", keep_default_na=False,
                               dtype={"codigo_ine": str})
    definitions = pd.read_csv(folder / "catalogo_indicadores.csv", keep_default_na=False)
    observations["valor"] = pd.to_numeric(observations.valor.replace("", np.nan), errors="raise")
    validate_indicator_contract(master, observations, definitions)
    eligible = master.loc[master.elegible_hasta_50000].sort_values("codigo_ine").copy()
    if len(eligible) != manifest["municipios_elegibles"]:
        raise IntegrationError("La fase 5 no utiliza el universo elegible vigente.")
    inputs = list({item["path"]: item for item in inputs}.values())
    return eligible, observations, definitions, inputs, manifest


def select_ivp_indicators(eligible, observations, definitions, health):
    """Una medida por dimensión y dos alternativas de vivienda, nunca sumadas."""
    codes = eligible.codigo_ine.tolist()
    ids = [HEALTH_ORIGINAL, *COMMON_INDICATORS.values(),
           *(profile["indicador"] for profile in PROFILES.values())]
    selected = observations.loc[observations.indicador_id.isin(ids)].copy()
    chosen_definitions = definitions.set_index("indicador_id").loc[ids]
    if not chosen_definitions.rol.eq("candidato_ivp").all():
        raise IntegrationError("La selección contiene variables de contexto no autorizadas.")
    if not chosen_definitions.sentido.eq("menor_mejor").all():
        raise IntegrationError("Revisar la dirección: esta edición selecciona costes y proximidad.")
    health_id = HEALTH_ORIGINAL
    blocked = ("sanidad",)
    if health["usable"]:
        new = health["observations"].copy()
        required = {"codigo_ine", "valor", "estado", "periodo", "fuentes_json", "detalle_json", "nota"}
        if (not required.issubset(new) or new.codigo_ine.duplicated().any()
                or set(new.codigo_ine) != set(codes) or not new.estado.eq("disponible").all()):
            raise IntegrationError("La revisión sanitaria no conserva todos los orígenes con datos verificables.")
        new["valor"] = pd.to_numeric(new.valor, errors="raise")
        if not np.isfinite(new.valor.to_numpy(dtype=float)).all() or new.valor.lt(0).any():
            raise IntegrationError("Distancias sanitarias nuevas inválidas.")
        health_id = HEALTH_REVIEWED
        new["indicador_id"] = health_id
        new["anio"] = ""
        new["uso_ivp_propuesto"] = "seleccionado_directorio_revisado"
        new["advertencia_comparabilidad"] = "Proximidad geométrica al directorio; no acredita asignación, horarios, capacidad o tiempo de viaje."
        new["alerta_contraste_local_primaria"] = False
        selected = pd.concat([selected.loc[~selected.indicador_id.eq(HEALTH_ORIGINAL)], new], ignore_index=True)
        blocked = ()
    selected = selected.sort_values(["codigo_ine", "indicador_id"]).reset_index(drop=True)
    if len(selected) != len(codes) * 5 or selected.duplicated(["codigo_ine", "indicador_id"]).any():
        raise IntegrationError("La selección debe mantener cinco medidas por municipio, incluidas ausencias.")
    lookup = definitions.set_index("indicador_id")
    policies = []
    mappings = {"sanidad": health_id, **COMMON_INDICATORS}
    for dimension, identifier in [*mappings.items(), *(("vivienda", p["indicador"]) for p in PROFILES.values())]:
        original = HEALTH_ORIGINAL if identifier == HEALTH_REVIEWED else identifier
        item = lookup.loc[original]
        policies.append({
            "dimension": dimension, "indicador_id": identifier,
            "perfil_vivienda": "VC" if identifier == PROFILES["VC"]["indicador"] else
                               "VU" if identifier == PROFILES["VU"]["indicador"] else "ambos",
            "nombre": "Distancia a atención primaria del directorio oficial revisado" if identifier == HEALTH_REVIEWED else item["nombre"],
            "unidad": item["unidad"], "sentido": "menor_mejor",
            "decision": "bloqueado_revision_cobertura" if dimension in blocked else "seleccionado_con_alcance_documentado",
            "periodos_json": json.dumps(sorted(selected.loc[selected.indicador_id.eq(identifier), "periodo"].unique()), ensure_ascii=False),
            "motivo": "Revisión sanitaria independiente; se sustituye el catálogo anterior sin alterar la fase 5." if identifier == HEALTH_REVIEWED else
                      "Una sola medida de proximidad por dimensión; vivienda mantiene alternativas separadas.",
            "limitaciones": "Directorio institucional observado, sin medir calidad asistencial, plazas, horarios o acceso efectivo." if identifier == HEALTH_REVIEWED else item["limitaciones"],
        })
    return selected, pd.DataFrame(policies), mappings, blocked


def normalize_selected(selected, policies, blocked_dimensions=()):
    """Ajusta una vez cada referencia con todos los elegibles observados del indicador."""
    policy_lookup = policies.set_index("indicador_id")
    references, rows, reference_rows = {}, [], []
    for identifier, group in selected.groupby("indicador_id", sort=True):
        values = group.set_index("codigo_ine").valor.astype(float).sort_index()
        policy = policy_lookup.loc[identifier]
        blocked = policy.dimension in blocked_dimensions
        for method in METHODS:
            # Un proxy bloqueado no recibe siquiera una puntuación diagnóstica confundible con la apta.
            usable = pd.Series(np.nan, index=values.index, dtype=float) if blocked else values
            reference = fit_normalization(usable, direction="menor_mejor", method=method)
            reference.update({"indicador_id": identifier, "metodologia_version": METHODOLOGY_VERSION,
                              "universo_elegible": len(values), "anclas_fijas": True,
                              "dimension": policy.dimension, "bloqueado": bool(blocked)})
            references.setdefault(method, {})[identifier] = reference
            scores = transform_normalization(usable, reference)
            reference_rows.append({"metodo": method, "indicador_id": identifier,
                                   "dimension": policy.dimension, "bloqueado": blocked,
                                   "referencia_json": json.dumps(reference, ensure_ascii=False, allow_nan=False)})
            original = group.set_index("codigo_ine")
            for code, score in scores.items():
                rows.append({"codigo_ine": code, "indicador_id": identifier,
                             "dimension": policy.dimension, "metodo": method,
                             "valor_original": float(values.loc[code]) if pd.notna(values.loc[code]) else np.nan,
                             "puntuacion": float(score) if pd.notna(score) else np.nan,
                             "estado_puntuacion": "bloqueado_revision_cobertura" if blocked else
                                                   "disponible" if pd.notna(score) else "sin_dato",
                             "periodo": original.loc[code, "periodo"],
                             "fuentes_json": original.loc[code, "fuentes_json"]})
    return references, pd.DataFrame(rows), pd.DataFrame(reference_rows)


def build_dimension_matrices(eligible, normalized, mappings):
    result, rows = {}, []
    codes = pd.Index(eligible.codigo_ine, name="codigo_ine")
    for profile, config in PROFILES.items():
        result[profile] = {}
        selected = {**mappings, "vivienda": config["indicador"]}
        for method in METHODS:
            subset = normalized.loc[normalized.metodo.eq(method)]
            matrix = pd.DataFrame(index=codes, columns=list(DIMENSIONS), dtype=float)
            for dimension, identifier in selected.items():
                series = subset.loc[subset.indicador_id.eq(identifier)].set_index("codigo_ine").puntuacion
                matrix[dimension] = series.reindex(codes)
            result[profile][method] = matrix
            table = matrix.reset_index()
            table.insert(1, "perfil_vivienda", profile)
            table.insert(2, "metodo", method)
            rows.append(table)
    return result, pd.concat(rows, ignore_index=True)


def profile_results(eligible, scores, profile, weights, mode, blocked_dimensions=()):
    """El índice requiere todas las dimensiones con peso positivo; no redistribuye ausencias."""
    normalized_weights = normalize_weights(weights)
    calculated = calculate_index(scores, normalized_weights, blocked_dimensions=blocked_dimensions)
    active = [dimension for dimension in DIMENSIONS if normalized_weights[dimension] > 0]
    calculated = calculated.join(scores.add_prefix("puntuacion_"))
    calculated["perfil_vivienda"] = profile
    calculated["modalidad"] = mode
    calculated["alcance"] = "cuatro_dimensiones" if len(active) == 4 else "parcial_dimensiones_seleccionadas"
    calculated["dimensiones_activas_json"] = json.dumps(active, ensure_ascii=False)
    calculated["pesos_json"] = json.dumps(normalized_weights, ensure_ascii=False)
    calculated["metodo"] = "minmax"
    calculated["metodologia_version"] = METHODOLOGY_VERSION
    calculated["municipios_universo"] = len(scores)
    calculated["municipios_con_indice"] = int(calculated.valor_indice.notna().sum())
    details = []
    for code, row in calculated.iterrows():
        for dimension in DIMENSIONS:
            weight = normalized_weights[dimension]
            score = row["puntuacion_" + dimension]
            details.append({"codigo_ine": code, "perfil_vivienda": profile, "modalidad": mode,
                            "dimension": dimension, "peso": weight, "puntuacion": score,
                            "contribucion": 0.0 if weight == 0 else score * weight,
                            "estado_indice": row.estado_indice,
                            "nota": "Las aportaciones disponibles no constituyen un índice cuando falta una dimensión activa."})
    master = eligible[["codigo_ine", "municipio", "poblacion", "poblacion_anio"]]
    output = master.merge(calculated.reset_index(), on="codigo_ine", validate="one_to_one")
    return output, pd.DataFrame(details)


def compare_housing_profiles(matrices, weights, blocked_dimensions=()):
    """Compara perfiles en la misma cohorte, conservando las anclas propias de VC y VU."""
    vc, vu = matrices["VC"]["minmax"], matrices["VU"]["minmax"]
    complete = vc.notna().all(axis=1) & vu.notna().all(axis=1)
    codes = vc.index[complete]
    columns = ["codigo_ine", "ivp_vc", "ivp_vu", "posicion_vc_cohorte_comun", "posicion_vu_cohorte_comun",
               "diferencia_vu_menos_vc", "municipios_cohorte_comun"]
    if len(codes) == 0:
        return pd.DataFrame(columns=columns)
    left = calculate_index(vc.loc[codes], weights, blocked_dimensions=blocked_dimensions)
    right = calculate_index(vu.loc[codes], weights, blocked_dimensions=blocked_dimensions)
    frame = pd.DataFrame({"ivp_vc": left.valor_indice, "ivp_vu": right.valor_indice,
                          "posicion_vc_cohorte_comun": left.posicion,
                          "posicion_vu_cohorte_comun": right.posicion})
    frame["diferencia_vu_menos_vc"] = frame.ivp_vu - frame.ivp_vc
    frame["municipios_cohorte_comun"] = len(codes)
    return frame.reset_index()[columns]


def _validate_publication(tables, documents):
    for name, frame in tables.items():
        if Path(name).name != name or not name.endswith(".csv") or not frame.columns.is_unique:
            raise IntegrationError("Nombre o columnas de tabla IVP inválidos: " + name)
    for name in documents:
        if Path(name).name != name or name in {"validacion_ivp.json", "esquema.json", "RESUMEN.md"} or not name.endswith(".json"):
            raise IntegrationError("Documento IVP inválido: " + name)


def publish_ivp(tables, documents, validation, output_dir):
    """Publica el conjunto verificado, protegiendo entradas y archivos ajenos."""
    output = Path(output_dir)
    validate_output_location(output, validation["entradas"])
    _validate_publication(tables, documents)
    if output.exists() and not output.is_dir():
        raise IntegrationError("La salida IVP no es una carpeta.")
    if output.exists() and any(output.iterdir()):
        previous_path = output / "validacion_ivp.json"
        if not previous_path.exists():
            raise IntegrationError("La salida contiene archivos ajenos a la fase 6.")
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        allowed = set(previous.get("archivos", {})) | {"validacion_ivp.json"}
        if previous.get("fase") != 6 or set(p.name for p in output.iterdir()) - allowed:
            raise IntegrationError("La salida contiene archivos ajenos al conjunto regenerable del IVP.")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".ivp-", dir=output.parent))
    backup = None
    try:
        schema = {}
        for name, frame in tables.items():
            frame.to_csv(stage / name, index=False, encoding="utf-8-sig")
            schema[name] = {"filas": len(frame), "columnas": [{"nombre": str(c), "tipo_en_memoria": str(frame[c].dtype)} for c in frame]}
        for name, document in documents.items():
            (stage / name).write_text(json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (stage / "esquema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = ["# IVP de HabitaMadrid — fase 6", "",
                 "Índice orientativo propio. Proximidad geométrica y coste de alquiler; no es una medición oficial de calidad de vida.",
                 f"Edición {METHODOLOGY_VERSION}. Perfil inicial propuesto: VU (vivienda unifamiliar o rural). También se publica VC por separado.",
                 "Pesos generales: 25 % sanidad, 25 % educación, 25 % transporte y 25 % vivienda.",
                 "Normalización principal min–max inversa, referencias fijas, escala 0–100. Ausencias sin imputación ni redistribución municipal de pesos.",
                 "Los pesos personalizados se normalizan una vez para todos los municipios del escenario.", "",
                 "## Cobertura", "", "| Modalidad | Tipología | Con índice | Universo |", "|---|---|---:|---:|"]
        lines.extend(f"| {r.modalidad} | {r.perfil_vivienda} | {r.con_indice} | {r.universo} |" for r in tables["cobertura_ivp.csv"].itertuples())
        lines.extend(["", "## Sanidad", "", validation["sanidad"]["decision_publicacion"], "",
                      "## Sensibilidad", "",
                      "Se contrastan tres normalizaciones y 13 configuraciones de pesos: 39 escenarios por tipología, sin mezclar sus cohortes.",
                      "Las frecuencias top 10 son descriptivas de estos escenarios, no probabilidades. Los empates pueden ampliar el grupo.",
                      "Los cambios de normalización, pesos y tipología no constituyen intervalos de confianza.", "",
                      "## Archivos", "", "| Tabla | Filas |", "|---|---:|"])
        lines.extend(f"| {name} | {len(frame)} |" for name, frame in tables.items())
        lines.extend(["", "Consulte docs/FASE_6_IVP.md para las fórmulas, selección, límites y ejecución. La interfaz corresponde a la fase 7."])
        (stage / "RESUMEN.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        for item in validation["entradas"]:
            if file_sha256(item["path"]) != item["sha256"]:
                raise IntegrationError("Una entrada cambió durante el IVP: " + item["path"])
        completed = {**validation, "generado_utc": datetime.now(timezone.utc).isoformat(), "archivos": {}}
        for path in sorted(stage.iterdir()):
            completed["archivos"][path.name] = {"sha256": file_sha256(path), "bytes": path.stat().st_size, **schema.get(path.name, {})}
        (stage / "validacion_ivp.json").write_text(json.dumps(completed, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        if output.exists():
            backup = Path(tempfile.mkdtemp(prefix=".ivp-anterior-", dir=output.parent))
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


def run_ivp(root, output_dir=None, personalized_weights=None, progress=None):
    from src.ivp_health import prepare_health_review

    root = Path(root).resolve()
    output = Path(output_dir) if output_dir else root / "data/processed/ivp"
    notify = progress if progress else lambda message: None
    weights = normalize_weights(GENERAL_WEIGHTS if personalized_weights is None else personalized_weights)
    notify("Verificando las fases anteriores y sus huellas…")
    eligible, observations, definitions, inputs, phase5 = load_ivp_inputs(root)
    notify("Revisando el directorio oficial de atención primaria…")
    full_master, _ = load_validated_master(root / "data/processed/municipios")
    health = prepare_health_review(full_master, root / "data/processed/geografia", root / "research/sanidad_fase6_2026-10-15")
    inputs.extend(health["inputs"])
    # La configuración y el código del algoritmo también forman parte de la edición.
    for filename in ["src/ivp.py", "src/ivp_calculation.py", "src/ivp_sensitivity.py", "src/ivp_health.py",
                     "src/geo_common.py", "src/municipalities.py", "src/indicators.py",
                     "src/integration.py", "src/integration_common.py"]:
        inputs.append(_record(root / filename))
    inputs = list({item["path"]: item for item in inputs}.values())
    validate_output_location(output, inputs)
    selected, policies, mappings, blocked = select_ivp_indicators(eligible, observations, definitions, health)
    notify("Ajustando referencias fijas y calculando las dos modalidades…")
    references, normalized, reference_table = normalize_selected(selected, policies, blocked)
    matrices, dimensions = build_dimension_matrices(eligible, normalized, mappings)
    general, personalized, contributions, coverage = [], [], [], []
    scenarios, sensitivity_summaries, sensitivity_comparisons = [], [], []
    for profile in PROFILES:
        for mode, chosen_weights, collection in [("general", GENERAL_WEIGHTS, general), ("personalizado", weights, personalized)]:
            index, detail = profile_results(eligible, matrices[profile]["minmax"], profile, chosen_weights, mode, blocked)
            collection.append(index)
            contributions.append(detail)
            coverage.append({"perfil_vivienda": profile, "modalidad": mode, "universo": len(index),
                             "con_indice": int(index.valor_indice.notna().sum()),
                             "sin_indice": int(index.valor_indice.isna().sum()),
                             "estados_json": json.dumps({str(k): int(v) for k, v in index.estado_indice.value_counts().items()}, ensure_ascii=False)})
        notify("Contrastando 39 escenarios de normalización y pesos para " + profile + "…")
        sensitivity = analyze_sensitivity(matrices[profile], blocked_dimensions=blocked)
        for key, collection in [("scores", scenarios), ("summary", sensitivity_summaries), ("comparisons", sensitivity_comparisons)]:
            table = sensitivity[key].copy()
            table["perfil_vivienda"] = profile
            collection.append(table)
    tables = dict(health["tables"])
    generated = {
        "seleccion_indicadores.csv": policies,
        "observaciones_seleccionadas.csv": selected,
        "referencias_normalizacion.csv": reference_table,
        "puntuaciones_indicadores.csv": normalized,
        "dimensiones_ivp.csv": dimensions,
        "ivp_general.csv": pd.concat(general, ignore_index=True),
        "ivp_personalizado.csv": pd.concat(personalized, ignore_index=True),
        "contribuciones_ivp.csv": pd.concat(contributions, ignore_index=True),
        "cobertura_ivp.csv": pd.DataFrame(coverage),
        "sensibilidad_escenarios.csv": pd.concat(scenarios, ignore_index=True),
        "sensibilidad_municipios.csv": pd.concat(sensitivity_summaries, ignore_index=True),
        "sensibilidad_comparaciones.csv": pd.concat(sensitivity_comparisons, ignore_index=True),
        "contraste_tipologias.csv": compare_housing_profiles(matrices, GENERAL_WEIGHTS, blocked),
    }
    if set(tables) & set(generated):
        raise IntegrationError("El módulo sanitario repite nombres de salida IVP.")
    tables.update(generated)
    usable = bool(health["usable"])
    health_summary = {**health["summary"], "apto_para_proxy_ivp": usable,
                      "decision_publicacion": "Se utiliza la proximidad al directorio oficial revisado de centros de salud y consultorios. No mide hospital, asignación, calidad, horarios o tiempo de acceso." if usable else
                                              "Sanidad permanece bloqueada: no se publica IVP general con peso sanitario positivo hasta completar la revisión."}
    configuration = {
        "metodologia_version": METHODOLOGY_VERSION, "perfil_inicial": "VU",
        "perfiles": PROFILES, "indicadores_comunes": mappings,
        "pesos_generales": GENERAL_WEIGHTS, "pesos_personalizados": weights,
        "normalizacion_principal": "minmax", "metodos_sensibilidad": list(METHODS),
        "sensibilidad": {"transferencia_peso": 0.10, "configuraciones_pesos": 13, "escenarios_por_perfil": 39,
                         "top_n": 10, "empates": "puesto mínimo compartido, sin redondeo previo"},
        "ausencias": "Sin índice si falta una dimensión con peso positivo; sin reponderación municipal.",
        "bloqueos_dimensiones": list(blocked), "anclas_fijas_tras_filtros": True,
        "escala": [0, 100], "agregacion": "media aritmética ponderada compensatoria",
        "publicacion": "Índice orientativo propio; proxies de proximidad y coste, no calidad de vida oficial.",
    }
    validation = {
        "schema_version": 1, "fase": 6, "metodologia_version": METHODOLOGY_VERSION,
        "poblacion_anio": int(phase5["poblacion_anio"]), "regla": "poblacion <= 50000",
        "municipios_elegibles": len(eligible), "entradas": inputs,
        "configuracion": configuration, "sanidad": health_summary,
        "cobertura": coverage, "normalizacion_calculada": True, "pesos_asignados": True,
        "motor_ivp_implementado": True, "ivp_general_publicable": usable,
        "ivp_calculado": bool(pd.concat(general).valor_indice.notna().any()),
        "ranking_calculado": bool(pd.concat(general).posicion.notna().any()),
        "tiempos_viaje_calculados": False, "imputaciones": 0,
        "controles": {"fuentes_previas_sin_alterar": True, "una_medida_por_dimension": True,
                      "tipologias_alternativas_separadas": True, "anclas_fijas": True,
                      "ausencias_sin_reponderacion": True, "empates_compartidos": True},
        "versiones": {"python": platform.python_version(), **{p: version(p) for p in ["pandas", "numpy"]}},
        "metodologia_referencias": [*phase5["metodologia_referencias"],
            {"titulo": "JRC: Weighting", "url": "https://knowledge-for-policy.ec.europa.eu/composite-indicators/toolkit_en/navigation-page/10-step-guide_en/step-6-weighting_en"},
            {"titulo": "JRC: Sensitivity analysis", "url": "https://knowledge-for-policy.ec.europa.eu/composite-indicators/toolkit_en/navigation-page/10-step-guide_en/step-8-sensitivity-analysis_en"}],
    }
    notify("Publicando índices, contribuciones, referencias y sensibilidad…")
    return publish_ivp(tables, {"normalizacion.json": references, "configuracion_ivp.json": configuration}, validation, output)
