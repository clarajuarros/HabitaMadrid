"""Maestro municipal de HabitaMadrid a partir del padrón y las claves de IDEM.

Fase 2: población y elegibilidad. No integra centros ni transforma geometrías.
"""
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import hashlib
import json
import re
import unicodedata

import pandas as pd

POPULATION_LIMIT = 50_000
REQUIRED_COLUMNS = {
    "Año", "Tipo territorio", "Código territorio", "Territorio",
    "Sexo", "Valor", "Unidad", "Estado dato",
}
MUNICIPAL_DESCRIPTIONS = {"Término Municipal", "Termino Municipal"}
MASTER_COLUMNS = [
    "codigo_ine", "codigo_municipio", "codigo_almudena", "municipio",
    "municipio_padron", "poblacion", "poblacion_anio", "limite_poblacion",
    "elegible_hasta_50000", "motivo_elegibilidad", "estado_dato_poblacion",
]


class MunicipalityDataError(ValueError):
    """La fuente no permite construir un maestro inequívoco y completo."""


def normalized_name(value):
    """Control de nombres tras unir por código; solo normaliza forma y artículos."""
    text = unicodedata.normalize("NFKD", str(value)).lower()
    text = "".join(c for c in text if not unicodedata.combining(c))
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    if len(tokens) > 1 and tokens[-1] in {"el", "la", "los", "las"}:
        tokens.insert(0, tokens.pop())
    return " ".join(tokens)


def source_record(path, content, **details):
    return {"path": str(Path(path).resolve()), "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(), **details}


def read_population(path):
    content = Path(path).read_bytes()
    try:
        content.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        encoding = "cp1252"
    try:
        frame = pd.read_csv(BytesIO(content), sep=";", encoding=encoding,
                            dtype=str, keep_default_na=False)
    except (ValueError, UnicodeError, pd.errors.ParserError) as exc:
        raise MunicipalityDataError("No se pudo leer el CSV del padrón.") from exc
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise MunicipalityDataError("Faltan columnas del padrón: " + ", ".join(sorted(missing)))
    return frame, source_record(path, content, encoding=encoding, delimiter=";", rows=len(frame))


def read_municipal_names(path):
    content = Path(path).read_bytes()
    try:
        geojson = json.loads(content)
    except (ValueError, UnicodeError) as exc:
        raise MunicipalityDataError("No se pudo leer el GeoJSON de IDEM.") from exc
    if not isinstance(geojson, dict) or geojson.get("type") != "FeatureCollection":
        raise MunicipalityDataError("La cartografía debe ser una FeatureCollection.")
    features = geojson.get("features")
    if not isinstance(features, list):
        raise MunicipalityDataError("La cartografía no contiene una lista de features.")
    rows, descriptions = [], {}
    for feature in features:
        props = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(props, dict):
            raise MunicipalityDataError("Hay elementos cartográficos sin propiedades válidas.")
        description = props.get("DS_DESCRIPCION", "Sin descripción")
        descriptions[description] = descriptions.get(description, 0) + 1
        if description not in MUNICIPAL_DESCRIPTIONS:
            continue
        code, name = props.get("CD_INE"), props.get("DS_NOMBRE")
        if not isinstance(code, str) or re.fullmatch(r"28[0-9]{3}", code) is None:
            raise MunicipalityDataError("Código INE inválido en un término municipal de IDEM.")
        if not isinstance(name, str) or not name.strip():
            raise MunicipalityDataError("Término municipal de IDEM sin nombre.")
        if props.get("CD_MUNICIPIO") is not None and props["CD_MUNICIPIO"] != code[-3:]:
            raise MunicipalityDataError("CD_MUNICIPIO y CD_INE no corresponden en IDEM: " + code)
        rows.append({"codigo_ine": code, "municipio": name.strip()})
    names = pd.DataFrame(rows, columns=["codigo_ine", "municipio"])
    if names.empty or names["codigo_ine"].duplicated().any():
        raise MunicipalityDataError("IDEM debe contener términos municipales con códigos únicos.")
    metadata = source_record(path, content, features=len(features),
                             descriptions=descriptions, crs_declared=geojson.get("crs"))
    return names, metadata


def population_numbers(frame, context):
    text = frame["Valor"].str.strip()
    if not text.str.fullmatch(r"[0-9]+").all():
        raise MunicipalityDataError("Población ausente, negativa o no entera en " + context + ".")
    try:
        return text.astype("int64")
    except (ValueError, OverflowError) as exc:
        raise MunicipalityDataError("Población fuera del rango entero admitido en " + context + ".") from exc


def build_municipality_master(population_path, boundaries_path,
                              reference_year=2025, expected_count=179):
    """Devuelve el maestro de toda la Comunidad y los controles de su construcción.

    El año es explícito y común a todas las filas. Un dato inválido causa error,
    sin convertir ausencias en ceros ni recurrir a otro año por municipio.
    """
    if isinstance(reference_year, bool) or not isinstance(reference_year, int):
        raise MunicipalityDataError("El año de referencia debe ser un entero.")
    population, population_source = read_population(population_path)
    names, geography_source = read_municipal_names(boundaries_path)
    year_rows = population[population["Año"].str.strip().eq(str(reference_year))].copy()
    if year_rows.empty:
        raise MunicipalityDataError("El padrón no contiene el año solicitado: " + str(reference_year))
    municipal = year_rows[year_rows["Tipo territorio"].eq("Municipios") &
                          year_rows["Sexo"].isin(["Total", "Hombres", "Mujeres"])].copy()
    municipal["Código territorio"] = municipal["Código territorio"].str.strip()
    if not municipal["Código territorio"].str.fullmatch(r"[0-9]{4}").all():
        raise MunicipalityDataError("Código territorial del padrón inválido; debe conservar cuatro dígitos.")
    if municipal.duplicated(["Código territorio", "Sexo"]).any():
        raise MunicipalityDataError("Duplicado municipal por código, año y sexo en el padrón.")
    municipal["poblacion_validada"] = population_numbers(municipal, "municipios")
    if not municipal["Unidad"].eq("Número").all():
        raise MunicipalityDataError("Unidad de población distinta de Número; revisar la fuente.")
    by_sex = municipal.pivot(index="Código territorio", columns="Sexo", values="poblacion_validada")
    if not {"Total", "Hombres", "Mujeres"}.issubset(by_sex.columns) or by_sex.isna().any().any():
        raise MunicipalityDataError("Falta alguna observación Total, Hombres o Mujeres en el año de referencia.")
    if not (by_sex["Hombres"] + by_sex["Mujeres"]).eq(by_sex["Total"]).all():
        raise MunicipalityDataError("La suma de Hombres y Mujeres no coincide con Total.")
    totals = municipal[municipal["Sexo"].eq("Total")].copy()
    totals["codigo_ine"] = "28" + totals["Código territorio"].str[:3]
    if totals["codigo_ine"].duplicated().any():
        raise MunicipalityDataError("Más de un código territorial produce el mismo código INE.")
    if expected_count is not None and (len(totals) != expected_count or len(names) != expected_count):
        raise MunicipalityDataError(
            f"Cobertura municipal incompleta: padrón={len(totals)}, IDEM={len(names)}, esperados={expected_count}.")
    population_codes, geography_codes = set(totals["codigo_ine"]), set(names["codigo_ine"])
    if population_codes != geography_codes:
        raise MunicipalityDataError(
            "Códigos sin correspondencia. Solo padrón: " + str(sorted(population_codes - geography_codes)) +
            "; solo IDEM: " + str(sorted(geography_codes - population_codes)))
    merged = totals.merge(names, on="codigo_ine", how="inner", validate="one_to_one")
    different = merged.apply(lambda row: normalized_name(row["Territorio"]) !=
                             normalized_name(row["municipio"]), axis=1)
    if different.any():
        raise MunicipalityDataError("Nombres municipales discrepantes en códigos: " +
                                    ", ".join(merged.loc[different, "codigo_ine"]))
    regional = year_rows[year_rows["Tipo territorio"].eq("Comunidad de Madrid") &
                         year_rows["Sexo"].eq("Total")].copy()
    if len(regional) != 1:
        raise MunicipalityDataError("Debe existir un único Total autonómico para contrastar la suma municipal.")
    regional_total = int(population_numbers(regional, "Total autonómico").iloc[0])
    municipal_total = int(merged["poblacion_validada"].sum())
    if regional_total != municipal_total:
        raise MunicipalityDataError("La suma municipal no coincide con el Total autonómico del mismo año.")
    eligible = merged["poblacion_validada"].le(POPULATION_LIMIT)
    master = pd.DataFrame({
        "codigo_ine": merged["codigo_ine"],
        "codigo_municipio": merged["codigo_ine"].str[-3:],
        "codigo_almudena": merged["Código territorio"],
        "municipio": merged["municipio"],
        "municipio_padron": merged["Territorio"],
        "poblacion": merged["poblacion_validada"],
        "poblacion_anio": reference_year,
        "limite_poblacion": POPULATION_LIMIT,
        "elegible_hasta_50000": eligible,
        "motivo_elegibilidad": [
            f"Población de {reference_year} hasta 50.000 habitantes inclusive" if value
            else f"Población de {reference_year} superior a 50.000 habitantes" for value in eligible],
        "estado_dato_poblacion": merged["Estado dato"],
    })[MASTER_COLUMNS].sort_values("codigo_ine").reset_index(drop=True)
    validation = {
        "schema_version": 1, "fase": 2, "poblacion_anio": reference_year,
        "fecha_referencia_padron": f"{reference_year}-01-01", "limite_poblacion": POPULATION_LIMIT,
        "regla": "poblacion <= 50000", "municipios_total": len(master),
        "municipios_elegibles": int(master["elegible_hasta_50000"].sum()),
        "municipios_excluidos": int((~master["elegible_hasta_50000"]).sum()),
        "municipios_exactamente_en_limite": int(master["poblacion"].eq(POPULATION_LIMIT).sum()),
        "poblacion_total_municipal": municipal_total, "poblacion_total_autonomica": regional_total,
        "nombres_normalizados_para_control": int(merged["Territorio"].ne(merged["municipio"]).sum()),
        "fuentes": {"padron": population_source, "idem": geography_source},
        "controles": {"cobertura_completa": True, "codigos_unicos": True,
                      "codigos_y_nombres_coinciden": True, "poblacion_entera_no_negativa": True,
                      "hombres_mujeres_igual_total": True, "suma_municipal_igual_autonomica": True,
                      "un_solo_anio_sin_imputaciones": True},
        "alcance": "Maestro y filtro poblacional. Los servicios de otros municipios se conservan para accesibilidad.",
        "geometria": "Se usan claves y nombres de términos; no se han unido enclaves ni jurisdicciones compartidas.",
    }
    return master, validation


def compare_previous_master(master, previous_path):
    """Contrasta con los antecedentes sin sustituirlos ni cambiar su semántica."""
    previous_path = Path(previous_path)
    if not previous_path.exists():
        return {"disponible": False}
    old = pd.read_csv(previous_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    required = {"codigo_ine", "poblacion", "poblacion_anio", "elegible_menos_50000"}
    if not required.issubset(old.columns) or old["codigo_ine"].duplicated().any():
        raise MunicipalityDataError("El maestro anterior no tiene el contrato histórico esperado.")
    years = sorted(old["poblacion_anio"].unique().tolist())
    same_year = years == [str(master["poblacion_anio"].iloc[0])]
    if not old["elegible_menos_50000"].isin(["True", "False"]).all():
        raise MunicipalityDataError("Bandera histórica de elegibilidad inválida.")
    old_eligible = set(old.loc[old["elegible_menos_50000"].eq("True"), "codigo_ine"])
    new_eligible = set(master.loc[master["elegible_hasta_50000"], "codigo_ine"])
    population_changes = []
    if same_year:
        old_populations = old.set_index("codigo_ine")["poblacion"].to_dict()
        population_changes = [r["codigo_ine"] for _, r in master.iterrows()
                              if old_populations.get(r["codigo_ine"]) != str(r["poblacion"])]
        if population_changes:
            raise MunicipalityDataError("El padrón no coincide con el antecedente del mismo año.")
    return {"disponible": True, "path": str(previous_path.resolve()), "mismo_anio": same_year,
            "anio_anterior": years, "regla_anterior": "poblacion < 50000", "regla_actual": "poblacion <= 50000",
            "mismos_codigos_elegibles": old_eligible == new_eligible,
            "incorporados": sorted(new_eligible - old_eligible), "retirados": sorted(old_eligible - new_eligible),
            "poblaciones_discrepantes_mismo_anio": population_changes}


def write_municipality_outputs(master, validation, output_dir):
    """Publica derivados locales ya validados y una lista reutilizable de códigos."""
    output_dir = Path(output_dir)
    # La CLI nunca puede escribir sobre los originales usados como entrada.
    for source in validation["fuentes"].values():
        source_dir = Path(source["path"]).resolve().parent
        if output_dir.resolve() == source_dir or source_dir in output_dir.resolve().parents:
            raise MunicipalityDataError("La salida debe quedar fuera de las carpetas de fuentes originales.")
    eligible = master[master["elegible_hasta_50000"]]
    excluded = master[~master["elegible_hasta_50000"]]
    output_dir.mkdir(parents=True, exist_ok=True)
    tables = {"maestro_municipios.csv": master, "municipios_elegibles.csv": eligible,
              "municipios_excluidos.csv": excluded}
    for name, frame in tables.items():
        frame.to_csv(output_dir / name, index=False, encoding="utf-8-sig")
    codes = {"poblacion_anio": int(validation["poblacion_anio"]), "regla": validation["regla"],
             "limite_poblacion": POPULATION_LIMIT, "codigo_ine_tipo": "texto de cinco caracteres",
             "codigos": eligible["codigo_ine"].tolist()}
    (output_dir / "codigos_elegibles.json").write_text(
        json.dumps(codes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    completed = {**validation, "generado_utc": datetime.now(timezone.utc).isoformat(), "archivos": {}}
    for name in [*tables, "codigos_elegibles.json"]:
        content = (output_dir / name).read_bytes()
        completed["archivos"][name] = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    (output_dir / "validacion.json").write_text(
        json.dumps(completed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return completed
