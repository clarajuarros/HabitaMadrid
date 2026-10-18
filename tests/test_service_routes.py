import json
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from src.service_routes import get_driving_route, validate_response, straight_distance

SOURCE = [-3.880406282, 40.90404891]
TARGET = [-3.879, 40.905]


def payload():
    return {"code": "Ok", "routes": [{"distance": 230.5, "duration": 46.2,
            "geometry": {"type": "LineString", "coordinates": [SOURCE, TARGET]}}],
            "waypoints": [{"location": SOURCE, "distance": 0}, {"location": TARGET, "distance": 0}]}


class DrivingRouteTests(unittest.TestCase):
    def test_keeps_provider_distance_time_geometry_and_snaps(self):
        route = validate_response(payload(), SOURCE, TARGET)
        self.assertEqual(route['distance_m'], 230.5)
        self.assertEqual(route['duration_s'], 46.2)
        self.assertEqual(route['geometry']['coordinates'], [SOURCE, TARGET])
        self.assertEqual(route['snap_distances_m'], [0, 0])

    def test_rejects_no_route_invalid_measure_and_remote_access(self):
        cases = [{'code': 'NoRoute'}]
        for field, value in [('distance', float('nan')), ('duration', -1), ('duration', None)]:
            data = payload(); data['routes'][0][field] = value; cases.append(data)
        data=payload();data['waypoints'][0]['location']=[-3.5,40.5];cases.append(data)
        data=payload();data['routes'][0]['geometry']['coordinates']=[SOURCE];cases.append(data)
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                validate_response(data,SOURCE,TARGET)

    def test_cache_avoids_network_and_coordinate_change_invalidates(self):
        client=Mock();client.get.return_value.json.return_value=payload()
        with tempfile.TemporaryDirectory() as folder, patch('src.service_routes.time.sleep'):
            first=get_driving_route(SOURCE,TARGET,folder,session=client)
            second=get_driving_route(SOURCE,TARGET,folder,session=client)
            self.assertEqual(first,second)
            self.assertEqual(client.get.call_count,1)
            self.assertIn('radiuses',client.get.call_args.kwargs['params'])
            get_driving_route(SOURCE,[-3.8791,40.905],folder,session=client)
            self.assertEqual(client.get.call_count,2)

    def test_outage_never_invents_time_or_road_distance(self):
        client=Mock();client.get.side_effect=requests.Timeout()
        with tempfile.TemporaryDirectory() as folder, patch('src.service_routes.time.sleep'):
            result=get_driving_route(SOURCE,[-3.878,40.906],folder,session=client)
            self.assertEqual(result['status'],'unavailable')
            self.assertNotIn('duration_s',result)
            self.assertNotIn('distance_m',result)
            get_driving_route(SOURCE,[-3.878,40.906],folder,session=client)
            self.assertEqual(client.get.call_count,1)

    def test_geodesic_zero_and_coordinate_validation(self):
        self.assertEqual(straight_distance(SOURCE,SOURCE),0)
        self.assertGreater(straight_distance(SOURCE,TARGET),100)
        with self.assertRaises(ValueError):straight_distance([0,0],TARGET)

    def test_malformed_provider_payloads_fall_back_without_crashing(self):
        for data in ([], {"code": "Ok", "routes": [None]},
                     {"code": "Ok", "routes": [{"geometry": None}]}):
            client = Mock()
            client.get.return_value.json.return_value = data
            with tempfile.TemporaryDirectory() as folder, patch('src.service_routes.time.sleep'), patch('src.service_routes._FAILURES', {}):
                result = get_driving_route(SOURCE, TARGET, folder, session=client)
                self.assertEqual(result['status'], 'unavailable')
                self.assertNotIn('duration_s', result)
                self.assertNotIn('distance_m', result)
