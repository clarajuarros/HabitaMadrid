"""Formato de la ficha sin convertir datos ausentes en valores o nombres."""
from decimal import Decimal
import math
import unittest

import pandas as pd

from src.app_presenter import (distance, esc, finite, metric_card, number,
                               raw_item, safe_link, selected_value, service_card)


class AppPresenterTests(unittest.TestCase):
    def test_spanish_decimal_and_thousands_separators(self):
        self.assertEqual(number(1234567.89, 2), "1.234.567,89")
        self.assertEqual(number(-1234.5, 1), "-1.234,5")
        self.assertEqual(number("1234.5", 2), "1.234,50")

    def test_zero_is_available_and_missing_is_never_zero(self):
        self.assertEqual(number(0), "0")
        self.assertEqual(number("0", 2), "0,00")
        for value in [None, "", "bad", float("nan"), float("inf"), -float("inf"), pd.NA, True]:
            with self.subTest(value=value):
                self.assertFalse(finite(value))
                self.assertEqual(number(value), "No hay datos disponibles")
                self.assertEqual(number(value, missing="—"), "—")

    def test_metric_distances_switch_units_without_changing_zero(self):
        self.assertEqual(distance(0), "0 m")
        self.assertEqual(distance(999), "999 m")
        self.assertEqual(distance(1000), "1,00 km")
        self.assertEqual(distance(1500.5), "1,50 km")
        self.assertEqual(distance(None), "Sin coordenadas verificadas")

    def test_distance_accepts_numeric_strings_and_decimal_objects(self):
        self.assertEqual(distance("1500"), "1,50 km")
        self.assertEqual(distance(Decimal("2500")), "2,50 km")
        self.assertEqual(distance("0"), "0 m")

    def test_selected_value_retains_zero_and_marks_missing_or_ambiguous_selection(self):
        frame = pd.DataFrame({"indicador_id": ["zero", "value", "duplicated", "duplicated"],
                              "valor": [0.0, 4.0, 1.0, 2.0]})
        self.assertEqual(selected_value(frame, "zero"), 0)
        self.assertEqual(selected_value(frame, "value"), 4)
        self.assertTrue(math.isnan(selected_value(frame, "absent")))
        self.assertTrue(math.isnan(selected_value(frame, "duplicated")))

    def test_html_escape_applies_to_user_and_source_text(self):
        self.assertEqual(esc('<a title="test">&'), "&lt;a title=&quot;test&quot;&gt;&amp;")
        for rendered in [metric_card("<script>", "<b>", 'a"b', unit="<&"),
                         raw_item("<script>", "<b>", 'a"b')]:
            self.assertNotIn("<script>", rendered)
            self.assertNotIn("<b>", rendered)
            self.assertIn("&lt;script&gt;", rendered)
            self.assertIn("a&quot;b", rendered)

    def test_links_reject_non_web_schemes_and_accept_published_urls(self):
        for url in [None, 123, "", "javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "//example.org"]:
            with self.subTest(url=url):
                self.assertEqual(safe_link(url), "")
        self.assertEqual(safe_link(" https://www.comunidad.madrid/centros/centro-publicado "),
                         "https://www.comunidad.madrid/centros/centro-publicado")

    def test_malformed_url_does_not_crash_a_source_card(self):
        self.assertEqual(safe_link("http://[malformed"), "")

    def test_service_name_is_published_text_not_an_invented_id_label(self):
        html = service_card({"servicio_id": "centro:12345", "nombre": "", "municipio": "Ajalvir",
                             "distancia_m": float("nan"), "url": ""})
        self.assertIn("Nombre no publicado", html)
        self.assertNotIn("centro:12345", html)
        self.assertIn("Sin posición verificable", html)
        self.assertNotIn("0 m", html)

    def test_service_card_escapes_source_fields_and_the_link_attribute(self):
        html = service_card({"nombre": '<img src=x onerror="alert(1)">',
                             "municipio": "<script>Madrid</script>", "direccion": "A & B",
                             "url": 'https://example.org/" onclick="alert(1)', "distancia_m": 0})
        self.assertNotIn("<img", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn(' onclick="', html)
        self.assertIn("&lt;img", html)
        self.assertIn("A &amp; B", html)
        self.assertIn("0 m", html)
        self.assertIn('rel="noopener noreferrer"', html)

    def test_service_card_does_not_infer_public_ownership_or_address(self):
        html = service_card({"nombre": "Consultorio publicado", "modalidad": "Consultorio",
                             "titularidad": "", "direccion": "", "distancia_m": 123,
                             "url": "javascript:alert(1)"})
        self.assertNotIn("Público", html)
        self.assertNotIn("PÚBLICO", html)
        self.assertNotIn("href=", html)
        self.assertIn("Consultorio publicado", html)

    def test_bus_card_labels_routes_as_feed_relations_without_service_promises(self):
        html = service_card({"nombre": "Parada publicada", "lineas": "191, 191B",
                             "modalidad": "Autobús interurbano (fuente parcial)", "distancia_m": 1450})
        self.assertIn("Líneas del feed parcial: 191, 191B", html)
        self.assertIn("1,45 km", html)
        self.assertNotIn("minutos", html)


if __name__ == "__main__":
    unittest.main()
