"""Read-only, offline adapter between the prepared datasets and the application.

No Streamlit import, network request or pipeline execution belongs here. The UI
can cache ``load_app_data``; each uncached call validates the recorded snapshot.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
import re
import unicodedata

import numpy as np
import pandas as pd

from src.integration_common import IntegrationError


class AppDataError(IntegrationError):
    """A prepared dataset is missing, stale or inconsistent."""


MANIFESTS = {
    2: "data/processed/municipios/validacion.json",
    3: "data/processed/integracion/validacion_integracion.json",
    4: "data/processed/geografia/validacion_geografia.json",
    5: "data/processed/indicadores/validacion_indicadores.json",
    6: "data/processed/ivp/validacion_ivp.json",
}
TABLES = {
    "municipios": (2, "maestro_municipios.csv"),
    "ivp_general": (6, "ivp_general.csv"),
    "dimensiones": (6, "dimensiones_ivp.csv"),
    "observaciones": (6, "observaciones_seleccionadas.csv"),
    "seleccion": (6, "seleccion_indicadores.csv"),
    "catalogo": (5, "catalogo_indicadores.csv"),
    "sensibilidad": (6, "sensibilidad_municipios.csv"),
    "escenarios": (6, "sensibilidad_escenarios.csv"),
    "vivienda_historico": (3, "vivienda_historico.csv"),
    "evolucion_poblacion": (3, "evolucion_poblacion.csv"),
    "contexto_demografico": (5, "demografia_componentes.csv"),
    "fuentes": (5, "fuentes_indicadores.csv"),
    "contexto": (5, "indicadores_municipales.csv"),
}
JSON_FILES = {
    "configuracion": (6, "configuracion_ivp.json"),
    "normalizacion": (6, "normalizacion.json"),
}
DIMENSIONS = ("sanidad", "educacion", "transporte", "vivienda")
PROFILES = ("VC", "VU")
_CODE = re.compile(r"28\d{3}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppDataError(f"No se puede leer {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise AppDataError(f"{path.name}: se esperaba un objeto JSON")
    return value


def _snapshot_records(root: Path, include_all_outputs: bool = False) -> tuple[dict, dict]:
    """Collect consumed outputs and the complete phase-six ancestry.

    Recorded absolute paths are rebased to the supplied project root, allowing
    a copied checkout to work without weakening checks or reading outside it.
    """
    root = Path(root).resolve()
    from src.runtime_snapshot import MANIFEST, read_snapshot
    if (root / MANIFEST).exists():
        return read_snapshot(root)
    manifests = {phase: _json(root / path) for phase, path in MANIFESTS.items()}
    for phase, value in manifests.items():
        if value.get("fase") != phase or not isinstance(value.get("archivos"), dict):
            raise AppDataError(f"Manifiesto de fase {phase} inválido")
    entries = manifests[6].get("entradas", [])
    if not isinstance(entries, list) or not entries:
        raise AppDataError("La fase 6 no declara sus entradas")
    master_suffix = Path("data/processed/municipios/maestro_municipios.csv")
    recorded_master = next((Path(e.get("path", "")) for e in entries
                            if str(e.get("path", "")).endswith(str(master_suffix))), None)
    if recorded_master is None:
        raise AppDataError("La fase 6 no referencia el maestro municipal")
    recorded_root = recorded_master
    for _ in master_suffix.parts:
        recorded_root = recorded_root.parent
    records: dict[Path, dict] = {}

    def register(path: Path, record: dict) -> None:
        if not isinstance(record, dict):
            raise AppDataError(f"Metadatos de archivo inválidos: {path.name}")
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise AppDataError(f"Referencia fuera del proyecto: {path}")
        digest = record.get("sha256", "")
        if not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise AppDataError(f"Huella SHA256 inválida: {path.name}")
        previous = records.get(resolved)
        if previous is not None and previous["sha256"] != digest:
            raise AppDataError(f"Referencias incompatibles o caducadas: {path.name}")
        records[resolved] = record

    def reference(entry: dict) -> None:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]:
            raise AppDataError("Entrada de procedencia inválida")
        path = Path(entry["path"])
        if path.is_absolute():
            try:
                path = path.relative_to(recorded_root)
            except ValueError as exc:
                raise AppDataError(f"Entrada ajena al proyecto: {path.name}") from exc
        register(root / path, entry)

    # Older manifests expose their ancestry under different explicit keys.
    for manifest in manifests.values():
        for key in ("entradas", "metadatos_entrada"):
            entries_for_key = manifest.get(key, [])
            if not isinstance(entries_for_key, list):
                raise AppDataError(f"Lista de procedencia inválida: {key}")
            for entry in entries_for_key:
                reference(entry)
        if isinstance(manifest.get("maestro_entrada"), dict):
            reference(manifest["maestro_entrada"])
    for phase in (2, 3, 4, 5):
        if (root / MANIFESTS[phase]).resolve() not in records:
            raise AppDataError(f"Falta el enlace de procedencia a la fase {phase}")
    for phase, name in list(TABLES.values()) + list(JSON_FILES.values()):
        metadata = manifests[phase]["archivos"].get(name)
        if metadata is None:
            raise AppDataError(f"Salida no declarada en fase {phase}: {name}")
        register((root / MANIFESTS[phase]).parent / name, metadata)
    if include_all_outputs:
        for name, metadata in manifests[6]["archivos"].items():
            register((root / MANIFESTS[6]).parent / name, metadata)
    return manifests, records


def _verify_snapshot(root: Path) -> tuple[dict, dict]:
    """Hash the prepared snapshot on each uncached load."""
    manifests, records = _snapshot_records(root)
    total_bytes = 0
    for path, record in records.items():
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as exc:
            raise AppDataError(f"Falta un archivo preparado: {path.name}") from exc
        if ("bytes" in record and size != record["bytes"]) or digest != record["sha256"]:
            raise AppDataError(f"Huella distinta o dato caducado: {path.name}. Regenera la fase correspondiente.")
        total_bytes += size
    return manifests, {"archivos_verificados": len(records), "bytes_verificados": total_bytes,
                        "fase": 6, "generado_utc": manifests[6].get("generado_utc", "")}


def snapshot_signature(root: str | Path) -> str:
    """Cheap cache key using stat metadata, never reading the large datasets.

    Changing an ancestor, prepared output or adapter invalidates the UI cache.
    The subsequent uncached load performs the cryptographic checks. This key
    deliberately does not claim to be an integrity check in its own right.
    """
    root = Path(root).resolve()
    _, records = _snapshot_records(root, include_all_outputs=True)
    from src.runtime_snapshot import MANIFEST
    manifests = [MANIFEST] if (root / MANIFEST).exists() else MANIFESTS.values()
    paths = set(records) | {(root / path).resolve() for path in manifests}
    paths.update((root / "src" / name).resolve() for name in ("app_data.py", "app_services.py"))
    map_adapter = root / "src/app_map.py"
    if map_adapter.exists():
        paths.add(map_adapter.resolve())
    fingerprint = hashlib.sha256()
    for path in sorted(paths, key=str):
        try:
            stat = path.stat()
        except OSError as exc:
            raise AppDataError(f"Falta un archivo del snapshot: {path.name}") from exc
        fingerprint.update(f"{path.relative_to(root)}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8"))
    return fingerprint.hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, ValueError) as exc:
        raise AppDataError(f"CSV inválido: {path.name}") from exc
    if "codigo_ine" in table:
        if not table["codigo_ine"].str.fullmatch(r"28\d{3}").all():
            raise AppDataError(f"Código INE de cinco dígitos inválido: {path.name}")
    # Only declared numeric measures are converted. Source IDs and years stored
    # inside traceability JSON remain exact strings, with leading zeroes intact.
    numbers = {
        "poblacion", "poblacion_anio", "limite_poblacion", "anio", "valor", "valor_indice",
        "posicion", "peso_disponible", "municipios_universo", "municipios_con_indice",
        "sanidad", "educacion", "transporte", "vivienda", "puntuacion_sanidad",
        "puntuacion_educacion", "puntuacion_transporte", "puntuacion_vivienda",
        "escenarios_totales", "escenarios_con_indice", "posicion_min", "posicion_mediana",
        "posicion_max", "amplitud_posicion", "apariciones_top_n", "fraccion_top_n",
        "testigos", "alquiler_m2_mes_mediana", "alquiler_m2_mes_p25", "alquiler_m2_mes_p75",
        "alquiler_mes_mediana", "alquiler_mes_p25", "alquiler_mes_p75", "superficie_m2_mediana",
        "superficie_m2_p25", "superficie_m2_p75", "anio_anterior_registrado", "intervalo_anios",
        "poblacion_anterior", "variacion_absoluta", "variacion_porcentaje", "poblacion_2020",
        "poblacion_2024", "poblacion_2025", "crecimiento_2020_2025_pct",
        "renta_disponible_per_capita_2023", "saldo_migratorio_externo_cm_2024",
        "saldo_migratorio_externo_cm_2024_por1000",
    }
    for column in numbers.intersection(table.columns):
        nonempty = table[column].ne("")
        converted = pd.to_numeric(table[column].replace("", np.nan), errors="coerce")
        if (nonempty & converted.isna()).any() or np.isinf(converted.to_numpy(dtype=float)).any():
            raise AppDataError(f"Valor numérico inválido: {path.name}/{column}")
        table[column] = converted
    for column in ("elegible_hasta_50000", "dato_disponible", "comparacion_disponible",
                   "alerta_contraste_local_primaria"):
        if column in table:
            if not table[column].isin(["True", "False"]).all():
                raise AppDataError(f"Booleano inválido: {path.name}/{column}")
            table[column] = table[column].eq("True")
    return table


def _unique(table: pd.DataFrame, columns: list[str], name: str) -> None:
    if not set(columns).issubset(table):
        raise AppDataError(f"Faltan columnas en {name}: {columns}")
    if table.duplicated(columns).any():
        raise AppDataError(f"Registros duplicados en {name}")


def _validate_model(data: dict) -> None:
    master = data["municipios"]
    _unique(master, ["codigo_ine"], "municipios")
    if len(master) != 155 or master["poblacion"].isna().any() or not master["poblacion"].between(0, 50000).all():
        raise AppDataError("El universo debe contener los 155 municipios de hasta 50.000 habitantes")
    eligible = set(master["codigo_ine"])
    for name, key in (("ivp_general", ["codigo_ine", "perfil_vivienda"]),
                      ("dimensiones", ["codigo_ine", "perfil_vivienda"]),
                      ("sensibilidad", ["codigo_ine", "perfil_vivienda"])):
        table = data[name]
        _unique(table, key, name)
        if len(table) != 310 or set(table["perfil_vivienda"]) != set(PROFILES):
            raise AppDataError(f"Matriz municipal incompleta: {name}")
        if any(set(group["codigo_ine"]) != eligible for _, group in table.groupby("perfil_vivienda")):
            raise AppDataError(f"Universo municipal incompatible: {name}")
    ivp = data["ivp_general"]
    master_by_code = master.set_index("codigo_ine")
    for column in ("poblacion", "poblacion_anio"):
        expected = ivp["codigo_ine"].map(master_by_code[column])
        if not np.array_equal(ivp[column].to_numpy(), expected.to_numpy()):
            raise AppDataError(f"El IVP no coincide con el maestro: {column}")
    available = ivp["estado_indice"].eq("disponible")
    if not available.equals(ivp["valor_indice"].notna()):
        raise AppDataError("Los estados del IVP no coinciden con la disponibilidad")
    if not ivp.loc[available, "valor_indice"].between(0, 100).all():
        raise AppDataError("IVP fuera de la escala 0–100")
    if not ivp["modalidad"].eq("general").all() or not ivp["metodo"].eq("minmax").all():
        raise AppDataError("El IVP general requiere modalidad general y método minmax")
    for column in DIMENSIONS:
        a = data["dimensiones"].set_index(["codigo_ine", "perfil_vivienda"])[column].sort_index()
        b = ivp.set_index(["codigo_ine", "perfil_vivienda"])[f"puntuacion_{column}"].sort_index()
        if not np.allclose(a.to_numpy(dtype=float), b.to_numpy(dtype=float), equal_nan=True, atol=1e-9):
            raise AppDataError(f"Puntuaciones incongruentes en {column}")
        if not a.dropna().between(0, 100).all():
            raise AppDataError(f"Dimensión fuera de la escala 0–100: {column}")
    observations = data["observaciones"]
    _unique(observations, ["codigo_ine", "indicador_id"], "observaciones")
    config = data["configuracion"]
    wanted = set(config["indicadores_comunes"].values()) | {config["perfiles"][p]["indicador"] for p in PROFILES}
    if len(observations) != 775 or set(observations["indicador_id"]) != wanted:
        raise AppDataError("Selección de cinco indicadores incompleta")
    if any(set(group["codigo_ine"]) != eligible for _, group in observations.groupby("indicador_id")):
        raise AppDataError("Las observaciones no cubren el universo municipal")
    if not observations["estado"].eq("disponible").equals(observations["valor"].notna()):
        raise AppDataError("Un dato ausente se ha confundido con un valor disponible")
    _unique(data["seleccion"], ["indicador_id"], "seleccion")
    if set(data["seleccion"]["indicador_id"]) != wanted:
        raise AppDataError("El catálogo seleccionado no coincide con la configuración")
    for row in data["seleccion"].to_dict("records"):
        expected_unit = "EUR/m²/mes" if row["dimension"] == "vivienda" else "m"
        if row["unidad"] != expected_unit:
            raise AppDataError(f"Unidad incompatible: {row['indicador_id']}")
        try:
            periods = json.loads(row["periodos_json"])
        except (ValueError, TypeError) as exc:
            raise AppDataError("Períodos seleccionados inválidos") from exc
        if not periods or any(not isinstance(period, str) or not re.fullmatch(r"(?:\d{4}|consulta_\d{4}-\d{2}-\d{2})", period) for period in periods):
            raise AppDataError("Fecha o período seleccionado inválido")
        try:
            for period in periods:
                if period.startswith("consulta_"):
                    date.fromisoformat(period.removeprefix("consulta_"))
        except ValueError as exc:
            raise AppDataError("Fecha de consulta imposible") from exc
        if not observations.loc[observations["indicador_id"].eq(row["indicador_id"]), "periodo"].isin(periods).all():
            raise AppDataError(f"Períodos incongruentes: {row['indicador_id']}")
    for name, keys in (("vivienda_historico", ["codigo_ine", "anio", "tipo"]),
                       ("evolucion_poblacion", ["codigo_ine", "anio"]),
                       ("contexto_demografico", ["codigo_ine"]),
                       ("catalogo", ["indicador_id"]), ("fuentes", ["fuente"]),
                       ("escenarios", ["codigo_ine", "perfil_vivienda", "metodo", "escenario_pesos"])):
        _unique(data[name], keys, name)
    if len(data["escenarios"]) != 12090 or not set(data["escenarios"]["codigo_ine"]).issubset(eligible):
        raise AppDataError("Escenarios de sensibilidad incompletos")
    if set(data["contexto_demografico"]["codigo_ine"]) != eligible:
        raise AppDataError("Contexto demográfico municipal incompleto")
    if len(data["vivienda_historico"]) != 5012:
        raise AppDataError("Serie histórica de vivienda incompleta")
    if set(config.get("perfiles", {})) != set(PROFILES):
        raise AppDataError("La configuración debe distinguir las tipologías VC y VU")


def load_app_data(root: str | Path) -> dict:
    """Load an integrity-checked snapshot, preserving missing measures as NaN."""
    root = Path(root).resolve()
    validations, integrity = _verify_snapshot(root)
    data = {name: _read_csv((root / MANIFESTS[phase]).parent / filename)
            for name, (phase, filename) in TABLES.items()}
    data.update({name: _json((root / MANIFESTS[phase]).parent / filename)
                 for name, (phase, filename) in JSON_FILES.items()})
    data["municipios"] = data["municipios"].loc[data["municipios"]["elegible_hasta_50000"]].reset_index(drop=True)
    data["dimensiones"] = data["dimensiones"].loc[data["dimensiones"]["metodo"].eq("minmax")].reset_index(drop=True)
    _validate_model(data)
    # The revised primary-care definition supersedes the old candidate only in
    # the selected catalog; phase-five history remains available as authored.
    data["catalogo_seleccionado"] = data["seleccion"].copy()
    directory_source = pd.DataFrame([{
        "fuente": "cm_directorio_atencion_primaria",
        "archivo": "research/sanidad_fase6_2026-10-15/descargas.json",
        "url": "https://www.comunidad.madrid/centros/buscador-centros-sanitarios",
        "fecha_descarga_utc": validations[6].get("sanidad", {}).get("periodo", "").removeprefix("consulta_"),
        "licencia": "Consultar condiciones de reutilización de la Comunidad de Madrid",
        "nota": validations[6].get("sanidad", {}).get("limitaciones", ""),
    }])
    data["fuentes"] = pd.concat([data["fuentes"], directory_source], ignore_index=True)
    data["validaciones"], data["integridad"], data["root"] = validations, integrity, root
    return data


def municipal_view(data: dict, codigo_ine: str, profile: str) -> dict:
    """A municipality/profile view. Unknown or excluded codes fail explicitly."""
    if not isinstance(codigo_ine, str) or not _CODE.fullmatch(codigo_ine):
        raise AppDataError("El código INE debe ser un texto de cinco dígitos de Madrid")
    if profile not in PROFILES:
        raise AppDataError("La tipología de vivienda debe ser VC o VU")
    municipality = data["municipios"].loc[data["municipios"]["codigo_ine"].eq(codigo_ine)]
    if len(municipality) != 1:
        raise AppDataError("Municipio desconocido o fuera del ámbito de hasta 50.000 habitantes")

    def rows(name: str) -> pd.DataFrame:
        table = data[name]
        mask = table["codigo_ine"].eq(codigo_ine)
        if "perfil_vivienda" in table:
            mask &= table["perfil_vivienda"].eq(profile)
        if name == "vivienda_historico":
            mask &= table["tipo"].eq(profile)
        return table.loc[mask].copy()

    wanted = set(data["configuracion"]["indicadores_comunes"].values()) | {data["configuracion"]["perfiles"][profile]["indicador"]}
    observations = rows("observaciones")
    observations = observations.loc[observations["indicador_id"].isin(wanted)].copy()
    return {
        "municipio": municipality.iloc[0].to_dict(),
        "index_row": rows("ivp_general").iloc[0].to_dict(),
        "dimensiones": rows("dimensiones").iloc[0].to_dict(),
        "observaciones": observations,
        "contexto": rows("contexto"),
        "contexto_demografico": rows("contexto_demografico").iloc[0].to_dict(),
        "poblacion_historico": rows("evolucion_poblacion").sort_values("anio"),
        "vivienda_historico": rows("vivienda_historico").sort_values("anio"),
        "sensibilidad": rows("sensibilidad").iloc[0].to_dict(),
        "escenarios": rows("escenarios"),
        "perfil_vivienda": profile,
    }


def search_options(data: dict, query: str = "") -> list[tuple[str, str]]:
    """Stable ``(INE, name)`` options, with optional accent-insensitive search."""
    def plain(value: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
    query = plain(str(query).strip())
    table = data["municipios"].sort_values(["municipio", "codigo_ine"])
    return [(row.codigo_ine, row.municipio) for row in table.itertuples()
            if not query or query in plain(row.municipio) or query in row.codigo_ine]
