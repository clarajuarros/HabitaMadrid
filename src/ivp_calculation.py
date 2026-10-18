"""Motor numérico del IVP: referencias fijas y agregación sin imputación.

Las funciones no leen archivos ni eligen indicadores. El ajuste utiliza la
población municipal de referencia completa; los filtros posteriores solo
transforman datos con esa referencia y nunca recalculan sus extremos o rangos.
"""
from collections.abc import Mapping
import json
import math
from numbers import Integral, Real

import numpy as np
import pandas as pd

from src.integration_common import IntegrationError


DIMENSIONS = ("sanidad", "educacion", "transporte", "vivienda")
METHODS = ("minmax", "winsor_p05_p95", "percentil")
DIRECTIONS = ("menor_mejor", "mayor_mejor")


def _numeric_series(values, name):
    """Convierte números reales y ausencias explícitas, nunca texto o booleanos."""
    if not isinstance(values, pd.Series):
        raise IntegrationError(f"{name} debe ser una serie de pandas.")
    result = []
    for value in values:
        if value is None or value is pd.NA:
            result.append(float("nan"))
            continue
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
            raise IntegrationError(f"{name} contiene un valor que no es numérico real.")
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise IntegrationError(f"{name} contiene un número no representable.") from exc
        if math.isinf(number):
            raise IntegrationError(f"{name} contiene un infinito.")
        result.append(number)
    return pd.Series(result, index=values.index, name=values.name, dtype=float)


def fit_normalization(values, direction="menor_mejor", method="minmax"):
    """Ajusta y devuelve una referencia serializable con JSON estricto.

    La winsorización fija cuantiles lineales 0,05 y 0,95 para la transformación;
    no modifica los valores originales. Una referencia constante produce 50,
    una convención explícita que no atribuye diferencias inexistentes.
    """
    if method not in METHODS or direction not in DIRECTIONS:
        raise IntegrationError("Método o sentido de normalización desconocido.")
    finite = _numeric_series(values, "Valores de ajuste").dropna().to_numpy()
    reference = {
        "version": 1, "method": method, "direction": direction,
        "estado": "sin_observaciones", "n_observaciones": int(len(finite)),
        "minimo_observado": None, "maximo_observado": None,
        "limite_inferior": None, "limite_superior": None,
        "valores_ordenados": [], "cuantiles": [0.05, 0.95] if method == "winsor_p05_p95" else [],
    }
    if not len(finite):
        return reference
    lower, upper = float(finite.min()), float(finite.max())
    reference["minimo_observado"] = lower
    reference["maximo_observado"] = upper
    if method == "winsor_p05_p95":
        # El escalado evita desbordamientos de resta entre extremos finitos.
        if math.isfinite(upper - lower):
            limits = np.quantile(finite, [0.05, 0.95], method="linear")
        else:
            limits = np.quantile(finite / 2, [0.05, 0.95], method="linear") * 2
        lower, upper = map(float, limits)
    if method == "percentil":
        reference["valores_ordenados"] = np.sort(finite).tolist()
    reference["limite_inferior"] = lower
    reference["limite_superior"] = upper
    reference["estado"] = "constante" if lower == upper else "ajustada"
    return reference


def _validate_reference(reference):
    if not isinstance(reference, Mapping):
        raise IntegrationError("La referencia de normalización debe ser un mapa.")
    required = {"version", "method", "direction", "estado", "n_observaciones",
                "minimo_observado", "maximo_observado", "limite_inferior", "limite_superior",
                "valores_ordenados", "cuantiles"}
    if not required.issubset(reference):
        raise IntegrationError("La referencia de normalización está incompleta.")
    n = reference["n_observaciones"]
    if (type(reference["version"]) is not int or reference["version"] != 1
            or isinstance(n, (bool, np.bool_)) or not isinstance(n, Integral) or n < 0
            or reference["method"] not in METHODS or reference["direction"] not in DIRECTIONS
            or reference["estado"] not in ("sin_observaciones", "constante", "ajustada")):
        raise IntegrationError("Referencia de normalización no válida.")
    try:
        json.dumps(dict(reference), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise IntegrationError("La referencia no admite JSON estricto.") from exc
    limits = [reference[key] for key in ("minimo_observado", "maximo_observado",
                                        "limite_inferior", "limite_superior")]
    if n == 0:
        if reference["estado"] != "sin_observaciones" or any(value is not None for value in limits):
            raise IntegrationError("Referencia sin observaciones inconsistente.")
    else:
        if any(isinstance(value, (bool, np.bool_)) or not isinstance(value, Real)
               or not math.isfinite(float(value)) for value in limits):
            raise IntegrationError("Extremos de referencia no válidos.")
        observed_min, observed_max, lower, upper = limits
        if not observed_min <= lower <= upper <= observed_max:
            raise IntegrationError("Extremos de referencia desordenados.")
        expected = "constante" if lower == upper else "ajustada"
        if reference["estado"] != expected:
            raise IntegrationError("Estado de referencia incompatible con sus extremos.")
    ordered = reference["valores_ordenados"]
    if not isinstance(ordered, list):
        raise IntegrationError("Los valores de referencia percentil deben ser una lista.")
    if reference["method"] == "percentil":
        observed = _numeric_series(pd.Series(ordered, dtype=object), "Referencia percentil")
        if len(observed) != n or observed.isna().any() or not observed.is_monotonic_increasing:
            raise IntegrationError("Población de referencia percentil no válida.")
        if n and (observed.iloc[0] != limits[0] or observed.iloc[-1] != limits[1]
                  or limits[0] != limits[2] or limits[1] != limits[3]):
            raise IntegrationError("La referencia percentil no coincide con sus extremos.")
    elif ordered:
        raise IntegrationError("Solo el método percentil utiliza valores ordenados.")
    expected_quantiles = [0.05, 0.95] if reference["method"] == "winsor_p05_p95" else []
    if reference["cuantiles"] != expected_quantiles:
        raise IntegrationError("Cuantiles de referencia no válidos.")
    if reference["method"] == "minmax" and n and limits[:2] != limits[2:]:
        raise IntegrationError("Los extremos minmax deben coincidir con los observados.")


def transform_normalization(values, reference):
    """Transforma a 0–100 conservando índice y ausencias con una referencia fija.

    En percentiles, el rango medio es (n_menores + n_menores_o_iguales + 1)/2.
    Los valores nuevos entre observaciones usan ese rango de inserción; los
    extremos nuevos se acotan a 0–100. Los empates observados comparten rango.
    """
    _validate_reference(reference)
    numeric = _numeric_series(values, "Valores de transformación")
    result = pd.Series(float("nan"), index=numeric.index, name=numeric.name, dtype=float)
    available = numeric.notna().to_numpy()
    if reference["estado"] == "sin_observaciones" or not available.any():
        return result
    finite = numeric.to_numpy()[available]
    if reference["estado"] == "constante":
        transformed = np.full(len(finite), 50.0)
    elif reference["method"] == "percentil":
        ordered = np.asarray(reference["valores_ordenados"], dtype=float)
        left = np.searchsorted(ordered, finite, side="left")
        right = np.searchsorted(ordered, finite, side="right")
        rank = (left + right + 1) / 2
        transformed = np.clip(100 * ((rank - 1) / (len(ordered) - 1)), 0, 100)
    else:
        lower, upper = reference["limite_inferior"], reference["limite_superior"]
        clipped = np.clip(finite, lower, upper)
        span = upper - lower
        if math.isfinite(span):
            fraction = (clipped - lower) / span
        else:
            fraction = (clipped / 2 - lower / 2) / (upper / 2 - lower / 2)
        transformed = 100 * np.clip(fraction, 0, 1)
    if reference["direction"] == "menor_mejor":
        transformed = 100 - transformed
    result.iloc[np.flatnonzero(available)] = transformed
    return result


def normalize_weights(weights):
    """Normaliza los cuatro pesos sin mutar la entrada ni redistribuir ausencias."""
    if not isinstance(weights, Mapping) or set(weights) != set(DIMENSIONS):
        raise IntegrationError("Los pesos deben contener exactamente las cuatro dimensiones.")
    numbers = {}
    for dimension in DIMENSIONS:
        value = weights[dimension]
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
            raise IntegrationError(f"El peso de {dimension} debe ser numérico real.")
        try:
            number = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise IntegrationError(f"Peso no representable en {dimension}.") from exc
        if not math.isfinite(number) or number < 0:
            raise IntegrationError(f"Peso no finito o negativo en {dimension}.")
        numbers[dimension] = number
    maximum = max(numbers.values())
    if maximum == 0:
        raise IntegrationError("Al menos una dimensión debe tener peso positivo.")
    scaled = {key: value / maximum for key, value in numbers.items()}
    denominator = math.fsum(scaled.values())
    normalized = {key: value / denominator for key, value in scaled.items()}
    # Un peso positivo que desaparece por subdesbordamiento cambia el contrato.
    if any(numbers[key] > 0 and normalized[key] == 0 for key in DIMENSIONS):
        raise IntegrationError("Los pesos presentan una escala numérica no representable.")
    return normalized


def calculate_index(scores, weights, *, blocked_dimensions=()):
    """Agrega solo filas completas para todas las dimensiones con peso positivo.

    Ningún municipio recibe pesos distintos por falta de datos. Un bloqueo se
    aplica a todo el escenario, salvo que el peso de esa dimensión sea cero.
    Las posiciones son rangos mínimos descendentes sobre valores sin redondear;
    se conserva el orden original de filas y los empates no se deshacen.
    """
    if (not isinstance(scores, pd.DataFrame) or not scores.columns.is_unique
            or set(scores.columns) != set(DIMENSIONS)):
        raise IntegrationError("Las puntuaciones deben contener las cuatro dimensiones exactas.")
    if not scores.index.is_unique:
        raise IntegrationError("Los códigos municipales de las puntuaciones deben ser únicos.")
    normalized = normalize_weights(weights)
    if isinstance(blocked_dimensions, (str, bytes)):
        raise IntegrationError("Las dimensiones bloqueadas deben ser una colección de nombres.")
    try:
        blocked = set(blocked_dimensions)
    except TypeError as exc:
        raise IntegrationError("Las dimensiones bloqueadas no son válidas.") from exc
    if not blocked.issubset(DIMENSIONS):
        raise IntegrationError("Hay dimensiones bloqueadas desconocidas.")
    numeric = pd.DataFrame({key: _numeric_series(scores[key], f"Puntuación {key}")
                            for key in DIMENSIONS}, index=scores.index)
    if ((numeric < 0) | (numeric > 100)).any().any():
        raise IntegrationError("Las puntuaciones deben estar entre 0 y 100.")
    active = [key for key in DIMENSIONS if normalized[key] > 0]
    active_blocked = [key for key in active if key in blocked]
    missing_json, blocked_json, states, available_weights, values = [], [], [], [], []
    for row in numeric.itertuples(index=False, name=None):
        by_dimension = dict(zip(DIMENSIONS, row))
        missing = [key for key in active if math.isnan(by_dimension[key])]
        missing_json.append(json.dumps(missing, ensure_ascii=False))
        blocked_json.append(json.dumps(active_blocked, ensure_ascii=False))
        available_weights.append(math.fsum(normalized[key] for key in active
                                          if key not in missing and key not in blocked))
        if missing or active_blocked:
            state = ("dimensiones_faltantes_y_bloqueadas" if missing and active_blocked
                     else "dimensiones_faltantes" if missing else "dimensiones_bloqueadas")
            states.append(state)
            values.append(float("nan"))
        else:
            states.append("disponible")
            weighted = math.fsum(normalized[key] * by_dimension[key] for key in active)
            values.append(min(100.0, max(0.0, weighted)))
    result = pd.DataFrame({"valor_indice": pd.Series(values, index=scores.index, dtype=float),
                           "estado_indice": states, "peso_disponible": available_weights,
                           "dimensiones_faltantes_json": missing_json,
                           "dimensiones_bloqueadas_json": blocked_json}, index=scores.index)
    result["posicion"] = result.valor_indice.rank(method="min", ascending=False).astype("Int64")
    return result
