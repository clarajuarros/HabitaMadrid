"""Scheduled municipal connections from synthetic, deliberately unordered GTFS."""
import csv
from datetime import date, timedelta
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from src.transport_offer import TransportOfferError, build_transport_offer, orphan_line_coverage_gaps


A, B, TERMINAL, MISSING, MADRID = "28001", "28002", "28003", "28004", "28079"
WEEK = [date(2026, 10, 19) + timedelta(days=index) for index in range(7)]
FIELDS = {
    "routes.txt": ["route_id"],
    "stops.txt": ["stop_id", "stop_name"],
    "trips.txt": ["route_id", "service_id", "trip_id", "trip_headsign"],
    "calendar.txt": ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "start_date", "end_date"],
    "calendar_dates.txt": ["service_id", "date", "exception_type"],
    "stop_times.txt": ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "pickup_type", "drop_off_type"],
    "frequencies.txt": ["trip_id", "start_time", "end_time", "headway_secs"],
}


def visit(trip, stop, sequence, time="08:00:00", pickup="", dropoff=""):
    return {"trip_id": trip, "arrival_time": time, "departure_time": time,
            "stop_id": stop, "stop_sequence": str(sequence),
            "pickup_type": pickup, "drop_off_type": dropoff}


def calendar(service, weekend=False):
    return dict(zip(FIELDS["calendar.txt"], [service, "1", "1", "1", "1", "1",
                                           "1" if weekend else "0", "1" if weekend else "0", "20260101", "20261231"]))


class TransportOfferTests(unittest.TestCase):
    def test_orphan_variant_without_observed_stops_blocks_its_public_line_family(self):
        routes = [{"route_id": "missing_c5", "route_short_name": "C-5"},
                  {"route_id": "observed_c5", "route_short_name": " C5 "},
                  {"route_id": "inactive_c8b", "route_short_name": "C-8b"},
                  {"route_id": "observed_c8a", "route_short_name": "C-8a"}]
        orphans = [{"route_id": "missing_c5", "active_dates": ["2026-10-19"]},
                   {"route_id": "inactive_c8b", "active_dates": []}]
        municipal = [{"feed": "rail", "codigo_ine": A, "route_id": "observed_c5", "line": "C5"},
                     {"feed": "other", "codigo_ine": B, "route_id": "observed_c5", "line": "C5"},
                     {"feed": "rail", "codigo_ine": TERMINAL, "route_id": "observed_c8a", "line": "C8a"}]
        self.assertEqual(orphan_line_coverage_gaps(orphans, routes, municipal, "rail"), {A: ["C5"]})
        orphans[1]["active_dates"] = ["2026-10-19"]
        self.assertEqual(orphan_line_coverage_gaps(orphans, routes, municipal, "rail"), {A: ["C5"]})
        with self.assertRaisesRegex(TransportOfferError, "official line catalogue"):
            orphan_line_coverage_gaps(orphans, routes[1:], municipal, "rail")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def write_feed(self, key, tables, padded=False):
        path = Path(self.temp.name) / (key + ".zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for filename, rows in tables.items():
                fields = FIELDS[filename]
                output = io.StringIO(newline="")
                writer = csv.writer(output)
                writer.writerow([field + "   " if padded else field for field in fields])
                for row in rows:
                    writer.writerow([str(row.get(field, "")) + "  " if padded else row.get(field, "") for field in fields])
                archive.writestr(filename, output.getvalue())
        return {"key": key, "path": str(path)}

    def fixture(self):
        bus_mapping = {"a": A, "a2": A, "b": B, "terminal": TERMINAL, "m": MADRID, "unknown": None}
        trips = ["t1", "blocked", "local", "dropoff_blocked", "phone_only", "unknown_origin", "special"]
        bus = {
            "routes.txt": [{"route_id": "r"}],
            "stops.txt": [{"stop_id": stop, "stop_name": stop} for stop in bus_mapping],
            "trips.txt": [{"route_id": "r", "service_id": "special" if trip == "special" else "week",
                           "trip_id": trip, "trip_headsign": "Misleading destination ignored"} for trip in trips],
            "calendar.txt": [calendar("week")],
            "calendar_dates.txt": [{"service_id": "week", "date": "20261020", "exception_type": "2"},
                                   {"service_id": "special", "date": "20261020", "exception_type": "1"}],
            # Deliberately interleaved trips and nonmonotonic CSV sequence order.
            "stop_times.txt": [visit("t1", "m", 90, "26:00:00"),
                               visit("blocked", "a", 1, pickup="1"),
                               visit("local", "a", 1),
                               visit("t1", "a2", 20, "25:10:00"),
                               visit("dropoff_blocked", "b", 2, dropoff="1"),
                               visit("t1", "a", 10, "25:00:00"),
                               visit("blocked", "m", 2),
                               visit("local", "unknown", 2), visit("local", "a2", 3),
                               visit("t1", "b", 30, "25:20:00", pickup="1"),
                               visit("dropoff_blocked", "a", 1),
                               visit("phone_only", "a", 1, pickup="2"), visit("phone_only", "m", 2),
                               visit("unknown_origin", "unknown", 1), visit("unknown_origin", "terminal", 2),
                               visit("special", "a", 1), visit("special", "terminal", 2)],
        }
        rail_mapping = {"a": A, "b": B, "m": MADRID}
        rail = {
            "routes.txt": [{"route_id": "r"}],
            "stops.txt": [{"stop_id": stop} for stop in rail_mapping],
            "trips.txt": [{"route_id": "r", "service_id": "daily", "trip_id": "t1"}],
            "calendar.txt": [calendar("daily", weekend=True)],
            "stop_times.txt": [visit("t1", "b", 2, "01:10:00"),
                               visit("t1", "m", 3, "01:30:00"),
                               visit("t1", "a", 1, "01:00:00")],
        }
        return bus, rail, {"bus": bus_mapping, "rail": rail_mapping}

    def test_dated_trips_direct_destinations_and_namespace_are_not_duplicated(self):
        bus, rail, mappings = self.fixture()
        result = build_transport_offer([self.write_feed("bus", bus), self.write_feed("rail", rail)],
                                       mappings, WEEK, [A, B, TERMINAL, MISSING])
        monday = result["daily"][A]["2026-10-19"]
        self.assertEqual(monday["frequency"], 2)  # Same trip_id in two feeds counts twice.
        self.assertEqual(monday["connectivity"], 2)
        self.assertEqual(monday["destinations"], [B, MADRID])  # Madrid is not an eligible origin.

        self.assertEqual(monday["first_departure_seconds"], 3600)
        self.assertEqual(monday["last_departure_seconds"], 25 * 3600)
        self.assertEqual(monday["cobertura_presentes"], ["bus", "rail"])
        tuesday = result["daily"][A]["2026-10-20"]
        self.assertEqual(tuesday["frequency"], 2)  # Exception removes week and adds special.
        self.assertEqual(tuesday["connectivity"], 3)
        self.assertEqual(tuesday["destinations"], [B, TERMINAL, MADRID])
        summary = result["municipalities"][A]
        self.assertAlmostEqual(summary["frequency"], 12 / 7)
        self.assertAlmostEqual(summary["connectivity"], 15 / 7)
        self.assertEqual(summary["weekdays"], {"days": 5, "frequency": 2, "connectivity": 2.2})
        self.assertEqual(summary["saturday"], {"days": 1, "frequency": 1, "connectivity": 2})
        self.assertEqual(summary["sunday"], {"days": 1, "frequency": 1, "connectivity": 2})
        self.assertEqual(summary["covered_reference_days"], 7)

    def test_route_replaced_by_pdf_is_excluded_before_counting_and_coverage(self):
        bus, rail, mappings = self.fixture()
        feed = self.write_feed('bus', bus)
        feed['exclude_route_ids'] = ['r']
        result = build_transport_offer([feed], mappings, WEEK, [A, B, TERMINAL, MISSING])
        self.assertEqual(result['municipalities'][A]['frequency'], 0)
        self.assertFalse(result['municipalities'][A]['has_scheduled_data'])
        self.assertEqual(result['feeds']['bus']['excluded_route_ids'], ['r'])
        feed['exclude_route_ids'] = ['invented']
        with self.assertRaises(TransportOfferError):
            build_transport_offer([feed], mappings, WEEK, [A])

    def test_pickup_dropoff_terminal_local_and_unmapped_stops_do_not_invent_connections(self):
        bus, _, mappings = self.fixture()
        result = build_transport_offer([self.write_feed("bus", bus)], {"bus": mappings["bus"]}, WEEK, [A, B, TERMINAL, MISSING])
        self.assertEqual(result["daily"][A]["2026-10-19"]["frequency"], 1)
        self.assertEqual(result["daily"][B]["2026-10-19"]["frequency"], 0)  # Pickup forbidden or terminal.
        self.assertEqual(result["daily"][TERMINAL]["2026-10-19"]["frequency"], 0)
        self.assertTrue(result["municipalities"][TERMINAL]["has_scheduled_data"])
        self.assertFalse(result["municipalities"][MISSING]["has_scheduled_data"])
        self.assertEqual(result["municipalities"][MISSING]["cobertura_presentes"], [])
        self.assertFalse(result["daily"][A]["2026-10-24"]["has_active_service"])
        self.assertTrue(result["daily"][A]["2026-10-24"]["has_scheduled_data"])
        self.assertEqual(result["daily"][A]["2026-10-24"]["frequency"], 0)

    def test_padded_headers_and_identifiers_keep_leading_zero_stop_ids(self):
        _, rail, _ = self.fixture()
        rail["stops.txt"] = [{"stop_id": "0001"}, {"stop_id": "0002"}]
        rail["stop_times.txt"] = [visit("t1", "0002", 20), visit("t1", "0001", 10, "25:30:00")]
        result = build_transport_offer([self.write_feed("renfe", rail, padded=True)],
                                       {"renfe": {"0001  ": A, "0002": MADRID}}, [WEEK[0]], [A])
        self.assertEqual(result["daily"][A]["2026-10-19"]["frequency"], 1)
        self.assertEqual(result["daily"][A]["2026-10-19"]["first_departure_seconds"], 25 * 3600 + 30 * 60)
        self.assertEqual(result["municipalities"][A]["sunday"], {"days": 0, "frequency": None, "connectivity": None})

    def test_exception_only_feed_and_out_of_range_zero_are_identifiable(self):
        _, rail, _ = self.fixture()
        del rail["calendar.txt"]
        rail["calendar_dates.txt"] = [{"service_id": "daily", "date": "20261019", "exception_type": "1"}]
        result = build_transport_offer([self.write_feed("rail", rail)],
                                       {"rail": {"a": A, "b": B, "m": MADRID}}, WEEK, [A])
        self.assertEqual(result["daily"][A]["2026-10-19"]["frequency"], 1)
        self.assertEqual(result["daily"][A]["2026-10-20"]["feeds_in_range"], [])
        self.assertEqual(result["municipalities"][A]["covered_reference_days"], 1)

    def test_nonempty_frequencies_fail_instead_of_counting_template_trip(self):
        _, rail, _ = self.fixture()
        rail["frequencies.txt"] = [{"trip_id": "t1", "start_time": "06:00:00", "end_time": "12:00:00", "headway_secs": "600"}]
        with self.assertRaisesRegex(TransportOfferError, "frequencies.txt is unsupported"):
            build_transport_offer([self.write_feed("rail", rail)], {"rail": {}}, WEEK, [A])

    def test_unknown_references_fail_explicitly(self):
        for label in ("route", "service", "trip", "stop"):
            with self.subTest(reference=label):
                _, rail, _ = self.fixture()
                if label in ("route", "service"):
                    rail["trips.txt"][0][label + "_id"] = "unknown"
                else:
                    rail["stop_times.txt"][0][label + "_id"] = "unknown"
                with self.assertRaisesRegex(TransportOfferError, "unknown"):
                    build_transport_offer([self.write_feed("rail", rail)], {"rail": {}}, WEEK, [A])

    def test_duplicate_stop_sequence_and_feed_keys_are_rejected(self):
        _, rail, _ = self.fixture()
        rail["stop_times.txt"].append(dict(rail["stop_times.txt"][0]))
        feed = self.write_feed("rail", rail)
        with self.assertRaisesRegex(TransportOfferError, "duplicate.*trip_id, stop_sequence"):
            build_transport_offer([feed], {"rail": {}}, WEEK, [A])
        with self.assertRaisesRegex(TransportOfferError, "distinct nonempty keys"):
            build_transport_offer([feed, feed], {"rail": {}}, WEEK, [A])

    def test_orphans_default_to_error_and_diagnostic_mode_records_every_missing_trip(self):
        _, rail, _ = self.fixture()
        rail["trips.txt"].append({"trip_id": "missing", "route_id": "r", "service_id": "daily"})
        feed = self.write_feed("rail", rail)
        mapping = {"rail": {"a": A, "b": B, "m": MADRID}}
        with self.assertRaisesRegex(TransportOfferError, "published trip.*no stop_times"):
            build_transport_offer([feed], mapping, WEEK, [A])
        result = build_transport_offer([{**feed, "allow_incomplete_trips": True}], mapping, WEEK, [A])
        metadata = result["feeds"]["rail"]
        self.assertEqual(metadata["orphan_trip_count"], 1)
        self.assertEqual(metadata["active_orphan_trip_count"], 1)
        self.assertTrue(metadata["diagnostic_incomplete_trips"])
        self.assertEqual(metadata["orphans"], [{"trip_id": "missing", "route_id": "r", "active_dates": [day.isoformat() for day in WEEK]}])
        self.assertEqual(metadata["active_trip_count"]["2026-10-19"], 2)
        self.assertEqual(metadata["active_trips_with_stop_times"]["2026-10-19"], 1)
        self.assertEqual(result["daily"][A]["2026-10-19"]["frequency"], 1)


if __name__ == "__main__":
    unittest.main()
