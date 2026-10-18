"""Housing subindex extension alongside the immutable IVP 1.0 engine."""
import math,json
from numbers import Real
from collections.abc import Mapping
import numpy as np
import pandas as pd
from src.integration_common import IntegrationError
from src.ivp_calculation import _numeric_series

def calculate_composite_dimension(scores, weights):
    """Combine selected, already-normalized indicators without imputing gaps.

    Internal weights sum to one and never change the global dimension weight.
    Only zero-weight indicators may be absent. No municipal reweighting.
    """
    if not isinstance(scores, pd.DataFrame) or not scores.columns.is_unique or not scores.index.is_unique:
        raise IntegrationError('Las puntuaciones internas deben tener filas y columnas únicas.')
    if not isinstance(weights, Mapping) or not weights or set(weights) != set(scores.columns):
        raise IntegrationError('Los pesos internos deben coincidir con los indicadores.')
    checked = {}
    for key, value in weights.items():
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not math.isfinite(float(value)) or value < 0:
            raise IntegrationError('Peso interno no válido.')
        checked[key] = float(value)
    maximum = max(checked.values())
    if not maximum:
        raise IntegrationError('Activa al menos un indicador interno.')
    denominator = math.fsum(value/maximum for value in checked.values())
    normalized = {key:(value/maximum)/denominator for key,value in checked.items()}
    if any(checked[key] > 0 and normalized[key] == 0 for key in checked):
        raise IntegrationError('Escala de pesos internos no representable.')
    numeric = pd.DataFrame({key:_numeric_series(scores[key],key) for key in scores},index=scores.index)
    if ((numeric<0)|(numeric>100)).any().any():
        raise IntegrationError('Las puntuaciones internas deben estar entre 0 y 100.')
    active = [key for key in normalized if normalized[key]>0]
    complete = numeric[active].notna().all(axis=1)
    result = pd.DataFrame(index=scores.index)
    result['puntuacion'] = numeric[active].mul(pd.Series(normalized)).sum(axis=1).where(complete)
    result['estado'] = complete.map({True:'completo',False:'indicadores_faltantes'})
    result['indicadores_faltantes_json'] = [json.dumps([key for key in active if pd.isna(row[key])],ensure_ascii=False) for _,row in numeric.iterrows()]
    result.attrs['pesos_internos'] = normalized
    return result
