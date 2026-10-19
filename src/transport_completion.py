"""Reproduce the completed edition from separate GTFS and dated PDF evidence.

This verifies source integration, not the truth of the originating timetable.
The scoring formula lives in transport_offer_model and is unchanged here.
"""
from collections import defaultdict
from datetime import date
import json

from src.integration_common import IntegrationError


def pdf_contributions(evidence, dates, codes):
    sources = {s['url']: s['sha256'] for s in evidence['sources']}
    result = {code: {day: {'frequency': 0, 'destinations': set()} for day in dates} for code in codes}
    seen_ids = set()
    for trip in evidence['journeys']:
        identity, stamp = trip['id'], trip['date']
        if identity in seen_ids or stamp not in dates:
            raise IntegrationError('Expedición PDF repetida o fuera de la semana.')
        seen_ids.add(identity)
        if sources.get(trip['source_url']) != trip['source_sha256']:
            raise IntegrationError('Expedición PDF sin una fuente contrastada.')
        if 'school_days_only' in trip['restrictions'] and date.fromisoformat(stamp).weekday() >= 5:
            raise IntegrationError('Servicio lectivo asignado al fin de semana.')
        downstream, outward = set(), defaultdict(set)
        for visit in reversed(trip['visits']):
            code = visit['codigo_ine']
            if (not isinstance(code, str) or len(code) != 5 or not code.startswith('28')
                    or not code.isdigit() or type(visit['pickup']) is not bool or type(visit['dropoff']) is not bool):
                raise IntegrationError('Parada municipal PDF o permisos no válidos.')
            seconds = visit['departure_seconds']
            if seconds is not None and (type(seconds) is not int or seconds < 0):
                raise IntegrationError('Hora publicada PDF no válida.')
            if visit['pickup'] and code in result:
                outward[code].update(downstream - {code})
            if visit['dropoff']:
                downstream.add(code)
        for code, destinations in outward.items():
            if destinations:
                result[code][stamp]['frequency'] += 1
                result[code][stamp]['destinations'].update(destinations)
    return result


def verify_completion_evidence(folder, offer):
    if 'completion_audit' not in offer:
        return  # The immutable earlier transport edition has no such evidence.
    try:
        evidence = json.loads((folder / 'dated_pdf_journeys.json').read_text(encoding='utf-8'))
        gtfs = json.loads((folder / 'gtfs_component_daily.json').read_text(encoding='utf-8'))
        dates, codes = offer['dates'], sorted(offer['daily'])
        if gtfs['dates'] != dates or sorted(gtfs['daily']) != codes:
            raise IntegrationError('El componente GTFS no conserva fechas y municipios.')
        pdf = pdf_contributions(evidence, dates, codes)
        for code in codes:
            if set(gtfs['daily'][code]) != set(dates):
                raise IntegrationError('Faltan días del componente GTFS.')
            for day in dates:
                base = gtfs['daily'][code][day]
                actual = offer['daily'][code][day]
                expected_destinations = set(base['destinations']) | pdf[code][day]['destinations']
                if (actual['frequency'] != base['frequency'] + pdf[code][day]['frequency']
                        or set(actual['destinations']) != expected_destinations):
                    raise IntegrationError('El total diario no reproduce GTFS y PDF sin duplicarlos: ' + code)
        if len(evidence['journeys']) != offer['completion_audit']['dated_pdf_vehicles']:
            raise IntegrationError('El total de expediciones PDF no coincide con su evidencia.')
    except (KeyError, TypeError, OSError, ValueError) as exc:
        if isinstance(exc, IntegrationError):
            raise
        raise IntegrationError('Evidencia de ampliación de transporte incompleta.') from exc
