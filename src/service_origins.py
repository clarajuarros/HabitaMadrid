"""Verified IGN urban-place reference points, scoped to the service locator."""
from pathlib import Path
import hashlib
import json

import numpy as np
import pandas as pd
from shapely.geometry import Point, shape


def load_urban_origins(root, map_data):
    directory = Path(root) / "data/reference/urban_centres"
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    path = directory / "centros_urbanos.csv"
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["sha256"]:
        raise ValueError("La tabla de centros urbanos no coincide con su versión verificada")
    source_path = directory / "ign_capitales_seleccionadas.geojson"
    if hashlib.sha256(source_path.read_bytes()).hexdigest() != manifest["source_records_sha256"]:
        raise ValueError("Los registros originales del IGN no coinciden con la versión verificada")
    source_records = {f["id"]: f for f in json.loads(source_path.read_text())["features"]}
    frame = pd.read_csv(path, dtype={"codigo_ine": str}, keep_default_na=False)
    expected = set(map_data["origenes"].codigo_ine)
    if frame.codigo_ine.duplicated().any() or set(frame.codigo_ine) != expected:
        raise ValueError("Se requiere un centro urbano oficial por municipio elegible")
    if not np.isfinite(frame[["longitud", "latitud"]]).all().all():
        raise ValueError("Centro urbano sin coordenadas")
    boundaries = {f["properties"]["codigo_ine"]: shape(f["geometry"]) for f in map_data["geojson"]["features"]}
    for row in frame.itertuples():
        if not boundaries[row.codigo_ine].covers(Point(row.longitud, row.latitud)):
            raise ValueError("El centro urbano no pertenece al municipio indicado")
        record = source_records.get(row.identificador_ign)
        if record is None or not np.allclose(record["geometry"]["coordinates"], [row.longitud, row.latitud], rtol=0, atol=1e-10):
            raise ValueError("Las coordenadas no coinciden con el punto original del IGN")
        if row.tipo_ign not in {"Entidad singular", "Núcleos de población", "Capital de municipio"}:
            raise ValueError("El origen no corresponde a una localidad del IGN")
        if not row.identificador_ign.startswith("ES.IGN.NGBE."):
            raise ValueError("Falta la identidad oficial del núcleo urbano")
    return frame
