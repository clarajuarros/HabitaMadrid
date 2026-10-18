"""Apply a municipality map event before Streamlit recreates its widgets."""

from collections.abc import Iterable, MutableMapping
from typing import Any


def apply_ranking_selection(state, query_params, ordered_codes, *, event_key):
    """Aplica una fila del ranking visible, solo durante su callback nuevo."""
    from collections.abc import Mapping
    event = state.get(event_key)
    if not isinstance(event, Mapping) or not isinstance(event.get("selection"), Mapping):
        return False
    rows = event["selection"].get("rows")
    if not isinstance(rows, list) or len(rows) != 1 or type(rows[0]) is not int or not 0 <= rows[0] < len(ordered_codes):
        return False
    code = ordered_codes[rows[0]]
    if state.get("perfil_vivienda") not in ("VC", "VU") or code == state.get("municipio_codigo"):
        return False
    state["_mapa_pick_revision"] = state.get("_mapa_pick_revision", 0) + 1
    state["municipio_codigo"] = code
    state["busqueda_nombre"] = ""
    query_params["municipio"] = code
    query_params["vivienda"] = state["perfil_vivienda"]
    state["_last_query"] = (code, state["perfil_vivienda"])
    return True


def apply_map_selection(
    state: MutableMapping[str, Any],
    query_params: MutableMapping[str, Any],
    allowed_codes: Iterable[str],
    *,
    event_key: str = "mapa_municipios",
    layer_id: str = "municipios",
) -> bool:
    """Share a new eligible map selection with the search and municipal sheet.

    Call this from ``st.pydeck_chart(on_select=...)`` only. Streamlit executes
    that callback before recreating the municipality selector, so its value
    can be changed safely. Reading the retained map event during every render
    would instead overwrite a subsequent search selection.

    Empty/invalid events are no-ops. A new click on the current municipality
    opens its sheet again, without changing the map identity or camera.
    ``event_key`` identifies the chart that emitted the callback, including
    when an explicit framing command renews the chart. ``layer_id`` allows
    search to clear native picking without replacing the chart or its camera.
    The chart's read-only event itself is never changed. The existing valid
    housing profile and unrelated URL parameters are preserved.
    """
    from src.app_map import selection_code

    code = selection_code(state.get(event_key), allowed_codes, layer_id=layer_id)
    profile = state.get("perfil_vivienda", "VU")
    if code is None:
        return False
    if profile not in ("VC", "VU"):
        return False

    state["municipio_codigo"] = code
    state["busqueda_nombre"] = ""
    query_params["municipio"] = code
    query_params["vivienda"] = profile
    state["_last_query"] = (code, profile)
    # Clear the native pick by renewing only its layer, so the same polygon
    # can be opened with one click after returning from its sheet. The chart
    # key and client camera remain mounted.
    state["_mapa_pick_revision"] = state.get("_mapa_pick_revision", 0) + 1
    from src.app_navigation import SHEET, request_navigation
    request_navigation(state, SHEET, code)
    return True
