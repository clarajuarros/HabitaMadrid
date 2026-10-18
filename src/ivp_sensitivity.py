"""Escenarios deterministas de sensibilidad del IVP, sin completar datos.

Las frecuencias describen este conjunto finito de decisiones metodológicas;
no son probabilidades, intervalos de confianza ni incertidumbre muestral.
Cada llamada utiliza un único perfil de vivienda y un universo municipal fijo.
"""
from collections.abc import Mapping
import math
from numbers import Integral, Real

import numpy as np
import pandas as pd

from src.integration_common import IntegrationError
from src.ivp_calculation import DIMENSIONS, calculate_index, normalize_weights


def _active_dimensions(active_dimensions):
    if isinstance(active_dimensions, (str, bytes)):
        raise IntegrationError("Las dimensiones activas deben ser una colección de nombres.")
    try:
        supplied = tuple(active_dimensions)
    except TypeError as exc:
        raise IntegrationError("Las dimensiones activas no son válidas.") from exc
    if not supplied or any(not isinstance(value, str) for value in supplied):
        raise IntegrationError("Debe existir al menos una dimensión activa conocida.")
    if len(set(supplied)) != len(supplied) or not set(supplied).issubset(DIMENSIONS):
        raise IntegrationError("Las dimensiones activas están duplicadas o son desconocidas.")
    return tuple(dimension for dimension in DIMENSIONS if dimension in supplied)


def weight_scenarios(active_dimensions, delta=0.10):
    """Devuelve pesos iguales y todas las transferencias dirigidas de ``delta``.

    Los mapas siempre contienen las cuatro dimensiones. Las inactivas pesan
    cero por decisión del escenario, nunca por una ausencia de un municipio.
    Para cuatro dimensiones hay 13 escenarios; para tres, siete. ``delta`` es
    positivo y no puede superar el peso base, para evitar pesos negativos.
    """
    active = _active_dimensions(active_dimensions)
    if isinstance(delta, (bool, np.bool_)) or not isinstance(delta, Real):
        raise IntegrationError("El desplazamiento de pesos debe ser un número real.")
    try:
        shift = float(delta)
    except (OverflowError, TypeError, ValueError) as exc:
        raise IntegrationError("El desplazamiento de pesos no es representable.") from exc
    equal_weight = 1.0 / len(active)
    if not math.isfinite(shift) or not 0 < shift <= equal_weight:
        raise IntegrationError("El desplazamiento debe ser positivo y no superar el peso base.")
    base = {dimension: equal_weight if dimension in active else 0.0
            for dimension in DIMENSIONS}
    scenarios = [{"escenario_pesos": "iguales", "pesos": normalize_weights(base)}]
    for donor in active:
        for recipient in active:
            if donor == recipient:
                continue
            weights = dict(base)
            weights[donor] -= shift
            weights[recipient] += shift
            scenarios.append({
                "escenario_pesos": f"transferencia_{donor}_a_{recipient}",
                "pesos": normalize_weights(weights),
            })
    return scenarios


def _matrices(score_matrices):
    if not isinstance(score_matrices, Mapping) or not score_matrices:
        raise IntegrationError("Se necesita al menos una matriz de puntuaciones.")
    if any(not isinstance(method, str) or not method for method in score_matrices):
        raise IntegrationError("Cada método de normalización necesita un nombre de texto.")
    if "minmax" not in score_matrices:
        raise IntegrationError("La sensibilidad necesita el método de referencia minmax.")
    methods = ["minmax"] + sorted(method for method in score_matrices if method != "minmax")
    reference_codes = None
    result = {}
    for method in methods:
        matrix = score_matrices[method]
        if (not isinstance(matrix, pd.DataFrame) or not matrix.columns.is_unique
                or set(matrix.columns) != set(DIMENSIONS)):
            raise IntegrationError(f"La matriz {method} debe tener las cuatro dimensiones exactas.")
        if (matrix.empty or not matrix.index.is_unique
                or any(not isinstance(code, str) or not code for code in matrix.index)):
            raise IntegrationError(f"La matriz {method} necesita códigos únicos de texto y filas.")
        codes = set(matrix.index)
        if reference_codes is None:
            reference_codes = codes
        elif codes != reference_codes:
            raise IntegrationError("Todos los métodos deben conservar el mismo universo municipal.")
        # Orden fijo, sin convertir identificadores ni alterar las entradas.
        ordered = matrix.loc[sorted(codes), list(DIMENSIONS)].copy(deep=True)
        ordered.index.name = "codigo_ine"
        result[method] = ordered
    return result


def _comparison(reference, current):
    comparable = (reference.valor_indice.notna() & current.valor_indice.notna()
                  & reference.posicion.notna() & current.posicion.notna())
    reference_ranks = reference.loc[comparable, "posicion"].astype(float)
    current_ranks = current.loc[comparable, "posicion"].astype(float)
    displacement = (current_ranks - reference_ranks).abs()
    # Rangos medios para empates y Pearson sobre esos rangos: no exige SciPy.
    # Un conjunto constante o de menos de dos pares no tiene correlación.
    correlation = float("nan")
    if len(reference_ranks) >= 2 and reference_ranks.nunique() > 1 and current_ranks.nunique() > 1:
        correlation = float(reference_ranks.rank(method="average").corr(
            current_ranks.rank(method="average")))
    return {
        "n_comparables": int(comparable.sum()),
        "spearman_posiciones": correlation,
        "desplazamiento_max": float(displacement.max()) if len(displacement) else float("nan"),
        "desplazamiento_medio": float(displacement.mean()) if len(displacement) else float("nan"),
    }


def analyze_sensitivity(score_matrices, *, active_dimensions=DIMENSIONS,
                        blocked_dimensions=(), top_n=10):
    """Calcula escenarios y estabilidad sobre un único universo municipal.

    La referencia de comparación es siempre ``minmax`` con pesos ``iguales``.
    Se comparan los pares con índice y posición en ambos escenarios, sin
    reconstruir rangos en esa intersección. Las posiciones proceden del motor
    con empates compartidos; ``posicion <= top_n`` incluye todos los empates.

    ``fraccion_top_n`` divide apariciones entre escenarios con índice de ese
    municipio, no entre todos los escenarios. Si no hay ninguno, permanece
    ausente. Un bloqueo de una dimensión con peso positivo conserva todos los
    resultados vacíos. La modalidad parcial solo nace de una selección expresa
    de ``active_dimensions``; esta función no elimina dimensiones por su cuenta.
    """
    if isinstance(top_n, (bool, np.bool_)) or not isinstance(top_n, Integral) or top_n < 1:
        raise IntegrationError("El umbral top_n debe ser un entero positivo.")
    matrices = _matrices(score_matrices)
    scenarios = weight_scenarios(active_dimensions)
    # Materializar una sola vez evita consumir generadores de bloqueos por
    # escenario; el motor comprueba nombres y aplica únicamente pesos positivos.
    if isinstance(blocked_dimensions, (str, bytes)):
        raise IntegrationError("Las dimensiones bloqueadas deben ser una colección de nombres.")
    try:
        blocked = tuple(blocked_dimensions)
    except TypeError as exc:
        raise IntegrationError("Las dimensiones bloqueadas no son válidas.") from exc
    results = {}
    long_tables = []
    for method, matrix in matrices.items():
        for scenario in scenarios:
            name = scenario["escenario_pesos"]
            result = calculate_index(matrix, scenario["pesos"], blocked_dimensions=blocked)
            results[(method, name)] = result
            selected = result[["valor_indice", "posicion", "estado_indice"]].copy()
            selected.insert(0, "escenario_pesos", name)
            selected.insert(0, "metodo", method)
            selected = selected.reset_index()
            long_tables.append(selected[["metodo", "escenario_pesos", "codigo_ine",
                                         "valor_indice", "posicion", "estado_indice"]])
    scores = pd.concat(long_tables, ignore_index=True)
    scores["posicion"] = scores.posicion.astype("Int64")
    scenario_count = len(matrices) * len(scenarios)
    summaries = []
    for code, group in scores.groupby("codigo_ine", sort=True):
        valid = group.valor_indice.notna() & group.posicion.notna()
        ranks = group.loc[valid, "posicion"].astype(float)
        count = int(valid.sum())
        appearances = int((ranks <= top_n).sum())
        summaries.append({
            "codigo_ine": code,
            "escenarios_totales": scenario_count,
            "escenarios_con_indice": count,
            "posicion_min": int(ranks.min()) if count else pd.NA,
            "posicion_mediana": float(ranks.median()) if count else float("nan"),
            "posicion_max": int(ranks.max()) if count else pd.NA,
            "amplitud_posicion": int(ranks.max() - ranks.min()) if count else pd.NA,
            "apariciones_top_n": appearances,
            "fraccion_top_n": appearances / count if count else float("nan"),
        })
    summary = pd.DataFrame(summaries)
    for column in ("posicion_min", "posicion_max", "amplitud_posicion"):
        summary[column] = summary[column].astype("Int64")
    reference = results[("minmax", "iguales")]
    comparisons = []
    for (method, scenario), current in results.items():
        comparisons.append({"metodo": method, "escenario_pesos": scenario,
                            **_comparison(reference, current)})
    return {"scores": scores, "summary": summary, "comparisons": pd.DataFrame(comparisons)}
