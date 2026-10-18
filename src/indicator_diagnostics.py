"""Cobertura y relaciones descriptivas de los indicadores de la fase 5.

No normaliza, asigna pesos, elimina variables ni calcula un índice. Spearman se
calcula sobre los valores originales mediante rangos medios para los empates,
usando únicamente los municipios con ambos valores disponibles. El mínimo de
30 pares es una regla operativa para publicar este diagnóstico, no una prueba
de significación ni una garantía de representatividad.
"""
from itertools import combinations
import json
import math
import re

import pandas as pd


MINIMUM_PAIRS = 30
OBSERVATION_COLUMNS = {"codigo_ine", "indicador_id", "valor", "estado"}
DEFINITION_COLUMNS = {"indicador_id", "rol", "dimension", "grupo_redundancia"}
CORRELATION_COLUMNS = [
    "indicador_a", "nombre_a", "dimension_a", "grupo_redundancia_a",
    "indicador_b", "nombre_b", "dimension_b", "grupo_redundancia_b",
    "mismo_grupo_redundancia", "municipios_universo", "n_pares",
    "cobertura_pares_porcentaje", "minimo_pares_operativo", "periodos_a",
    "periodos_b", "pares_periodo_coincidente", "pares_periodo_diferente",
    "pares_periodo_desconocido", "etiquetas_periodo_coinciden",
    "comparabilidad_temporal_confirmada", "periodos_comparables", "spearman",
    "estado_correlacion", "interpretacion",
]


def _json_list(values):
    return json.dumps(sorted(set(values)), ensure_ascii=False)


def _periods(frame):
    """Mantiene periodo y año visibles sin convertir fechas ausentes en 'nan'."""
    result = []
    for row in frame.to_dict("records"):
        period = row.get("periodo")
        year = row.get("anio")
        if pd.notna(period) and str(period).strip():
            result.append(str(period).strip())
        elif pd.notna(year) and str(year).strip():
            if isinstance(year, (int, float)) and float(year).is_integer():
                result.append(str(int(year)))
            else:
                result.append(str(year).strip())
        else:
            result.append("")
    return pd.Series(result, index=frame.index, dtype="object")


def _observed_period(value):
    """Solo reconoce años e intervalos, nunca fechas de consulta de un registro.

    Que dos catálogos se descarguen el mismo día no acredita que describan
    servicios vigentes en un mismo periodo. Las etiquetas opacas tampoco lo
    acreditan; se mantienen visibles para revisión sin asumir comparabilidad.
    """
    return re.fullmatch(r"[0-9]{4}(?:[-–][0-9]{4})?", value) is not None


def _validate(observations, definitions):
    missing_obs = OBSERVATION_COLUMNS - set(observations.columns)
    missing_defs = DEFINITION_COLUMNS - set(definitions.columns)
    if missing_obs or missing_defs:
        raise ValueError("Faltan columnas para los diagnósticos: observaciones=" +
                         str(sorted(missing_obs)) + "; definiciones=" + str(sorted(missing_defs)))
    if not {"anio", "periodo"}.intersection(observations.columns):
        raise ValueError("Cada observación necesita anio y/o periodo, aunque sea desconocido.")
    if observations.empty or definitions.empty:
        raise ValueError("El diagnóstico requiere observaciones y definiciones no vacías.")
    obs, defs = observations.copy(deep=True), definitions.copy(deep=True)
    for frame, columns in [(obs, ["codigo_ine", "indicador_id", "estado"]),
                           (defs, ["indicador_id", "rol", "dimension"])]:
        for column in columns:
            if frame[column].isna().any() or frame[column].astype(str).str.strip().eq("").any():
                raise ValueError("La columna " + column + " contiene claves o etiquetas vacías.")
            frame[column] = frame[column].astype(str)
    if not obs["codigo_ine"].str.fullmatch(r"28[0-9]{3}").all():
        raise ValueError("Los códigos municipales deben conservar el INE de cinco dígitos de Madrid.")
    if defs["indicador_id"].duplicated().any():
        raise ValueError("Las definiciones contienen un indicador_id duplicado.")
    if obs.duplicated(["codigo_ine", "indicador_id"]).any():
        raise ValueError("Hay más de una observación por municipio e indicador.")
    if set(obs["indicador_id"]) != set(defs["indicador_id"]):
        raise ValueError("Las observaciones y las definiciones no contienen los mismos indicadores.")
    codes = set(obs["codigo_ine"])
    for identifier, group in obs.groupby("indicador_id", sort=False):
        if set(group["codigo_ine"]) != codes:
            raise ValueError("La malla municipal no es uniforme para " + identifier +
                             ": las ausencias deben figurar con valor nulo.")
    try:
        obs["valor"] = pd.to_numeric(obs["valor"], errors="raise").astype(float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("valor debe contener números o ausencias explícitas.") from exc
    if obs["valor"].map(lambda value: pd.notna(value) and not math.isfinite(value)).any():
        raise ValueError("Los valores infinitos no son observaciones válidas.")
    available = obs["valor"].notna()
    if not available.eq(obs["estado"].eq("disponible")).all():
        raise ValueError("Solo estado=disponible puede tener valor, y debe ser finito.")
    obs["_periodo"] = _periods(obs)
    defs["grupo_redundancia"] = defs["grupo_redundancia"].fillna("").astype(str)
    if "nombre" not in defs:
        defs["nombre"] = defs["indicador_id"]
    defs["nombre"] = defs["nombre"].fillna(defs["indicador_id"])
    return obs, defs, len(codes)


def indicator_diagnostics(observations: pd.DataFrame, definitions: pd.DataFrame) -> dict:
    """Devuelve tres tablas CSV y un resumen serializable, sin modificar entradas.

    Se exige una fila por cada combinación municipio/indicador, también para
    ausencias. El universo se obtiene de esa malla validada: 155 en los datos
    reales, sin fijarlo en el código. Una fila con valor finito debe declarar
    exactamente estado='disponible'; los demás estados deben tener valor nulo.
    Solo rol='candidato_ivp' participa en las correlaciones. Los grupos de
    redundancia son avisos editoriales, nunca órdenes de eliminar o promediar.
    """
    obs, defs, count = _validate(observations, definitions)
    definition_lookup = defs.set_index("indicador_id")
    groups = {identifier: group.set_index("codigo_ine").sort_index()
              for identifier, group in obs.groupby("indicador_id", sort=True)}
    coverage, states = [], []
    for identifier, group in groups.items():
        definition = definition_lookup.loc[identifier]
        values = group["valor"].dropna()
        record = {
            "indicador_id": identifier, "nombre": definition["nombre"],
            "rol": definition["rol"], "dimension": definition["dimension"],
            "grupo_redundancia": definition["grupo_redundancia"],
            "municipios_universo": count, "con_valor": len(values),
            "sin_valor": count - len(values),
            "cobertura_porcentaje": len(values) / count * 100,
            "ceros_observados": int(values.eq(0).sum()),
            "valores_distintos": int(values.nunique()),
            "periodos_con_valor": _json_list(group.loc[group["valor"].notna(), "_periodo"]),
        }
        for key, value in [("minimo", values.min()), ("p25", values.quantile(.25)),
                           ("mediana", values.median()), ("p75", values.quantile(.75)),
                           ("maximo", values.max()), ("media", values.mean())]:
            record[key] = float(value) if pd.notna(value) else float("nan")
        coverage.append(record)
        for state, n in group["estado"].value_counts().sort_index().items():
            states.append({"indicador_id": identifier, "estado": state,
                           "municipios": int(n), "municipios_universo": count,
                           "porcentaje": int(n) / count * 100})
    correlations = []
    candidate_ids = sorted(defs.loc[defs["rol"].eq("candidato_ivp"), "indicador_id"])
    for first, second in combinations(candidate_ids, 2):
        a, b = groups[first], groups[second]
        definition_a, definition_b = definition_lookup.loc[first], definition_lookup.loc[second]
        pair = pd.DataFrame({"a": a["valor"], "b": b["valor"],
                             "periodo_a": a["_periodo"], "periodo_b": b["_periodo"]}).dropna(subset=["a", "b"])
        known = pair["periodo_a"].ne("") & pair["periodo_b"].ne("")
        same = known & pair["periodo_a"].eq(pair["periodo_b"])
        different = known & ~same
        n = len(pair)
        labels_match = bool(n > 0 and same.all())
        temporal_confirmed = bool(labels_match and
                                  pair["periodo_a"].map(_observed_period).all() and
                                  pair["periodo_b"].map(_observed_period).all())
        constant = n > 0 and (pair["a"].nunique() < 2 or pair["b"].nunique() < 2)
        if constant:
            status, coefficient = "variable_constante_en_pares", float("nan")
        elif n < MINIMUM_PAIRS:
            status, coefficient = "pares_insuficientes", float("nan")
        else:
            status = "calculada_descriptiva"
            coefficient = float(pair["a"].rank(method="average").corr(pair["b"].rank(method="average")))
        correlations.append({
            "indicador_a": first, "nombre_a": definition_a["nombre"],
            "dimension_a": definition_a["dimension"], "grupo_redundancia_a": definition_a["grupo_redundancia"],
            "indicador_b": second, "nombre_b": definition_b["nombre"],
            "dimension_b": definition_b["dimension"], "grupo_redundancia_b": definition_b["grupo_redundancia"],
            "mismo_grupo_redundancia": bool(definition_a["grupo_redundancia"] and
                                            definition_a["grupo_redundancia"] == definition_b["grupo_redundancia"]),
            "municipios_universo": count, "n_pares": n,
            "cobertura_pares_porcentaje": n / count * 100,
            "minimo_pares_operativo": MINIMUM_PAIRS,
            "periodos_a": _json_list(pair["periodo_a"]), "periodos_b": _json_list(pair["periodo_b"]),
            "pares_periodo_coincidente": int(same.sum()),
            "pares_periodo_diferente": int(different.sum()),
            "pares_periodo_desconocido": int((~known).sum()),
            "etiquetas_periodo_coinciden": labels_match,
            "comparabilidad_temporal_confirmada": temporal_confirmed,
            "periodos_comparables": temporal_confirmed,
            "spearman": coefficient, "estado_correlacion": status,
            "interpretacion": "Relación descriptiva entre valores originales; no implica causalidad ni fija pesos o selección.",
        })
    correlation_frame = pd.DataFrame(correlations, columns=CORRELATION_COLUMNS)
    summary = {
        "municipios_universo": count, "indicadores": len(defs),
        "candidatos_ivp": len(candidate_ids), "pares_candidatos": len(correlations),
        "minimo_pares_operativo": MINIMUM_PAIRS,
        "correlaciones_calculadas": sum(record["estado_correlacion"] == "calculada_descriptiva" for record in correlations),
        "pares_con_periodos_diferentes": sum(record["pares_periodo_diferente"] > 0 for record in correlations),
        "malla_municipal_uniforme": True,
        "imputaciones": 0,
        "metodo_correlacion": "Spearman sobre rangos medios de valores originales, casos completos por pareja.",
        "alcance": "Diagnóstico descriptivo; sin inferencia, eliminación automática, pesos, normalización ni IVP.",
    }
    return {"tables": {"cobertura_indicadores.csv": pd.DataFrame(coverage),
                       "estados_indicadores.csv": pd.DataFrame(states),
                       "correlaciones_candidatos.csv": correlation_frame},
            "summary": summary}
