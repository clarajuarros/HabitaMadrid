"""On-demand driving routes; never infer road distance or time from a straight line."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import threading
import time

import requests

ENDPOINT = "https://routing.openstreetmap.de/routed-car/route/v1/driving"
_LOCK = threading.Lock()
_LAST_REQUEST = 0.0
_FAILURES = {}
TTL_SECONDS = 86400
MAX_SNAP_METRES = 200


def coordinates(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Coordenadas incompletas")
    lon, lat = map(float, value)
    if not (math.isfinite(lon) and math.isfinite(lat) and -5 < lon < -2.5 and 39.5 < lat < 41.5):
        raise ValueError("Coordenadas fuera del ámbito de Madrid")
    return [lon, lat]


def straight_distance(source, target):
    """WGS84 geodesic distance in metres, including short and coincident pairs."""
    from pyproj import Geod
    source, target = coordinates(source), coordinates(target)
    return abs(Geod(ellps="WGS84").inv(*source, *target)[2])


def validate_response(payload, source, target):
    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise ValueError("El proveedor no ha encontrado una ruta en coche")
    route = payload["routes"][0]
    geometry = route.get("geometry", {})
    if geometry.get("type") != "LineString" or len(geometry.get("coordinates", [])) < 2:
        raise ValueError("La ruta no contiene un recorrido válido")
    for point in geometry["coordinates"]:
        if len(point) != 2 or not all(math.isfinite(float(x)) for x in point):
            raise ValueError("La ruta contiene coordenadas inválidas")
        if not (-6 < point[0] < -2 and 38 < point[1] < 43):
            raise ValueError("Recorrido fuera de la región esperada")
    for key in ("distance", "duration"):
        if isinstance(route.get(key), bool) or not isinstance(route.get(key), (int, float)) or not math.isfinite(route[key]) or route[key] < 0:
            raise ValueError("La ruta no tiene distancia o duración válida")
    waypoints = payload.get("waypoints", [])
    if len(waypoints) != 2:
        raise ValueError("Faltan los accesos a la red viaria")
    snaps = []
    for point, waypoint, end in zip((source, target), waypoints, (geometry["coordinates"][0], geometry["coordinates"][-1])):
        location = coordinates(waypoint.get("location"))
        snap = straight_distance(point, location)
        if snap > MAX_SNAP_METRES or straight_distance(location, end) > 10:
            raise ValueError("El acceso vial está demasiado lejos del punto elegido")
        snaps.append(round(snap, 1))
    return {"status": "ok", "distance_m": route["distance"], "duration_s": route["duration"],
            "geometry": geometry, "snap_distances_m": snaps,
            "provider": "OSRM · FOSSGIS / OpenStreetMap",
            "fetched_at": datetime.now(timezone.utc).isoformat()}


def get_driving_route(source, target, cache_dir, *, session=None):
    """One selected pair only; disk cache and a shared <=1 request/second limit."""
    global _LAST_REQUEST
    source, target = coordinates(source), coordinates(target)
    identity = json.dumps([ENDPOINT, source, target], separators=(",", ":"))
    key = hashlib.sha256(identity.encode()).hexdigest()
    path = Path(cache_dir) / (key + ".json")
    with _LOCK:
        now = time.time()
        try:
            if now - path.stat().st_mtime < TTL_SECONDS:
                cached = json.loads(path.read_text())
                if cached["identity"] == identity:
                    # Cached provider payload is validated again before display.
                    result = validate_response(cached["payload"], source, target)
                    result["fetched_at"] = cached["fetched_at"]
                    return result
        except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError):
            pass
        failure = _FAILURES.get(key)
        if failure and now - failure[0] < 60:
            return failure[1].copy()
        time.sleep(max(0, 1.05 - (time.monotonic() - _LAST_REQUEST)))
        _LAST_REQUEST = time.monotonic()
        try:
            client = session or requests
            pair = ";".join(",".join(f"{v:.8f}" for v in point) for point in (source, target))
            response = client.get(ENDPOINT + "/" + pair,
                                  params={"overview": "full", "geometries": "geojson", "steps": "false",
                                          "alternatives": "false", "radiuses": "200;200"},
                                  headers={"User-Agent": "HabitaMadrid/1.0 (local municipal services explorer)"},
                                  timeout=(3, 10))
            response.raise_for_status()
            payload = response.json()
            result = validate_response(payload, source, target)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"identity": identity, "payload": payload,
                                                 "fetched_at": result["fetched_at"]}), encoding="utf-8")
                temporary.replace(path)
            except OSError:
                pass  # A read-only deployment can still display the valid route.
            return result
        except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError, AttributeError, IndexError):
            result = {"status": "unavailable", "reason": "No hay una ruta en coche disponible ahora para estos puntos."}
            _FAILURES[key] = (now, result)
            return result.copy()
