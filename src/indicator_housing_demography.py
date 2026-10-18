"""Fase 5: indicadores de vivienda y demografía con períodos explícitos.

No normaliza, pondera ni calcula un índice. No mezcla VC y VU, no estima
asequibilidad a partir de renta IRPF y no completa valores ausentes.
"""
from pathlib import Path
import json
import math

import pandas as pd

from src.integration_common import IntegrationError, make_issue

HOUSING_YEAR = 2024
POPULATION_YEAR = 2025
BASE_YEAR = 2020
MIGRATION_YEAR = 2024
RENT_SOURCE = "MIVAU_SERPAVI_2011_2024"
POP_SOURCE = "padron_por_sexo"
MIG_SOURCE = "migraciones_por_procedencia_destino_y_sexo"
INCOME_SOURCE = "irpf_indicador_renta"
INCOME_YEAR = 2023
INCOME_SEMANTIC_URL = "https://gestiona.comunidad.madrid/desvan/AccionDatosTemaMunicipal.icm?codTema=1930406"
MIGRATION_COMPONENTS = {
    "entradas_otras_ccaa": (1, "Desde otras Comunidades Autónomas a la Comunidad de Madrid"),
    "entradas_extranjero": (1, "Desde el extranjero con destino la Comunidad de Madrid"),
    "salidas_otras_ccaa": (-1, "Procedencia la Comunidad de Madrid con destino otras Comunidades Autónomas"),
    "salidas_extranjero": (-1, "Procedencia la Comunidad de Madrid con destino el extranjero"),
}
OBSERVATION_COLUMNS = ["codigo_ine", "indicador_id", "valor", "anio", "periodo", "estado",
                       "fuentes_json", "detalle_json", "nota"]
RENT_FIELDS = ("alquiler_m2_mes_mediana", "alquiler_m2_mes_p25", "alquiler_m2_mes_p75",
               "alquiler_mes_mediana", "alquiler_mes_p25", "alquiler_mes_p75", "testigos")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _number(value):
    if value is None or value == "" or pd.isna(value):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise IntegrationError(f"Valor numérico no interpretable: {value!r}")
    if not math.isfinite(result):
        raise IntegrationError(f"Valor no finito: {value!r}")
    return result


def _truth(value):
    return value is True or str(value).lower() == "true"


def _read(path, required):
    try:
        frame = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, ValueError) as exc:
        raise IntegrationError(f"No se pudo leer {path}") from exc
    missing = set(required) - set(frame.columns)
    if missing:
        raise IntegrationError(f"{Path(path).name}: faltan columnas {sorted(missing)}")
    return frame


def _eligible(master):
    required = {"codigo_ine", "municipio", "poblacion", "poblacion_anio", "elegible_hasta_50000"}
    if not required.issubset(master):
        raise IntegrationError("Maestro municipal incompleto para indicadores demográficos")
    if master["codigo_ine"].duplicated().any() or not master["codigo_ine"].astype(str).str.fullmatch(r"28[0-9]{3}").all():
        raise IntegrationError("Maestro con códigos INE no válidos o repetidos")
    if not master["elegible_hasta_50000"].astype(str).str.lower().isin(["true", "false"]).all():
        raise IntegrationError("Bandera de elegibilidad no booleana")
    eligible = master.loc[master["elegible_hasta_50000"].map(_truth)].copy()
    if not eligible["poblacion_anio"].map(_number).eq(POPULATION_YEAR).all():
        raise IntegrationError("La fase 5 requiere el maestro del padrón 2025")
    if eligible.empty:
        raise IntegrationError("El maestro no contiene municipios elegibles")
    return eligible.sort_values("codigo_ine")


def _definition(identifier, dimension, name, unit, direction, role, formula, sources, limitations, group=""):
    return {"indicador_id": identifier, "dimension": dimension, "nombre": name, "unidad": unit,
            "sentido": direction, "rol": role, "formula": formula, "fuentes_json": _json(sources),
            "limitaciones": limitations, "grupo_redundancia": group}


def _observation(code, identifier, value, year, sources, detail, note="", period=None):
    return {"codigo_ine": code, "indicador_id": identifier, "valor": value, "anio": year,
            "periodo": str(year) if period is None else period,
            "estado": "disponible" if value is not None else "sin_dato",
            "fuentes_json": _json(sources), "detalle_json": _json(detail), "nota": note}


def _trace_stat(row):
    if row is None:
        return {"estado": "sin_fila"}
    return {field: row.get(field, "") for field in
            ("fuente", "archivo_fuente", "id_observacion", "fila_fuente", "filas_fuente", "anio",
             "dimensiones_normalizadas", "valor_original", "unidad_interpretada", "estado_observacion")}


def _stat_lookup(frame, source, year, dimensions):
    selected = frame.loc[frame["fuente"].eq(source) & frame["anio"].eq(str(year))]
    wanted = {str(key): str(value) for key, value in dimensions.items()}
    mask = selected["dimensiones_normalizadas"].map(lambda raw: json.loads(raw) == wanted)
    selected = selected.loc[mask]
    if selected["codigo_ine"].duplicated().any():
        raise IntegrationError(f"Estadística duplicada o conflictiva: {source}/{year}/{dimensions}")
    return selected.set_index("codigo_ine").to_dict("index")


def _stat_value(row, *, count=False):
    if row is None or not _truth(row.get("dato_disponible")) or _truth(row.get("conflicto_observacion")):
        return None
    value = _number(row["valor"])
    if count and value is not None and row.get("unidad_interpretada") != "Número":
        raise IntegrationError("El recuento estadístico no declara la unidad Número")
    if count and value is not None and (value < 0 or not value.is_integer()):
        raise IntegrationError("El recuento estadístico debe ser entero no negativo")
    return value


def _rent_trace(row, field):
    return {"archivo_fuente": row["archivo_fuente"], "url_fuente": row["url_fuente"],
            "hoja_fuente": row["hoja_fuente"], "fila_fuente_excel": int(row["fila_fuente_excel"]),
            "campo_fuente": row["campo_fuente_" + field],
            "columna_fuente": row.get("columna_fuente_" + field, ""),
            "valor": _number(row[field])}


def prepare_housing_demography(master, integration_dir: Path):
    """Devuelve observaciones completas por elegible, definiciones y trazabilidad.

    Los años son comunes y fijos; nunca se sustituye una ausencia con otro año.
    Las migraciones calculadas abarcan movimientos que cruzan el límite de la
    Comunidad de Madrid. No incluyen el saldo entre municipios madrileños.
    """
    eligible = _eligible(master)
    codes = set(eligible["codigo_ine"])
    integration_dir = Path(integration_dir)
    rent_required = ["codigo_ine", "anio", "tipo", "dato_disponible", "archivo_fuente", "url_fuente",
                     "hoja_fuente", "fila_fuente_excel", *RENT_FIELDS,
                     *("campo_fuente_" + field for field in RENT_FIELDS)]
    rent = _read(integration_dir / "vivienda_2024.csv", rent_required)
    rent = rent.loc[rent["codigo_ine"].isin(codes) & rent["anio"].eq(str(HOUSING_YEAR))].copy()
    if rent.duplicated(["codigo_ine", "tipo"]).any():
        raise IntegrationError("SERPAVI 2024: clave municipio/tipología repetida")
    if not rent["tipo"].isin(["VC", "VU"]).all():
        raise IntegrationError("SERPAVI 2024: tipología no reconocida")
    rent_lookup = rent.set_index(["codigo_ine", "tipo"]).to_dict("index")
    stat_required = ["codigo_ine", "fuente", "anio", "dimensiones_normalizadas", "valor",
                     "dato_disponible", "conflicto_observacion", "unidad_interpretada"]
    stats = _read(integration_dir / "estadisticas_historico.csv", stat_required)
    stats = stats.loc[stats["codigo_ine"].isin(codes) & stats["fuente"].isin([POP_SOURCE, MIG_SOURCE, "1004050", INCOME_SOURCE])]
    try:
        for raw in stats["dimensiones_normalizadas"].unique():
            if not isinstance(json.loads(raw), dict):
                raise ValueError("Las dimensiones deben ser un objeto")
    except (ValueError, TypeError) as exc:
        raise IntegrationError("Dimensiones estadísticas no interpretables") from exc
    population = {year: _stat_lookup(stats, POP_SOURCE, year, {"Sexo": "Total"})
                  for year in [BASE_YEAR, MIGRATION_YEAR, POPULATION_YEAR]}
    purchase = _stat_lookup(stats, "1004050", POPULATION_YEAR, {})
    income = _stat_lookup(stats, INCOME_SOURCE, INCOME_YEAR, {"Medida": "Per cápita"})
    migration = {key: _stat_lookup(stats, MIG_SOURCE, MIGRATION_YEAR,
                                   {"Sexo": "Total", "Procedencia y destino": label})
                 for key, (_, label) in MIGRATION_COMPONENTS.items()}
    observations, definitions, issues, housing_rows, demo_rows = [], [], [], [], []
    rent_limitations = ("Alquiler habitual declarado en 2024; no son ofertas vigentes. "
                        "VC y VU se comparan por separado; no mide esfuerzo respecto a los ingresos.")
    for kind, label in [("VC", "colectiva"), ("VU", "unifamiliar o rural")]:
        for measure, unit, role, direction in [
            ("m2", "EUR/m²/mes", "candidato_ivp", "menor_mejor"),
            ("mes", "EUR/mes", "contexto", "contexto"),
        ]:
            field = "alquiler_m2_mes_mediana" if measure == "m2" else "alquiler_mes_mediana"
            identifier = f"vivienda_alquiler_{kind.lower()}_{measure}_mediana"
            definition = _definition(identifier, "vivienda", f"Alquiler mediano de vivienda {label}",
                                           unit, direction, role, f"SERPAVI 2024: {field}, tipología {kind}",
                                           [RENT_SOURCE], rent_limitations + (
                                               " El importe mensual depende de la superficie de las viviendas." if measure == "mes" else ""),
                                           "alquiler_tipologias_alternativas" if measure == "m2" else "alquiler_" + kind.lower())
            definition["condicion_uso_ivp"] = (
                "Elegir una tipología común al comparar; VC y VU son alternativas. "
                "No sumarlas ni sustituir una ausencia con la otra. Los datos ausentes no equivalen a coste cero."
                if measure == "m2" else "Solo contexto; no sumar junto al coste por m².")
            definitions.append(definition)
    definitions.extend([
        _definition("vivienda_valor_tasado_m2", "vivienda", "Valor tasado de vivienda libre", "EUR/m²",
                    "contexto", "contexto", "Valor publicado en 1004050 para 2025", ["1004050"],
                    "Cobertura municipal muy reducida. Tasación, no precio observado de compraventa ni oferta actual."),
        _definition("demografia_poblacion_2025", "demografia", "Población empadronada", "habitantes",
                    "contexto", "contexto", "Padrón a 1 de enero de 2025, Sexo=Total", [POP_SOURCE],
                    "Contexto de tamaño; una población mayor no implica mayor calidad de vida."),
        _definition("renta_disponible_per_capita_2023", "demografia", "Renta disponible bruta municipal per cápita", "EUR/persona/año",
                    "contexto", "contexto", "Medida=Per cápita del indicador municipal, año 2023", [INCOME_SOURCE],
                    "Estimación municipal por persona; no es la renta de un hogar ni salario. "
                    "No se combina con alquiler 2024 para estimar esfuerzo de un hogar. " + INCOME_SEMANTIC_URL),
        _definition("demografia_crecimiento_2020_2025", "demografia", "Cambio de población 2020–2025", "%",
                    "contexto", "contexto", "100 × (población2025 / población2020 − 1)", [POP_SOURCE],
                    "Cambio acumulado entre fechas padronales; no es saldo migratorio ni crecimiento anual. "
                    "El signo positivo o negativo no representa por sí solo mejor o peor calidad de vida."),
        _definition("demografia_saldo_migratorio_externo_cm", "demografia",
                    "Saldo de migraciones que cruzan el límite de la Comunidad de Madrid", "personas",
                    "contexto", "contexto", "entradas desde otras CCAA y extranjero − salidas a otras CCAA y extranjero, 2024",
                    [MIG_SOURCE], "Excluye los movimientos entre municipios de la Comunidad de Madrid; no es el saldo municipal total.",
                    "saldo_migratorio_externo_cm"),
        _definition("demografia_saldo_migratorio_externo_cm_por1000", "demografia",
                    "Saldo migratorio externo a la CM por 1.000 habitantes", "personas/1.000 habitantes",
                    "contexto", "contexto", "1000 × saldo externo2024 / población a 1 de enero de 2024",
                    [MIG_SOURCE, POP_SOURCE], "Tasa con población al inicio de 2024, no población media anual. "
                    "Excluye movimientos entre municipios madrileños; no equivale a variación padronal.", "saldo_migratorio_externo_cm"),
    ])
    for municipal in eligible.to_dict("records"):
        code = municipal["codigo_ine"]
        for kind in ["VC", "VU"]:
            row = rent_lookup.get((code, kind))
            quality = {"codigo_ine": code, "anio": HOUSING_YEAR, "tipo": kind}
            if row is not None:
                parsed = {field: _number(row[field]) for field in RENT_FIELDS}
                for prefix in ["alquiler_m2_mes", "alquiler_mes"]:
                    values = [parsed[prefix + suffix] for suffix in ["_p25", "_mediana", "_p75"]]
                    if any(v is not None for v in values) and (any(v is None for v in values) or not 0 < values[0] <= values[1] <= values[2]):
                        raise IntegrationError(f"SERPAVI: percentiles incompletos, no positivos o desordenados: {code}/{kind}")
                witnesses = parsed["testigos"]
                if witnesses is not None and (witnesses < 0 or not witnesses.is_integer()):
                    raise IntegrationError(f"SERPAVI: testigos no enteros no negativos: {code}/{kind}")
                available = _truth(row["dato_disponible"])
                medians_present = [parsed[field] is not None for field in ["alquiler_mes_mediana", "alquiler_m2_mes_mediana"]]
                if any(medians_present) != all(medians_present):
                    raise IntegrationError(f"SERPAVI: mediana mensual y por m² publicadas de forma incompleta: {code}/{kind}")
                has_median = all(medians_present)
                if available != has_median or (has_median and (witnesses is None or witnesses <= 0)):
                    raise IntegrationError(f"SERPAVI: disponibilidad incoherente con precio/testigos: {code}/{kind}")
                traces = {field: _rent_trace(row, field) for field in RENT_FIELDS}
                quality.update(parsed)
                quality.update({"estado": "disponible" if available else "sin_dato", "trazabilidad_json": _json(traces)})
            else:
                parsed = dict.fromkeys(RENT_FIELDS)
                traces, available = {"estado": "sin_fila"}, False
                quality.update(parsed)
                quality.update({"estado": "sin_dato", "trazabilidad_json": _json(traces)})
            housing_rows.append(quality)
            for measure, field in [("m2", "alquiler_m2_mes_mediana"), ("mes", "alquiler_mes_mediana")]:
                value = parsed[field] if available else None
                observations.append(_observation(code, f"vivienda_alquiler_{kind.lower()}_{measure}_mediana", value,
                                                 HOUSING_YEAR, [RENT_SOURCE], {"tipologia": kind, "celdas_fuente": traces},
                                                 "Sin estadística publicada; no implica ausencia de alquiler." if value is None else rent_limitations))
        p2025 = _number(municipal["poblacion"])
        if p2025 is None or not 0 <= p2025 <= 50000 or not p2025.is_integer():
            raise IntegrationError("Población elegible del maestro no entera o fuera de 0–50.000")
        official2025 = _stat_value(population[POPULATION_YEAR].get(code), count=True)
        if official2025 is not None and official2025 != p2025:
            raise IntegrationError("El padrón integrado de 2025 no coincide con el maestro")
        p2020_row, p2024_row = population[BASE_YEAR].get(code), population[MIGRATION_YEAR].get(code)
        p2020, p2024 = _stat_value(p2020_row, count=True), _stat_value(p2024_row, count=True)
        growth = (p2025 / p2020 - 1) * 100 if p2020 is not None and p2020 > 0 else None
        observations.append(_observation(code, "demografia_poblacion_2025", p2025, POPULATION_YEAR, [POP_SOURCE],
                                         {"origen": "maestro_municipal_fase2", "fecha_referencia": "2025-01-01",
                                          "fila_integrada": _trace_stat(population[POPULATION_YEAR].get(code))}))
        observations.append(_observation(code, "demografia_crecimiento_2020_2025", growth, POPULATION_YEAR, [POP_SOURCE],
                                         {"poblacion_2020": p2020, "poblacion_2025": p2025,
                                          "fila_2020": _trace_stat(p2020_row),
                                          "fila_2025": _trace_stat(population[POPULATION_YEAR].get(code))},
                                         "No se sustituye el año inicial cuando falta o es cero.", period="2020-2025"))
        purchase_row = purchase.get(code)
        purchase_value = _stat_value(purchase_row)
        if purchase_value is not None and (purchase_value <= 0 or purchase_row["unidad_interpretada"] != "Euros/m2"):
            raise IntegrationError("Valor tasado publicado no positivo o con unidad incorrecta")
        observations.append(_observation(code, "vivienda_valor_tasado_m2", purchase_value, POPULATION_YEAR, ["1004050"],
                                         _trace_stat(purchase_row), "Valor tasado; no oferta ni transacción de compraventa."))
        income_row = income.get(code)
        income_value = _stat_value(income_row)
        if income_value is not None and (income_value < 0 or income_row["unidad_interpretada"] != "Euros"):
            raise IntegrationError("Renta per cápita: valor o unidad no válidos")
        observations.append(_observation(code, "renta_disponible_per_capita_2023", income_value, INCOME_YEAR,
                                         [INCOME_SOURCE], {"fila_fuente": _trace_stat(income_row),
                                                           "verificacion_semantica": INCOME_SEMANTIC_URL},
                                         "Renta disponible bruta municipal por persona, no renta de un hogar."))
        components = {}
        for key, (sign, label) in MIGRATION_COMPONENTS.items():
            component_row = migration[key].get(code)
            components[key] = {"valor": _stat_value(component_row, count=True), "signo": sign,
                               "direccion": label, "fila": _trace_stat(component_row)}
        complete = all(component["valor"] is not None for component in components.values())
        balance = sum(component["valor"] * component["signo"] for component in components.values()) if complete else None
        rate = balance / p2024 * 1000 if balance is not None and p2024 is not None and p2024 > 0 else None
        detail = {"componentes": components, "poblacion_2024": p2024, "fila_padron_2024": _trace_stat(p2024_row),
                  "excluye": "Movimientos entre municipios de la Comunidad de Madrid"}
        note = "Saldo parcial: movimientos que cruzan el límite de la CM; no es el saldo migratorio municipal total."
        observations.append(_observation(code, "demografia_saldo_migratorio_externo_cm", balance, MIGRATION_YEAR,
                                         [MIG_SOURCE], detail, note))
        observations.append(_observation(code, "demografia_saldo_migratorio_externo_cm_por1000", rate, MIGRATION_YEAR,
                                         [MIG_SOURCE, POP_SOURCE], detail, note + " Denominador: padrón al inicio de 2024."))
        demo_rows.append({"codigo_ine": code, "poblacion_2020": p2020, "poblacion_2024": p2024,
                          "poblacion_2025": p2025, "crecimiento_2020_2025_pct": growth,
                          "renta_disponible_per_capita_2023": income_value,
                          "saldo_migratorio_externo_cm_2024": balance,
                          "saldo_migratorio_externo_cm_2024_por1000": rate,
                          "componentes_migratorios_json": _json(components)})
    for observation in observations:
        if observation["valor"] is None:
            issues.append(make_issue("indicadores_vivienda_demografia", "indicador_sin_dato",
                                     registro=observation["codigo_ine"], campo=observation["indicador_id"],
                                     detalle="No se imputa, no se cambia de año y no se convierte en cero."))
    observations.sort(key=lambda item: (item["codigo_ine"], item["indicador_id"]))
    coverage = {definition["indicador_id"]: sum(o["valor"] is not None for o in observations
                                               if o["indicador_id"] == definition["indicador_id"])
                for definition in definitions}
    return {"observations": observations, "definitions": definitions,
            "tables": {"vivienda_trazabilidad.csv": pd.DataFrame(housing_rows),
                       "demografia_componentes.csv": pd.DataFrame(demo_rows)}, "issues": issues,
            "summary": {"municipios_elegibles": len(eligible), "indicadores": len(definitions),
                        "observaciones": len(observations), "cobertura": coverage,
                        "anios": {"alquiler": HOUSING_YEAR, "padron": POPULATION_YEAR,
                                  "base_crecimiento": BASE_YEAR, "migraciones": MIGRATION_YEAR, "renta_per_capita": INCOME_YEAR},
                        "saldo_migratorio": "Externo a la CM; no es el saldo municipal total",
                        "sin_ratios_alquiler_irpf": True, "sin_mezcla_tipologias": True}}
