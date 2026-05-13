from __future__ import annotations

from decimal import Decimal

from django.test import TestCase

from api.models import FuelPrice
from api.optimizer import FuelStopOptimizer, OptimizationError


def _make_station(*, city, state, lat, lng, price):
    return FuelPrice.objects.create(
        city=city, state=state,
        latitude=Decimal(f'{lat:.6f}'),
        longitude=Decimal(f'{lng:.6f}'),
        regular_price=Decimal(f'{price:.3f}'),
    )


def _straight_line_polyline(start, end, steps=20):
    lat1, lng1 = start
    lat2, lng2 = end
    return [
        (lat1 + (lat2 - lat1) * i / steps,
         lng1 + (lng2 - lng1) * i / steps)
        for i in range(steps + 1)
    ]


class FuelStopOptimizerTest(TestCase):
    def test_optimize_short_trip_returns_zero_stops(self):
        polyline = _straight_line_polyline((34.05, -118.24), (34.05, -116.74))
        plan = FuelStopOptimizer().optimize(polyline, total_distance_miles=100.0)

        self.assertEqual(plan['total_stops'], 0)
        self.assertEqual(plan['total_cost'], 0.0)
        self.assertEqual(plan['stops'], [])
        self.assertAlmostEqual(plan['final_leg_miles'], 100.0)

    def test_optimize_empty_polyline_raises(self):
        with self.assertRaises(OptimizationError):
            FuelStopOptimizer().optimize([(0.0, 0.0)], total_distance_miles=10.0)

    def test_optimize_zero_distance_raises(self):
        polyline = _straight_line_polyline((34.0, -118.0), (40.0, -74.0))
        with self.assertRaises(OptimizationError):
            FuelStopOptimizer().optimize(polyline, total_distance_miles=0)

    def test_optimize_no_stations_raises(self):
        polyline = _straight_line_polyline((34.05, -118.24), (40.71, -74.01))
        with self.assertRaises(OptimizationError):
            FuelStopOptimizer().optimize(polyline, total_distance_miles=2800.0)

    def test_optimize_picks_cheaper_of_two_in_far_half(self):
        polyline = _straight_line_polyline((34.0, -118.0), (34.0, -108.0))
        _make_station(city='Expensive', state='AZ',
                      lat=34.0, lng=-112.5, price=4.50)
        cheaper = _make_station(city='Cheap', state='AZ',
                                lat=34.0, lng=-113.0, price=3.10)

        plan = FuelStopOptimizer(vehicle_range_miles=500.0).optimize(
            polyline, total_distance_miles=575.0
        )

        self.assertEqual(plan['total_stops'], 1)
        self.assertEqual(plan['stops'][0]['station_id'], cheaper.id)
        self.assertAlmostEqual(plan['stops'][0]['price_per_gallon'], 3.10, places=3)

    def test_optimize_total_cost_matches_gallons_times_price(self):
        polyline = _straight_line_polyline((34.0, -118.0), (34.0, -108.0))
        _make_station(city='Mid', state='AZ',
                      lat=34.0, lng=-113.0, price=3.50)

        plan = FuelStopOptimizer(vehicle_range_miles=500.0, mpg=10.0).optimize(
            polyline, total_distance_miles=575.0
        )

        self.assertEqual(plan['total_stops'], 1)
        s = plan['stops'][0]
        self.assertAlmostEqual(s['cost'], round(s['gallons'] * s['price_per_gallon'], 2), places=2)
        self.assertAlmostEqual(plan['total_cost'], s['cost'])
        self.assertAlmostEqual(plan['total_gallons'], s['gallons'])

    def test_optimize_advances_position_each_stop(self):
        polyline = _straight_line_polyline((34.0, -118.0), (34.0, -100.0))
        for i, lng in enumerate([-114.5, -110.0, -105.5]):
            _make_station(city=f'Stop{i}', state='AZ',
                          lat=34.0, lng=lng, price=3.40 + i * 0.05)

        plan = FuelStopOptimizer(vehicle_range_miles=500.0).optimize(
            polyline, total_distance_miles=1035.0
        )

        self.assertGreaterEqual(plan['total_stops'], 2)
        for stop in plan['stops']:
            self.assertGreater(stop['distance_from_previous_miles'], 0)
