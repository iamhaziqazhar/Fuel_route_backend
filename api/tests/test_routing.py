from __future__ import annotations

from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings

from api.routing import RoutingError, RoutingService


def _ors_payload(distance_m=4500000.0, duration_s=151200.0, geometry='abc123'):
    return {
        'routes': [{
            'summary': {'distance': distance_m, 'duration': duration_s},
            'geometry': geometry,
            'segments': [{
                'steps': [
                    {
                        'instruction': 'Head east on I-10',
                        'distance': 1500.0,
                        'duration': 45.0,
                        'name': 'I-10',
                        'type': 11,
                        'way_points': [0, 5],
                    },
                ],
            }],
        }],
    }


class _StubResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.text = '' if json_data is None else 'stub'

    def json(self):
        return self._json


@override_settings(MAP_ROUTING_API_KEY='test-key', MAP_ROUTING_API_BASE_URL='https://example.test')
class RoutingServiceTest(TestCase):
    def setUp(self):
        cache.clear()
        self.service = RoutingService()
        self.start = (34.0522, -118.2437)
        self.end = (40.7128, -74.0060)

    def test_get_route_calls_ors_and_caches(self):
        with patch('api.routing.requests.post',
                   return_value=_StubResponse(_ors_payload())) as mock_post:
            first = self.service.get_route(self.start, self.end)

        self.assertEqual(mock_post.call_count, 1)
        self.assertEqual(first['source'], 'openrouteservice')
        self.assertAlmostEqual(first['total_distance_miles'], 2796.171, places=1)
        self.assertEqual(first['estimated_duration'], 151200.0)
        self.assertEqual(first['polyline_encoded'], 'abc123')
        self.assertEqual(len(first['steps']), 1)
        self.assertEqual(first['steps'][0]['name'], 'I-10')

        with patch('api.routing.requests.post') as mock_post2:
            second = self.service.get_route(self.start, self.end)
        self.assertEqual(mock_post2.call_count, 0)
        self.assertEqual(second['source'], 'cache')

    def test_get_route_sends_authorization_and_lng_lat_order(self):
        with patch('api.routing.requests.post',
                   return_value=_StubResponse(_ors_payload())) as mock_post:
            self.service.get_route(self.start, self.end)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs['headers']['Authorization'], 'test-key')
        coords = kwargs['json']['coordinates']
        self.assertAlmostEqual(coords[0][0], -118.2437)
        self.assertAlmostEqual(coords[0][1], 34.0522)

    def test_get_route_http_error_raises(self):
        with patch('api.routing.requests.post',
                   return_value=_StubResponse(None, status_code=500)):
            with self.assertRaises(RoutingError):
                self.service.get_route(self.start, self.end)

    def test_get_route_network_error_raises(self):
        with patch('api.routing.requests.post', side_effect=requests.ConnectionError('boom')):
            with self.assertRaises(RoutingError):
                self.service.get_route(self.start, self.end)

    def test_get_route_invalid_coords_raises(self):
        with self.assertRaises(RoutingError):
            self.service.get_route((91, 0), self.end)

    def test_get_route_empty_routes_array_raises(self):
        with patch('api.routing.requests.post',
                   return_value=_StubResponse({'routes': []})):
            with self.assertRaises(RoutingError):
                self.service.get_route(self.start, self.end)


@override_settings(MAP_ROUTING_API_KEY='', MAP_ROUTING_API_BASE_URL='https://example.test')
class RoutingServiceMissingKeyTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_missing_key_raises_before_network(self):
        service = RoutingService()
        with patch('api.routing.requests.post') as mock_post:
            with self.assertRaises(RoutingError):
                service.get_route((34.0, -118.0), (40.0, -74.0))
        self.assertEqual(mock_post.call_count, 0)
