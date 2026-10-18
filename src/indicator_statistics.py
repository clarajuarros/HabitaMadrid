"""Indicadores contextuales seleccionados de las estadísticas publicadas."""
from pathlib import Path
import json
import math

import pandas as pd

from src.integration_common import IntegrationError, make_issue

EDUCATION_SOURCE = "centros_no_universitarios_general_por_tipo_y_ensenanza"
TRANSPORT_SOURCE = "infraestructura_transporte_publico"
HEALTH_SOURCE = "centros_sanitarios_por_tipo"
# Selección explícita: las categorías no se suman entre sí.
SPECS = [
    ("educacion_centros_regimen_general_2024", "educacion", "Centros de régimen general publicados", EDUCATION_SOURCE, 2024,
     {"Tipo": "Total", "Enseñanza": "Total"}, "Número", "centros", "educacion_estadistica"),
    ("educacion_centros_primaria_2024", "educacion", "Centros de primaria publicados", EDUCATION_SOURCE, 2024,
     {"Tipo": "Total", "Enseñanza": "Educación Primaria"}, "Número", "centros", "educacion_estadistica"),
    ("educacion_centros_eso_2024", "educacion", "Centros de ESO publicados", EDUCATION_SOURCE, 2024,
     {"Tipo": "Total", "Enseñanza": "Educación Secundaria Obligatoria (ESO)"}, "Número", "centros", "educacion_estadistica"),
    ("educacion_centros_regimen_general_por10000_2024", "educacion", "Centros de régimen general por 10.000 habitantes publicados", "1904060", 2024,
     {}, "Centros por 10.000 habitantes", "centros/10.000 habitantes", "educacion_estadistica"),
    ("transporte_paradas_estadistica_2023", "transporte", "Paradas de autobús publicadas en 2023", TRANSPORT_SOURCE, 2023,
     {"Medida": "Paradas de autobús"}, "Número", "paradas", "transporte_estadistica"),
    ("transporte_lineas_interurbanas_2023", "transporte", "Líneas interurbanas publicadas en 2023", TRANSPORT_SOURCE, 2023,
     {"Medida": "Líneas interurbanas"}, "Número", "líneas", "transporte_estadistica"),
    ("transporte_estaciones_cercanias_2023", "transporte", "Estaciones de Cercanías publicadas en 2023", TRANSPORT_SOURCE, 2023,
     {"Medida": "Estaciones de cercanías de tren"}, "Número", "estaciones", "transporte_estadistica"),
    ("transporte_estaciones_metro_2023", "transporte", "Estaciones de Metro publicadas en 2023", TRANSPORT_SOURCE, 2023,
     {"Medida": "Estaciones de metro"}, "Número", "estaciones", "transporte_estadistica"),
    ("transporte_estaciones_metro_ligero_2023", "transporte", "Estaciones de Metro Ligero publicadas en 2023", TRANSPORT_SOURCE, 2023,
     {"Medida": "Estaciones de metro ligero"}, "Número", "estaciones", "transporte_estadistica"),
    ("sanidad_atencion_primaria_estadistica_2024", "sanidad", "Centros de salud y consultorios locales publicados en 2024", HEALTH_SOURCE, 2024,
     {"Medida": "Total", "Tipo": "Consultorios locales y centros de salud"}, "Número", "centros", "sanidad_estadistica"),
]


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def prepare_statistical_indicators(master, integration_dir):
    path = Path(integration_dir) / "estadisticas_ultimo_anio.csv"
    data = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = {"codigo_ine", "fuente", "anio", "dimensiones_normalizadas", "valor",
                "dato_disponible", "unidad_interpretada", "conflicto_observacion"}
    if not required.issubset(data):
        raise IntegrationError("Faltan campos en las estadísticas de último año.")
    codes = master.loc[master.elegible_hasta_50000, "codigo_ine"].sort_values().tolist()
    observations, definitions, issues = [], [], []
    for identifier, dimension, title, source, year, dimensions, source_unit, unit, group in SPECS:
        source_rows = data.loc[data.fuente.eq(source)]
        if source_rows.empty or set(source_rows.anio) != {str(year)}:
            raise IntegrationError(f"{source}: el año global no coincide con el contrato {year}.")
        selected = source_rows.loc[source_rows.dimensiones_normalizadas.map(json.loads).map(lambda d: d == dimensions)]
        if selected.codigo_ine.duplicated().any():
            raise IntegrationError("Observación estadística duplicada o contradictoria: " + identifier)
        if selected.empty:
            raise IntegrationError("No existe la selección de dimensiones esperada: " + identifier)
        lookup = selected.set_index("codigo_ine")
        limit = ("Estadística de año fijo; no acredita instalaciones, oferta ni horarios actuales. "
                 "Los centros por enseñanza no se suman: una instalación puede aparecer en varias. "
                 "Tasas y recuentos se conservan separados; cero significa el cero publicado para esta selección.")
        if dimension == "transporte":
            limit += " Las estaciones de distintos sistemas no se suman como instalaciones físicas únicas."
        definitions.append({"indicador_id": identifier, "dimension": dimension, "nombre": title,
                            "unidad": unit, "sentido": "contexto", "rol": "contexto",
                            "formula": "Valor publicado, sin recálculo, con dimensiones exactas " + _json(dimensions),
                            "fuentes_json": _json([source]), "limitaciones": limit,
                            "grupo_redundancia": group, "condicion_uso_ivp": "Contexto; sin selección para el IVP."})
        for code in codes:
            value, state = None, "sin_fila"
            detail = {"fuente": source, "anio": year, "dimensiones": dimensions}
            if code in lookup.index:
                row = lookup.loc[code]
                detail.update({key: row.get(key, "") for key in ["id_observacion", "archivo_fuente", "fila_fuente",
                               "filas_fuente", "valor_original", "estado_dato", "estado_observacion", "unidad_interpretada"]})
                state = "sin_dato"
                if row.dato_disponible == "True" and row.conflicto_observacion == "False":
                    if row.unidad_interpretada != source_unit:
                        raise IntegrationError("Unidad inesperada para " + identifier)
                    try:
                        value = float(row.valor)
                    except ValueError as exc:
                        raise IntegrationError("Valor estadístico no numérico para " + identifier) from exc
                    if not math.isfinite(value) or value < 0 or (source_unit == "Número" and not value.is_integer()):
                        raise IntegrationError("Recuento o tasa inválido para " + identifier)
                    state = "disponible"
            if state != "disponible":
                issues.append(make_issue(source, "indicador_estadistico_" + state, code, identifier,
                                         detalle="El valor permanece ausente; no se completa con cero ni otro año."))
            observations.append({"codigo_ine": code, "indicador_id": identifier, "valor": value,
                                 "anio": year, "periodo": str(year), "estado": state,
                                 "fuentes_json": _json([source]), "detalle_json": _json(detail), "nota": limit})
    return {"observations": observations, "definitions": definitions, "issues": issues, "tables": {},
            "summary": {"indicadores": len(definitions), "observaciones": len(observations),
                        "alcance": "Selecciones contextuales explícitas; no se suman dimensiones ni se puntúan."}}


def cross_health_coverage(observations, geography_dir):
    """Contraste entre fechas y universos; alerta sin afirmar equivalencia de censos."""
    identifier = "sanidad_atencion_primaria_estadistica_2024"
    published = observations.loc[observations.indicador_id.eq(identifier)].set_index("codigo_ine")
    services = pd.read_csv(Path(geography_dir) / "catalogo_servicios_geograficos.csv", dtype=str,
                           encoding="utf-8-sig", keep_default_na=False)
    sources = pd.read_csv(Path(geography_dir) / "fuentes_geograficas.csv", dtype=str,
                          encoding="utf-8-sig", keep_default_na=False)
    dates = sources.loc[sources.fuente.eq("idem_centros_salud"), "fecha_descarga_utc"].tolist()
    period = "consulta_" + dates[0][:10] if dates else "consulta_sin_fecha"
    counted = services.loc[services.categoria.isin(["centro_salud", "consultorio"])].groupby("codigo_ine").size()
    rows, issues = [], []
    for code, row in published.iterrows():
        observed = int(counted.get(code, 0))
        available = row.estado == "disponible" and pd.notna(row.valor)
        alert = bool(available and row.valor > observed)
        status = "indicio_cobertura_no_equivalente" if alert else "sin_indicio_por_este_contraste" if available else "sin_estadistica_para_contrastar"
        rows.append({"codigo_ine": code, "centros_consultorios_publicados_2024": row.valor,
                     "puntos_primaria_catalogo_geografico": observed, "periodo_geografico": period,
                     "estado_contraste": status, "revision_ivp_requerida": alert or not available,
                     "sin_punto_local_con_estadistica_positiva": bool(available and row.valor > 0 and observed == 0),
                     "interpretacion": "Fuentes y fechas diferentes; indica posible cobertura no equivalente, no prueba cierres ni ausencia real de atención. No se imputan coordenadas."})
        if alert:
            issues.append(make_issue("contraste_atencion_primaria", status, code, "distancia_atencion_primaria_m",
                                     detalle="La estadística 2024 registra más centros que los puntos locales del catálogo geográfico. Revisar antes de usar la distancia para puntuar."))
    return pd.DataFrame(rows), issues
