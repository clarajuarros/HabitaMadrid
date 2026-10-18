"""Series municipales ALMUDENA con claves y semántica conservadas.

No suma categorías, no completa años por municipio y no sustituye ausencias por
cero. Las filas municipales no vinculadas también permanecen en el histórico.
"""
from pathlib import Path
import json
import re

import pandas as pd

from src.integration_common import (
    IntegrationError, attach_municipal_context, make_issue, read_csv_strings,
)
from src.municipalities import normalized_name


STATISTICAL_SOURCES = (
    "padron_por_sexo", "migraciones_por_procedencia_destino_y_sexo",
    "centros_sanitarios_por_tipo",
    "centros_no_universitarios_general_por_tipo_y_ensenanza",
    "1904060", "infraestructura_transporte_publico", "irpf_indicador_renta",
    "centros_servicios_sociales_por_tipo", "701042", "1004050",
    "alquiler_medio_mensual_viviendas_arrendadas_valor_catastral_municipios_mas_20000",
    "alquiler_metro_cuadrado_mensual_viviendas_arrendadas_valor_catastral_municipios_mas_20000",
)
COMMON_COLUMNS = {
    "Año", "Tipo territorio", "Código territorio", "Territorio", "Valor",
    "Unidad", "Estado dato", "fila_fuente",
}
REQUIRED_COLUMNS = COMMON_COLUMNS - {"fila_fuente"}
MISSING_MARKERS = {"", "-", "..", "...", "N/D", "ND"}
DIMENSION_ALIASES = {
    "Enseñanza": {
        "Enseñanza Secundaria Obligatoria (ESO)":
            "Educación Secundaria Obligatoria (ESO)",
    },
}
NUMBER_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)")
HISTORY_COLUMNS = [
    "fuente", "archivo_fuente", "fila_fuente", "codigo_ine",
    "codigo_territorio_original", "municipio_original", "municipio",
    "elegible_hasta_50000", "vinculacion_municipal", "anio", "anio_original",
    "dimensiones_originales", "dimensiones_normalizadas", "valor_original",
    "valor_parseado", "valor", "unidad", "unidad_interpretada",
    "nota_unidad", "estado_dato", "dato_disponible", "estado_observacion",
    "duplicado_exacto", "duplicado_exacto_n", "filas_fuente",
    "conflicto_observacion", "id_observacion",
]
COVERAGE_COLUMNS = ["fuente", "anio", "dimensiones_normalizadas", "unidad",
                    "municipios_elegibles", "con_fila", "con_valor",
                    "con_cero_observado", "con_fila_sin_valor", "sin_fila"]


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _dimensions(frame, fields, normalize=False):
    """Cachea combinaciones repetidas, sin perder la etiqueta original."""
    if not fields:
        return pd.Series("{}", index=frame.index, dtype="object")
    cache = {}
    result = []
    for values in frame[fields].itertuples(index=False, name=None):
        if values not in cache:
            dims = dict(zip(fields, values))
            if normalize:
                dims = {key: DIMENSION_ALIASES.get(key, {}).get(value, value)
                        for key, value in dims.items()}
            cache[values] = _json(dims)
        result.append(cache[values])
    return pd.Series(result, index=frame.index)


def _interpreted_unit(source, units, dimensions):
    """Aclara unidades mal rotuladas por la fuente sin cambiar su valor."""
    result, notes = units.copy(), pd.Series("", index=units.index)
    if source == "1904060":
        result[:] = "Centros por 10.000 habitantes"
        notes[:] = "La columna Unidad declara Número; el título de la fuente especifica por 10.000 habitantes."
    elif source.startswith("alquiler_metro_cuadrado_mensual_"):
        result[:] = "Euros/m²/mes"
        notes[:] = "La columna Unidad declara Euros; el título especifica alquiler mensual por metro cuadrado."
    elif source.startswith("alquiler_medio_mensual_"):
        result[:] = "Euros/mes"
        notes[:] = "Periodicidad mensual indicada en el título de la fuente."
    elif source == "centros_sanitarios_por_tipo":
        rates = dimensions.map(lambda value: json.loads(value).get("Medida") == "Por 10.000 habitantes")
        result.loc[rates] = "Centros por 10.000 habitantes"
        notes.loc[rates] = "Tasa identificada por la dimensión Medida; no sumar con Total."
    return result, notes


def _population_evolution(history):
    """Compara años registrados consecutivos sin saltar filas con dato ausente."""
    selected = history.loc[
        history["fuente"].eq("padron_por_sexo") &
        history["dimensiones_normalizadas"].eq(_json({"Sexo": "Total"})) &
        history["codigo_ine"].ne("")
    ].copy()
    columns = ["codigo_ine", "municipio", "elegible_hasta_50000", "anio",
               "poblacion", "anio_anterior_registrado", "intervalo_anios",
               "poblacion_anterior", "variacion_absoluta", "variacion_porcentaje",
               "comparacion_disponible", "fuente", "fila_fuente", "filas_fuente"]
    if selected.empty:
        return pd.DataFrame(columns=columns)
    # Un año conflictivo conserva su posición con población desconocida. No se
    # salta para comparar con un año anterior que sí tenga valor.
    selected["clave_repetida"] = selected.duplicated(["codigo_ine", "anio"], keep=False)
    selected.loc[selected["clave_repetida"], "valor"] = float("nan")
    for _, group in selected.loc[selected["clave_repetida"]].groupby(["codigo_ine", "anio"], sort=False, dropna=False):
        all_rows = sorted({int(row) for values in group["filas_fuente"] for row in json.loads(values)})
        selected.loc[group.index, "filas_fuente"] = _json(all_rows)
    selected = selected.sort_values(["codigo_ine", "anio", "fila_fuente"])
    # Aquí el valor de todas las repeticiones ya es nulo y el contexto oficial
    # es idéntico; se colapsa la fila anual conservando todas sus procedencias.
    selected = selected.drop_duplicates(["codigo_ine", "anio"]).copy()
    groups = selected.groupby("codigo_ine", sort=False)
    selected["poblacion"] = selected["valor"]
    selected["anio_anterior_registrado"] = groups["anio"].shift().astype("Int64")
    selected["poblacion_anterior"] = groups["valor"].shift()
    selected["intervalo_anios"] = selected["anio"] - selected["anio_anterior_registrado"]
    comparable = (selected["poblacion"].notna() & selected["poblacion_anterior"].notna())
    selected["variacion_absoluta"] = (selected["poblacion"] - selected["poblacion_anterior"]).where(comparable)
    denominator = selected["poblacion_anterior"].where(selected["poblacion_anterior"].ne(0))
    selected["variacion_porcentaje"] = (selected["variacion_absoluta"] / denominator * 100).where(comparable)
    selected["comparacion_disponible"] = comparable
    return selected[columns].reset_index(drop=True)


def prepare_statistics(master: pd.DataFrame, raw_dir: Path):
    """Devuelve tablas, incidencias y resumen sin escribir ni modificar fuentes.

    La selección se limita a STATISTICAL_SOURCES; 1903452 (agregado nacional)
    nunca se incorpora. Fuente ausente o esquema roto provoca IntegrationError.
    Los duplicados exactos se agrupan con sus filas de procedencia; los
    conflictos permanecen separados y no son utilizables.
    """
    required_master = {"codigo_ine", "codigo_almudena", "municipio", "elegible_hasta_50000"}
    if not required_master.issubset(master.columns):
        raise IntegrationError("El maestro no contiene las claves y el contexto municipal necesarios.")
    if master["codigo_almudena"].duplicated().any() or master["codigo_ine"].duplicated().any():
        raise IntegrationError("El maestro debe tener claves ALMUDENA e INE únicas.")
    codes = master.set_index("codigo_almudena")["codigo_ine"].to_dict()
    names = master.set_index("codigo_ine")["municipio"].map(normalized_name).to_dict()
    eligibility = master["elegible_hasta_50000"]
    eligible_count = int(eligibility.eq(True).sum())
    issues, histories, coverage, source_summary = [], [], [], {}
    for source in STATISTICAL_SOURCES:
        path = Path(raw_dir) / (source + ".csv")
        if not path.is_file():
            raise IntegrationError("Falta la fuente estadística requerida: " + str(path))
        original = read_csv_strings(path)
        missing_columns = REQUIRED_COLUMNS - set(original.columns)
        if missing_columns:
            raise IntegrationError(source + ": faltan columnas " + ", ".join(sorted(missing_columns)))
        fields = [column for column in original if column not in COMMON_COLUMNS]
        original["dimensiones_originales"] = _dimensions(original, fields)
        original["dimensiones_normalizadas"] = _dimensions(original, fields, normalize=True)
        valid_year = original["Año"].str.fullmatch(r"[0-9]{4}")
        for _, row in original.loc[~valid_year].iterrows():
            issues.append(make_issue(source, "anio_invalido", campo="Año",
                                     valor_original=row["Año"], fila_fuente=row["fila_fuente"],
                                     detalle="Fila conservada; no participa en la selección de último año."))
        latest_year = int(original.loc[valid_year, "Año"].astype(int).max()) if valid_year.any() else None
        municipal = original.loc[original["Tipo territorio"].eq("Municipios")].copy().reset_index(drop=True)
        municipal["codigo_territorio_original"] = municipal["Código territorio"]
        municipal["codigo_ine"] = municipal["Código territorio"].map(codes).fillna("")
        municipal["municipio_original"] = municipal["Territorio"]
        for _, row in municipal.loc[municipal["codigo_ine"].eq("")].iterrows():
            issues.append(make_issue(source, "codigo_municipal_desconocido", registro=row["Código territorio"],
                                     campo="Código territorio", valor_original=row["Código territorio"],
                                     fila_fuente=row["fila_fuente"],
                                     detalle="Municipio sin correspondencia exacta con ALMUDENA; fila conservada sin INE."))
        mapped = municipal["codigo_ine"].ne("")
        name_match = municipal["Territorio"].map(normalized_name).eq(municipal["codigo_ine"].map(names))
        for _, row in municipal.loc[mapped & ~name_match].iterrows():
            issues.append(make_issue(source, "nombre_no_coincide", registro=row["codigo_ine"],
                                     campo="Territorio", valor_original=row["Territorio"],
                                     fila_fuente=row["fila_fuente"],
                                     detalle="El código vincula, pero el nombre no pasa el control limitado; revisar."))
        municipal = attach_municipal_context(municipal, master)
        municipal["vinculacion_municipal"] = municipal["codigo_ine"].ne("")
        municipal["anio_original"] = municipal["Año"]
        municipal["anio"] = pd.to_numeric(municipal["Año"].where(municipal["Año"].str.fullmatch(r"[0-9]{4}")), errors="coerce").astype("Int64")
        text = municipal["Valor"].str.strip()
        missing = text.isin(MISSING_MARKERS)
        valid_number = text.map(lambda value: NUMBER_PATTERN.fullmatch(value) is not None)
        municipal["valor_parseado"] = pd.to_numeric(text.where(valid_number), errors="coerce")
        invalid = ~missing & (~valid_number | municipal["valor_parseado"].isna())
        for _, row in municipal.loc[invalid].iterrows():
            issues.append(make_issue(source, "valor_no_numerico", registro=row["codigo_ine"],
                                     campo="Valor", valor_original=row["Valor"], fila_fuente=row["fila_fuente"],
                                     detalle="Valor no reconocido; se conserva el original y no se convierte en cero."))
        municipal["valor_original"] = municipal["Valor"]
        municipal["unidad"] = municipal["Unidad"]
        municipal["unidad_interpretada"], municipal["nota_unidad"] = _interpreted_unit(
            source, municipal["Unidad"], municipal["dimensiones_normalizadas"])
        municipal["estado_dato"] = municipal["Estado dato"]
        exact_columns = [column for column in original.columns if column not in {"fila_fuente", "dimensiones_normalizadas", "dimensiones_originales"}]
        municipal["duplicado_exacto"] = municipal.duplicated(exact_columns, keep=False)
        keys = ["codigo_territorio_original", "anio_original", "dimensiones_normalizadas", "unidad"]
        repeated = municipal.duplicated(keys, keep=False)
        signatures = municipal[exact_columns].apply(lambda row: _json(row.to_dict()), axis=1) if len(municipal) else pd.Series(dtype="object")
        variants = signatures.groupby([municipal[key] for key in keys], dropna=False).transform("nunique")
        municipal["conflicto_observacion"] = repeated & variants.gt(1)
        for kind, mask in [("duplicado_exacto", municipal["duplicado_exacto"]),
                           ("conflicto_observacion", municipal["conflicto_observacion"])]:
            for _, row in municipal.loc[mask].iterrows():
                issues.append(make_issue(source, kind, registro=row["codigo_ine"],
                                         fila_fuente=row["fila_fuente"],
                                         valor_original=row["Valor"],
                                         detalle="Se conservan todas las filas; no se selecciona el primer valor."))
        municipal["dato_disponible"] = (municipal["valor_parseado"].notna() &
                                            ~municipal["conflicto_observacion"] & municipal["anio"].notna())
        municipal["valor"] = municipal["valor_parseado"].where(municipal["dato_disponible"])
        municipal["estado_observacion"] = "valida"
        municipal.loc[missing, "estado_observacion"] = "ausente"
        municipal.loc[invalid, "estado_observacion"] = "invalida"
        municipal.loc[municipal["duplicado_exacto"], "estado_observacion"] = "duplicado_exacto"
        municipal.loc[municipal["conflicto_observacion"], "estado_observacion"] = "conflicto"
        municipal.loc[municipal["anio"].isna(), "estado_observacion"] = "anio_invalido"
        municipal["fuente"] = source
        municipal["archivo_fuente"] = path.name
        municipal["id_observacion"] = source + ":" + municipal["fila_fuente"].astype(str)
        municipal["duplicado_exacto_n"] = 1
        municipal["filas_fuente"] = municipal["fila_fuente"].map(lambda value: _json([int(value)]))
        duplicate_rows = municipal.loc[municipal["duplicado_exacto"]]
        for _, group in duplicate_rows.groupby(exact_columns, sort=False, dropna=False):
            municipal.loc[group.index, "duplicado_exacto_n"] = len(group)
            municipal.loc[group.index, "filas_fuente"] = _json([int(value) for value in group["fila_fuente"]])
        normalized = municipal.loc[~municipal.duplicated(exact_columns, keep="first")].copy()
        histories.append(normalized[HISTORY_COLUMNS])
        if latest_year is not None:
            templates = original.loc[original["Año"].eq(str(latest_year)), ["dimensiones_normalizadas", "Unidad"]].drop_duplicates()
            latest_rows = municipal.loc[municipal["anio"].eq(latest_year) & municipal["elegible_hasta_50000"].eq(True)]
            for template in templates.to_dict("records"):
                group = latest_rows.loc[latest_rows["dimensiones_normalizadas"].eq(template["dimensiones_normalizadas"]) &
                                        latest_rows["unidad"].eq(template["Unidad"])]
                present = group["codigo_ine"].nunique()
                available = group.loc[group["dato_disponible"], "codigo_ine"].nunique()
                zeros = group.loc[group["dato_disponible"] & group["valor"].eq(0), "codigo_ine"].nunique()
                coverage.append({"fuente": source, "anio": latest_year,
                                 "dimensiones_normalizadas": template["dimensiones_normalizadas"],
                                 "unidad": template["Unidad"], "municipios_elegibles": eligible_count,
                                 "con_fila": present, "con_valor": available,
                                 "con_cero_observado": zeros, "con_fila_sin_valor": present - available,
                                 "sin_fila": eligible_count - present})
        source_summary[source] = {
            "filas_originales": len(original), "filas_municipales": len(municipal),
            "agregados_no_municipales_excluidos": len(original) - len(municipal),
            "tipos_territorio_excluidos": original.loc[~original["Tipo territorio"].eq("Municipios"), "Tipo territorio"].value_counts().to_dict(),
            "anio_ultimo_global": latest_year,
            "municipios_vinculados_historico": municipal.loc[mapped, "codigo_ine"].nunique(),
            "filas_sin_codigo_municipal": int(municipal["codigo_ine"].eq("").sum()),
            "filas_nombre_no_coincide": int((mapped & ~name_match).sum()),
            "filas_valor_ausente": int(missing.sum()), "filas_valor_invalido": int(invalid.sum()),
            "filas_duplicadas_exactas": int(municipal["duplicado_exacto"].sum()),
            "duplicados_exactos_agrupados": len(municipal) - len(normalized),
            "filas_en_conflicto": int(municipal["conflicto_observacion"].sum()),
            "dimensiones": fields,
            "filas_etiquetas_normalizadas": int(municipal["dimensiones_originales"].ne(municipal["dimensiones_normalizadas"]).sum()),
        }
    history = pd.concat(histories, ignore_index=True) if histories else pd.DataFrame(columns=HISTORY_COLUMNS)
    latest_frames = [
        history.loc[history["fuente"].eq(source) & history["anio"].eq(info["anio_ultimo_global"])]
        for source, info in source_summary.items() if info["anio_ultimo_global"] is not None
    ]
    latest = pd.concat(latest_frames, ignore_index=True) if latest_frames else history.iloc[:0].copy()
    excluded_national = Path(raw_dir) / "1903452.csv"
    summary = {
        "fuentes": source_summary,
        "filas_historico": len(history), "filas_ultimo_anio": len(latest),
        "agregados_nacionales_excluidos": {"1903452": "Fuente nacional no municipal; no entra en tablas ni cobertura."},
        "fuente_nacional_presente": excluded_national.exists(),
        "municipios_elegibles_cobertura": eligible_count,
        "nota_migraciones": "Histórico por dirección y sexo separado; no interpretar cambios de población como saldo migratorio ni sumar Total con sexos.",
        "nota_duplicados": "Duplicados exactos agrupados con filas_fuente y duplicado_exacto_n; conflictos conservados por separado, con valor y disponibilidad bloqueados.",
    }
    return ({"estadisticas_historico.csv": history,
             "estadisticas_ultimo_anio.csv": latest,
             "cobertura_estadisticas.csv": pd.DataFrame(coverage, columns=COVERAGE_COLUMNS),
             "evolucion_poblacion.csv": _population_evolution(history)}, issues, summary)
