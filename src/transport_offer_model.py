"""Explicit transport revision, retaining the IVP 1.0 source snapshot unchanged.

The revision measures published scheduled intermunicipal departures and direct
destinations. It cannot certify realtime operation, capacity or all network
coverage. Known coverage gaps prevent a transport score rather than becoming
zero or silently falling back to the distance to a bus stop.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from src.integration_common import IntegrationError
from src.ivp_calculation import fit_normalization, transform_normalization

VERSION = 'ivp-2.0.0-transporte-programado'
INDICATOR = 'oferta_conectividad_programada'
METHODS = ('minmax', 'winsor_p05_p95', 'percentil')


def score_transport_offer(offer, codes, coverage_gaps):
    """Return 155 raw component rows and fixed references for the revision.

    ``coverage_gaps`` maps municipality codes to unresolved evidence. The caller
    may clear an item only after source reconciliation, never merely to enlarge
    the ranking. Every date must be covered by at least one in-range source.
    The 50/50 component weights are an explicit model choice, not an empirically
    established equivalence between an extra trip and an extra destination.
    """
    if len(codes) != len(set(codes)) or set(offer['municipalities']) != set(codes):
        raise IntegrationError('El resumen de transporte no conserva el universo municipal.')
    dates = sorted(offer['dates'])
    if len(dates) != 7 or len(set(dates)) != 7:
        raise IntegrationError('La edición necesita siete fechas de servicio distintas.')
    rows = []
    for code in sorted(codes):
        summary = offer['municipalities'][code]
        gaps = coverage_gaps.get(code, [])
        usable = (summary['has_scheduled_data'] and summary['covered_reference_days'] == 7
                  and not gaps)
        f, c = summary['frequency'], summary['connectivity']
        if (isinstance(f, bool) or isinstance(c, bool) or not np.isfinite([f, c]).all()
                or f < 0 or c < 0 or c > 178):
            raise IntegrationError('Componentes de oferta no válidos para ' + code)
        row = {
            'codigo_ine': code,
            'expediciones_dia': float(f), 'destinos_directos_dia': float(c),
            'expediciones_laborable': summary['weekdays']['frequency'],
            'expediciones_sabado': summary['saturday']['frequency'],
            'expediciones_domingo': summary['sunday']['frequency'],
            'destinos_laborable': summary['weekdays']['connectivity'],
            'destinos_sabado': summary['saturday']['connectivity'],
            'destinos_domingo': summary['sunday']['connectivity'],
            'inicio_semana': dates[0], 'fin_semana': dates[-1],
            'estado_cobertura': 'publicado_con_contraste_de_lineas' if usable else 'cobertura_no_verificada',
            'nota_cobertura': ('Oferta publicada de los modos incluidos. El contraste de líneas no certifica exhaustividad ni servicio real.'
                              if usable else '; '.join(str(g) for g in gaps) or 'No hay horarios publicados y vigentes suficientes para puntuar.'),
            'fuentes_json': json.dumps(summary['cobertura_presentes'], ensure_ascii=False),
            'puntuable': usable,
        }
        rows.append(row)
    frame = pd.DataFrame(rows).set_index('codigo_ine')
    references, scores = {}, {}
    for method in METHODS:
        refs, normalized = {}, {}
        for field, label in [('expediciones_dia', 'frecuencia'),
                             ('destinos_directos_dia', 'conectividad')]:
            values = frame[field].where(frame.puntuable).astype(float)
            ref = fit_normalization(values, direction='mayor_mejor', method=method)
            ref.update({'componente': label, 'universo_elegible': len(codes), 'anclas_fijas': True,
                        'semana_inicio': dates[0], 'semana_fin': dates[-1]})
            refs[label] = ref
            normalized[label] = transform_normalization(values, ref)
        scores[method] = pd.DataFrame(normalized)
        scores[method]['transporte'] = (scores[method].frecuencia + scores[method].conectividad) / 2
        references[method] = refs
    frame['puntuacion_frecuencia'] = scores['minmax'].frecuencia
    frame['puntuacion_conectividad'] = scores['minmax'].conectividad
    frame['puntuacion_transporte'] = scores['minmax'].transporte
    return frame.reset_index(), scores, references
