from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from api.models import FuelPrice


def _stub_geocode(address):
    table = {
        'los angeles, ca': (34.0522, -118.2437),
        'new york, ny': (40.7128, -74.0060),
        'phoenix, az': (33.4484, -112.0740),
    }
    lat, lng = table[address.strip().lower()]
    return {'lat': lat, 'lng': lng, 'display_name': address, 'source': 'nominatim'}


def _stub_route(start_coords, end_coords):
    return {
        'total_distance_miles': 2796.0,
        'estimated_duration': 151200.0,
        'polyline_encoded': 'g~vlEnjpiU_seed',
        'steps': [],
        'source': 'openrouteservice',
    }


def _stub_polyline_decode(_encoded):
    return [
        (34.0522 + (40.7128 - 34.0522) * i / 10,
         -118.2437 + (-74.0060 + 118.2437) * i / 10)
        for i in range(11)
    ]


@override_settings(
    MAP_ROUTING_API_KEY='test-key',
    REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': [],
        'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
        'DEFAULT_THROTTLE_CLASSES': [],
        'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    },
)
class OptimizeViewTest(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.url = reverse('optimize-fuel-route')

        for i, (lat, lng) in enumerate(_stub_polyline_decode('')[1:-1], start=1):
            FuelPrice.objects.create(
                city=f'Stop{i}', state='XX',
                latitude=Decimal(f'{lat:.6f}'),
                longitude=Decimal(f'{lng:.6f}'),
                regular_price=Decimal(f'{3.20 + i * 0.03:.3f}'),
            )

    def test_happy_path_returns_full_plan(self):
        with patch('api.views.GeocodingService') as Geo, \
             patch('api.views.RoutingService') as Route, \
             patch('api.views.polyline_lib') as PL:
            Geo.return_value.geocode.side_effect = _stub_geocode
            Route.return_value.get_route.side_effect = _stub_route
            PL.decode.side_effect = _stub_polyline_decode

            response = self.client.post(self.url, {
                'start_address': 'Los Angeles, CA',
                'end_address': 'New York, NY',
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn('route_polyline', body)
        self.assertAlmostEqual(body['total_distance_miles'], 2796.0, places=1)
        self.assertGreater(len(body['stops']), 0)
        self.assertGreater(body['total_cost'], 0)
        self.assertFalse(body['cached'])

        for stop in body['stops']:
            self.assertIn('name', stop)
            self.assertIn('lat', stop)
            self.assertIn('lng', stop)
            self.assertIn('price_per_gallon', stop)
            self.assertIn('cost_for_stop', stop)

    def test_response_cache_short_circuits_second_call(self):
        with patch('api.views.GeocodingService') as Geo, \
             patch('api.views.RoutingService') as Route, \
             patch('api.views.polyline_lib') as PL:
            Geo.return_value.geocode.side_effect = _stub_geocode
            Route.return_value.get_route.side_effect = _stub_route
            PL.decode.side_effect = _stub_polyline_decode

            first = self.client.post(self.url, {
                'start_address': 'Los Angeles, CA',
                'end_address': 'New York, NY',
            }, format='json')
            self.assertEqual(first.status_code, status.HTTP_200_OK)

            Geo.return_value.geocode.reset_mock()
            Route.return_value.get_route.reset_mock()
            PL.decode.reset_mock()

            second = self.client.post(self.url, {
                'start_address': 'Los Angeles, CA',
                'end_address': 'New York, NY',
            }, format='json')

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.json()['cached'])
        Geo.return_value.geocode.assert_not_called()
        Route.return_value.get_route.assert_not_called()

    def test_equal_addresses_returns_400(self):
        response = self.client.post(self.url, {
            'start_address': 'Boston, MA',
            'end_address': 'Boston, MA',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_field_returns_400(self):
        response = self.client.post(self.url, {
            'start_address': 'Boston, MA',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_geocoding_failure_returns_400(self):
        from api.geocoding import GeocodingError
        with patch('api.views.GeocodingService') as Geo:
            Geo.return_value.geocode.side_effect = GeocodingError('nope')
            response = self.client.post(self.url, {
                'start_address': 'asdfqwer',
                'end_address': 'New York, NY',
            }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()['error'], 'geocoding_failed')

    def test_routing_failure_returns_502(self):
        from api.routing import RoutingError
        with patch('api.views.GeocodingService') as Geo, \
             patch('api.views.RoutingService') as Route:
            Geo.return_value.geocode.side_effect = _stub_geocode
            Route.return_value.get_route.side_effect = RoutingError('ORS down')
            response = self.client.post(self.url, {
                'start_address': 'Los Angeles, CA',
                'end_address': 'New York, NY',
            }, format='json')
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.json()['error'], 'routing_failed')

    def test_no_feasible_plan_returns_422_with_route(self):
        FuelPrice.objects.all().delete()

        with patch('api.views.GeocodingService') as Geo, \
             patch('api.views.RoutingService') as Route, \
             patch('api.views.polyline_lib') as PL:
            Geo.return_value.geocode.side_effect = _stub_geocode
            Route.return_value.get_route.side_effect = _stub_route
            PL.decode.side_effect = _stub_polyline_decode

            response = self.client.post(self.url, {
                'start_address': 'Los Angeles, CA',
                'end_address': 'New York, NY',
            }, format='json')

        self.assertEqual(response.status_code, status.HTTP_422_UNPROCESSABLE_ENTITY)
        body = response.json()
        self.assertEqual(body['error'], 'no_feasible_plan')
        self.assertIn('route_polyline', body)
        self.assertIn('total_distance_miles', body)
