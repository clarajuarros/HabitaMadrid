"""Candidate transport offer stays distinct from auxiliary stop distances."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.app_comparison import build_comparison
from src.app_explorer import build_explorer_frame, filter_explorer
from src.app_map import build_map_frame
from src.app_personal_report import build_personal_pdf
from src.app_recommendations import describe
from src.ivp_calculation import DIMENSIONS, calculate_index


ROOT = Path(__file__).resolve().parents[1]


def transport_fixture():
    codes = [f"28{index:03d}" for index in range(1, 156)]
    master = pd.DataFrame({"codigo_ine": codes, "municipio": [f"Municipio {index}" for index in range(155)],
                           "poblacion": np.arange(1000, 1155), "poblacion_anio": 2025})
    common = {"sanidad": "health", "educacion": "education", "transporte": "oferta_conectividad_programada"}
    config = {"indicadores_comunes": common, "perfiles": {p: {"indicador": "rent_" + p} for p in ("VC", "VU")},
              "metodologia_version": "candidate-transport", "pesos_generales": {d: .25 for d in DIMENSIONS}}
    observations = []
    for code in codes:
        for indicator, value in [("health", 400), ("education", 900), ("oferta_conectividad_programada", np.nan if code == codes[1] else 80),
                                 ("rent_VC", 7.5), ("rent_VU", 7.5)]:
            observations.append({"codigo_ine": code, "indicador_id": indicator, "valor": value, "estado": "disponible" if pd.notna(value) else "sin_dato",
                                 "periodo": "2026-10-19 a 2026-10-25", "anio": 2026, "fuentes_json": '["gtfs_test"]'})
    context = pd.DataFrame([{"codigo_ine": code, "indicador_id": "distancia_parada_bus_parcial_m", "valor": 1000 + index,
                             "periodo": "consulta_2026-10-15", "fuentes_json": '["gtfs_test"]'} for index, code in enumerate(codes)])
    offers = pd.DataFrame([{"codigo_ine": code, "expediciones_dia": 42, "destinos_directos_dia": 6,
                            "expediciones_laborable": 50, "expediciones_sabado": 25, "expediciones_domingo": 19,
                            "estado_cobertura": "horario_incompleto" if code == codes[1] else "contrastada",
                            "nota_cobertura": "Horario incompleto confirmado." if code == codes[1] else "Fuentes y líneas contrastadas.",
                            "inicio_semana": "2026-10-19", "fin_semana": "2026-10-25", "puntuacion_frecuencia": 70,
                            "puntuacion_conectividad": 90, "puntuacion_transporte": np.nan if code == codes[1] else 80} for code in codes])
    matrices, indices = [], []
    for profile in ("VC", "VU"):
        matrix = pd.DataFrame({d: [80 if d == "transporte" else 50] * len(codes) for d in DIMENSIONS}, index=codes, dtype=float)
        matrix.loc[codes[1], "transporte"] = np.nan
        calculated = calculate_index(matrix, {d: 1 for d in DIMENSIONS})
        for code in codes:
            matrices.append({"codigo_ine": code, "perfil_vivienda": profile, "metodo": "minmax", **matrix.loc[code].to_dict()})
            indices.append({"codigo_ine": code, "perfil_vivienda": profile, "metodo": "minmax", **calculated.loc[code].to_dict(),
                            "alcance": "cuatro_dimensiones", **{f"puntuacion_{d}": matrix.loc[code, d] for d in DIMENSIONS}})
    data = {"root": ROOT, "municipios": master, "configuracion": config,
            "observaciones": pd.DataFrame(observations), "contexto": context, "transporte_oferta": offers,
            "dimensiones": pd.DataFrame(matrices), "ivp_general": pd.DataFrame(indices),
            "fuentes": pd.DataFrame([{"fuente": "gtfs_test", "url": "https://example.test/gtfs", "fecha_descarga_utc": "2026-10-19"}]),
            "contexto_demografico": pd.DataFrame({"codigo_ine": codes, "crecimiento_2020_2025_pct": 1, "renta_disponible_per_capita_2023": 20000}),
            "evolucion_poblacion": pd.DataFrame({"codigo_ine": codes, "anio": 2025}),
            "sensibilidad": pd.DataFrame([{"codigo_ine": code, "perfil_vivienda": p} for code in codes for p in ("VC", "VU")]),
            "escenarios": pd.DataFrame(columns=["codigo_ine", "perfil_vivienda"]),
            "vivienda_historico": pd.DataFrame([{"codigo_ine": code, "tipo": p, "anio": 2024, "alquiler_m2_mes_mediana": 7.5,
                                                 "alquiler_mes_mediana": 600, "testigos": 100} for code in codes for p in ("VC", "VU")])}
    services = {d: pd.DataFrame(columns=["codigo_ine"]) for d in DIMENSIONS[:3]}
    return data, services


class TransportOfferUITests(unittest.TestCase):
    def setUp(self):
        self.data, self.services = transport_fixture()

    def test_explorer_joins_offer_without_calling_points_metres_or_recalculating_scores(self):
        frame = build_explorer_frame(self.data, "VU").set_index("codigo_ine")
        first = frame.loc["28001"]
        self.assertEqual(first["oferta_conectividad_programada"], 80)
        self.assertEqual(first["puntuacion_transporte"], 80)
        self.assertEqual(first["distancia_transporte_m"], 1000)
        self.assertEqual(first["expediciones_dia"], 42)
        self.assertTrue(frame.attrs["transporte_oferta_programada"])
        self.assertTrue(pd.isna(frame.loc["28002", "puntuacion_transporte"]))
        self.assertTrue(pd.isna(frame.loc["28002", "ivp_general"]))
        self.assertEqual(frame.loc["28002", "expediciones_dia"], 42)  # Available observations remain diagnostic.
        filtered = filter_explorer(frame.reset_index(), max_transport_distance_m=1000)
        self.assertEqual(filtered.codigo_ine.tolist(), ["28001"])

    def test_legacy_snapshot_without_offer_keeps_its_original_distance_observation(self):
        data = deepcopy(self.data)
        del data["transporte_oferta"]
        data["configuracion"]["indicadores_comunes"]["transporte"] = "distancia_parada_bus_parcial_m"
        obs = data["observaciones"]
        data["observaciones"] = pd.concat([obs.loc[~obs.indicador_id.eq("oferta_conectividad_programada")], data["contexto"]], ignore_index=True)
        frame = build_explorer_frame(data, "VU")
        self.assertEqual(frame.iloc[0]["distancia_transporte_m"], 1000)
        self.assertFalse(frame.attrs["transporte_oferta_programada"])
        self.assertNotIn("expediciones_dia", frame)

    def test_explanations_use_offer_and_keep_incomplete_coverage_visible(self):
        frame = build_explorer_frame(self.data, "VU", {"sanidad": 0, "educacion": 0, "transporte": 1, "vivienda": 0})
        explanation = describe(frame.iloc[0], frame, "personalizado")
        text = " ".join(explanation["positives"] + explanation["consider"])
        self.assertIn("42,0 expediciones", text)
        self.assertIn("6,0 destinos", text)
        self.assertIn("sin transbordos ni tiempo real", text)
        self.assertNotIn("Parada interurbana a", text)
        self.assertTrue(explanation["transport_offer_programmed"])
        self.assertEqual(explanation["transport_summary"]["inicio_semana"], "2026-10-19")
        missing = describe(frame.iloc[1], frame, "personalizado")
        self.assertIn("Transporte", missing["missing"])
        self.assertIn("Horario incompleto confirmado", " ".join(missing["consider"]))

    def test_comparison_metadata_keeps_auxiliary_distance_and_separate_programmed_offer(self):
        frame = build_explorer_frame(self.data, "VU")
        compared = build_comparison(self.data, self.services, ["28001", "28002"], "VU", frame)
        self.assertEqual(compared.iloc[0]["distancia_transporte_m"], 1000)
        self.assertEqual(compared.iloc[0]["expediciones_dia"], 42)
        self.assertEqual(compared.attrs["columnas"]["expediciones_dia"]["unidad"], "media / día")
        self.assertIn("contexto", compared.attrs["columnas"]["distancia_transporte_m"]["etiqueta"])
        self.assertEqual(set(compared), set(compared.attrs["columnas"]))
        self.assertIn("sin transbordos", " ".join(compared.attrs["limitaciones"]).lower())

    def test_map_transport_tooltip_describes_programmed_metrics_and_coverage(self):
        master = self.data["municipios"].assign(elegible_hasta_50000=True)
        excluded = pd.DataFrame([{"codigo_ine": "28999", "municipio": "Fuera del ámbito", "poblacion": 100000,
                                  "poblacion_anio": 2025, "elegible_hasta_50000": False}])
        frame = build_map_frame({"maestro": pd.concat([master, excluded], ignore_index=True)}, self.data, "VU", "transporte")
        text = frame.loc[frame.codigo_ine.eq("28001"), "tooltiptext"].iloc[0]
        self.assertIn("42,0 expediciones/día", text)
        self.assertIn("6,0 destinos directos/día", text)
        self.assertIn("oferta y conectividad programada", text)
        self.assertNotIn("puntuación de proximidad", text)
        missing = frame.loc[frame.codigo_ine.eq("28002")].iloc[0]
        self.assertEqual(missing["estado"], "sin_dato")
        self.assertIn("Horario incompleto confirmado", missing["tooltiptext"])

    def test_pdf_uses_same_offer_explanation_without_requiring_new_payload_adapter(self):
        frame = build_explorer_frame(self.data, "VU")
        explanation = describe(frame.iloc[0], frame)
        payload = {"mode": "general", "profile": "VU", "weights": {d: .25 for d in DIMENSIONS},
                   "records": [{"codigo_ine": "28001", "municipio": "Municipio 0", "active_score": 57.5,
                                "explanation": explanation, "sources": []}], "share_url": ""}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "data/reference/municipality_photos"
            folder.mkdir(parents=True)
            (folder / "catalog.json").write_text(json.dumps({}), encoding="utf-8")
            with patch("reportlab.rl_config.pageCompression", 0):
                pdf = build_personal_pdf(tmp, payload)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertIn(b"Oferta de transporte programada", pdf)
        self.assertIn(b"sin transbordos", pdf)
        self.assertNotIn(b"distancias de las cuatro dimensiones", pdf)


if __name__ == "__main__":
    unittest.main()
