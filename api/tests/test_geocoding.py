from __future__ import annotations

from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase

from api.geocoding import GeocodingError, GeocodingService


class _StubResponse:
    def __init__(self, *, json_data=None, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'HTTP {self.status_code}')


class GeocodingServiceTest(TestCase):
    def setUp(self):
        cache.clear()
        GeocodingService._last_request_at = 0.0
        self.service = GeocodingService(request_interval=0.0)

    def test_geocode_calls_nominatim_and_caches(self):
        payload = [{
            'lat': '40.7128', 'lon': '-74.006',
            'display_name': 'New York, NY, USA',
        }]
        with patch('api.geocoding.requests.get', return_value=_StubResponse(json_data=payload)) as mock_get:
            first = self.service.geocode('New York, NY')

        self.assertEqual(mock_get.call_count, 1)
        self.assertAlmostEqual(first['lat'], 40.7128)
        self.assertAlmostEqual(first['lng'], -74.006)
        self.assertEqual(first['source'], 'nominatim')

        with patch('api.geocoding.requests.get') as mock_get2:
            second = self.service.geocode('New York, NY')
        self.assertEqual(mock_get2.call_count, 0)
        self.assertEqual(second['source'], 'cache')

    def test_geocode_empty_address_raises(self):
        with self.assertRaises(GeocodingError):
            self.service.geocode('   ')

    def test_geocode_no_results_raises(self):
        with patch('api.geocoding.requests.get', return_value=_StubResponse(json_data=[])):
            with self.assertRaises(GeocodingError):
                self.service.geocode('asdfqwer zzz nowhere')

    def test_geocode_falls_back_to_mock_on_network_error(self):
        with patch('api.geocoding.requests.get', side_effect=requests.ConnectionError('boom')):
            result = self.service.geocode('Los Angeles')

        self.assertEqual(result['source'], 'mock')
        self.assertAlmostEqual(result['lat'], 34.0522, places=3)
        self.assertAlmostEqual(result['lng'], -118.2437, places=3)

    def test_reverse_geocode_validates_range(self):
        with self.assertRaises(GeocodingError):
            self.service.reverse_geocode(91.0, 0.0)
        with self.assertRaises(GeocodingError):
            self.service.reverse_geocode(0.0, -181.0)

    def test_reverse_geocode_caches(self):
        payload = {'display_name': '1 Apple Park Way, Cupertino, CA'}
        with patch('api.geocoding.requests.get', return_value=_StubResponse(json_data=payload)) as mock_get:
            first = self.service.reverse_geocode(37.3349, -122.009)
            second = self.service.reverse_geocode(37.3349, -122.009)
        self.assertEqual(mock_get.call_count, 1)
        self.assertEqual(first['source'], 'nominatim')
        self.assertEqual(second['source'], 'cache')
