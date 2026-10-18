"""Contratos compartidos para limpiar fuentes sin inventar correspondencias."""
from io import BytesIO
from pathlib import Path
import hashlib

import pandas as pd

ISSUE_COLUMNS = [
    "fuente", "tipo", "registro", "campo", "valor_original", "detalle",
    "severidad", "fila_fuente",
]


class IntegrationError(ValueError):
    """La fuente o el maestro incumplen el contrato requerido."""


def read_csv_strings(path, sep=";"):
    """Lee sin convertir vacíos, ceros iniciales o marcadores de ausencia.

    fila_fuente identifica el registro lógico de CSV contando la cabecera como
    fila 1. Un campo entrecomillado puede contener saltos de línea físicos.
    """
    content = Path(path).read_bytes()
    try:
        content.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        encoding = "cp1252"
    try:
        frame = pd.read_csv(BytesIO(content), sep=sep, encoding=encoding,
                            dtype=str, keep_default_na=False)
    except (ValueError, UnicodeError) as exc:
        raise IntegrationError(f"No se pudo leer el CSV: {path}") from exc
    if "fila_fuente" in frame:
        raise IntegrationError(f"La fuente ya utiliza la columna reservada fila_fuente: {path}")
    frame["fila_fuente"] = range(2, len(frame) + 2)
    return frame


def make_issue(fuente, tipo, registro="", campo="", valor_original="",
               detalle="", severidad="aviso", fila_fuente=""):
    return dict(zip(ISSUE_COLUMNS, [fuente, tipo, registro, campo, valor_original,
                                   detalle, severidad, fila_fuente]))


def attach_municipal_context(frame, master):
    """Enriquece por clave exacta; una asignación desconocida queda sin bandera.

    El módulo de origen guarda el nombre original antes de invocar esta función.
    No se aplica el filtro de población a las instalaciones de otros municipios.
    """
    if "codigo_ine" not in frame:
        raise IntegrationError("Falta codigo_ine en la tabla que se desea vincular.")
    result = frame.copy()
    lookup = master.set_index("codigo_ine")
    if not lookup.index.is_unique:
        raise IntegrationError("El maestro contiene claves municipales duplicadas.")
    result["municipio"] = result["codigo_ine"].map(lookup["municipio"]).fillna("")
    result["elegible_hasta_50000"] = (
        result["codigo_ine"].map(lookup["elegible_hasta_50000"]).astype("boolean"))
    return result


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
