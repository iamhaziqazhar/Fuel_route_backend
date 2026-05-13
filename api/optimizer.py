from __future__ import annotations
import math
from decimal import Decimal
from typing import Sequence
from django.db.models import Q
from .models import FuelPrice

EARTH_RADIUS_MILES = 3958.7613
MILES_PER_LAT_DEGREE = 69.0

LatLng = tuple[float, float]


class OptimizationError(Exception):
    pass


class FuelStopOptimizer:
    def __init__(
        self,
        vehicle_range_miles: float = 500.0,
        mpg: float = 10.0,
        corridor_miles: float = 50.0,
        safety_margin_miles: float = 10.0,
        min_advance_fraction: float = 0.5,
    ) -> None:
        self.vehicle_range_miles = float(vehicle_range_miles)
        self.mpg = float(mpg)
        self.corridor_miles = float(corridor_miles)
        self.safety_margin_miles = float(safety_margin_miles)
        self.min_advance_fraction = float(min_advance_fraction)

    def optimize(
        self,
        route_polyline: Sequence[LatLng],
        total_distance_miles: float,
    ) -> dict:
        if len(route_polyline) < 2:
            raise OptimizationError('Route polyline must have at least two points.')
        if total_distance_miles <= 0:
            raise OptimizationError('Total distance must be positive.')

        polyline = [(float(lat), float(lng)) for lat, lng in route_polyline]
        cum = _cumulative_haversine_miles(polyline)
        scale = total_distance_miles / cum[-1] if cum[-1] > 0 else 1.0
        cum = [c * scale for c in cum]

        position_miles = 0.0
        remaining_range = self.vehicle_range_miles
        stops: list[dict] = []
        visited_station_ids: set[int] = set()

        min_advance = max(
            self.min_advance_fraction * (self.vehicle_range_miles - self.safety_margin_miles),
            1.0,
        )
        max_stops = int(total_distance_miles / min_advance) + 5

        while total_distance_miles - position_miles > remaining_range:
            if len(stops) >= max_stops:
                raise OptimizationError(
                    f'Exceeded max stops ({max_stops}); no feasible plan found.'
                )

            station = self._pick_station(
                polyline, cum, position_miles, remaining_range, visited_station_ids
            )
            if station is None:
                raise OptimizationError(
                    f'No reachable fuel station within {self.corridor_miles:.0f} mi of '
                    f'route between mile {position_miles:.1f} and '
                    f'{position_miles + remaining_range:.1f}.'
                )

            distance_since_last = station['along_route_miles'] - position_miles
            if distance_since_last <= 0:
                raise OptimizationError(
                    'Optimizer would not advance along the route; aborting.'
                )

            gallons = distance_since_last / self.mpg
            cost = gallons * float(station['price_per_gallon'])

            stops.append({
                'station_id': station['id'],
                'city': station['city'],
                'state': station['state'],
                'lat': station['lat'],
                'lng': station['lng'],
                'price_per_gallon': float(station['price_per_gallon']),
                'distance_from_previous_miles': round(distance_since_last, 2),
                'detour_miles': round(station['detour_miles'], 2),
                'gallons': round(gallons, 3),
                'cost': round(cost, 2),
                'is_safety_stop': station['is_safety_stop'],
            })
            visited_station_ids.add(station['id'])
            position_miles = station['along_route_miles']
            remaining_range = self.vehicle_range_miles

        final_leg_miles = max(total_distance_miles - position_miles, 0.0)
        return {
            'stops': stops,
            'total_stops': len(stops),
            'total_cost': round(sum(s['cost'] for s in stops), 2),
            'total_gallons': round(sum(s['gallons'] for s in stops), 3),
            'final_leg_miles': round(final_leg_miles, 2),
        }

    def _pick_station(
        self,
        polyline: list[LatLng],
        cum: list[float],
        position_miles: float,
        remaining_range: float,
        visited: set[int],
    ) -> dict | None:
        usable_range = max(remaining_range - self.safety_margin_miles, 0.0)
        look_end = min(position_miles + usable_range, cum[-1])
        min_advance = self.min_advance_fraction * usable_range
        prefer_from = position_miles + min_advance

        seg_idx = [i for i, c in enumerate(cum) if position_miles <= c <= look_end]
        if not seg_idx:
            seg_idx = _bracket_indices(cum, position_miles, look_end)
            if not seg_idx:
                return None

        seg_points = [polyline[i] for i in seg_idx]
        seg_cums = [cum[i] for i in seg_idx]

        candidates = self._query_stations(seg_points, visited)

        best_far: dict | None = None
        best_any: dict | None = None
        for station in candidates:
            match = _project_station(station, seg_points, seg_cums)
            if match is None:
                continue
            detour, along_route, _ = match
            if detour > self.corridor_miles:
                continue
            if along_route <= position_miles:
                continue
            if along_route - position_miles > usable_range:
                continue

            entry = {
                'id': station.id,
                'city': station.city,
                'state': station.state,
                'lat': float(station.latitude),
                'lng': float(station.longitude),
                'price_per_gallon': station.price_per_gallon,
                'along_route_miles': along_route,
                'detour_miles': detour,
                'is_safety_stop': False,
            }
            if along_route >= prefer_from and _is_better(entry, best_far):
                best_far = entry
            if _is_better(entry, best_any):
                best_any = entry

        if best_far is not None:
            return best_far
        if best_any is not None:
            return best_any
        return self._safety_stop(seg_points, seg_cums, position_miles, usable_range, visited)

    def _query_stations(
        self,
        seg_points: list[LatLng],
        visited: set[int],
    ) -> list[FuelPrice]:
        lats = [p[0] for p in seg_points]
        lngs = [p[1] for p in seg_points]
        avg_lat = sum(lats) / len(lats)

        lat_pad = self.corridor_miles / MILES_PER_LAT_DEGREE
        lng_pad = self.corridor_miles / max(
            MILES_PER_LAT_DEGREE * math.cos(math.radians(avg_lat)), 1e-6
        )

        qs = FuelPrice.objects.filter(
            latitude__gte=Decimal(str(min(lats) - lat_pad)),
            latitude__lte=Decimal(str(max(lats) + lat_pad)),
            longitude__gte=Decimal(str(min(lngs) - lng_pad)),
            longitude__lte=Decimal(str(max(lngs) + lng_pad)),
            price_per_gallon__isnull=False,
        )
        if visited:
            qs = qs.exclude(id__in=visited)
        return list(qs.only(
            'id', 'city', 'state', 'latitude', 'longitude', 'price_per_gallon'
        ))

    def _safety_stop(
        self,
        seg_points: list[LatLng],
        seg_cums: list[float],
        position_miles: float,
        usable_range: float,
        visited: set[int],
    ) -> dict | None:
        wide_corridor = self.corridor_miles * 3
        lats = [p[0] for p in seg_points]
        lngs = [p[1] for p in seg_points]
        avg_lat = sum(lats) / len(lats)
        lat_pad = wide_corridor / MILES_PER_LAT_DEGREE
        lng_pad = wide_corridor / max(
            MILES_PER_LAT_DEGREE * math.cos(math.radians(avg_lat)), 1e-6
        )

        qs = FuelPrice.objects.filter(
            latitude__gte=Decimal(str(min(lats) - lat_pad)),
            latitude__lte=Decimal(str(max(lats) + lat_pad)),
            longitude__gte=Decimal(str(min(lngs) - lng_pad)),
            longitude__lte=Decimal(str(max(lngs) + lng_pad)),
        ).filter(Q(price_per_gallon__isnull=False) | Q(regular_price__isnull=False))
        if visited:
            qs = qs.exclude(id__in=visited)

        best: dict | None = None
        for st in qs.only(
            'id', 'city', 'state', 'latitude', 'longitude',
            'price_per_gallon', 'regular_price',
        ):
            price = st.price_per_gallon or st.regular_price
            if price is None:
                continue
            match = _project_station(st, seg_points, seg_cums)
            if match is None:
                continue
            detour, along_route, _ = match
            if along_route <= position_miles:
                continue
            if along_route - position_miles > usable_range:
                continue

            entry = {
                'id': st.id,
                'city': st.city,
                'state': st.state,
                'lat': float(st.latitude),
                'lng': float(st.longitude),
                'price_per_gallon': price,
                'along_route_miles': along_route,
                'detour_miles': detour,
                'is_safety_stop': True,
            }
            if best is None or (entry['detour_miles'], price) < (
                best['detour_miles'], best['price_per_gallon']
            ):
                best = entry
        return best


def _cumulative_haversine_miles(polyline: list[LatLng]) -> list[float]:
    cum = [0.0]
    for i in range(1, len(polyline)):
        d = _haversine_miles(*polyline[i - 1], *polyline[i])
        cum.append(cum[-1] + d)
    return cum


def _haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def _bracket_indices(cum: list[float], lo: float, hi: float) -> list[int]:
    below = [i for i, c in enumerate(cum) if c <= lo]
    above = [i for i, c in enumerate(cum) if c >= hi]
    if not below or not above:
        return []
    return [below[-1], above[0]]


def _project_station(
    station: FuelPrice,
    seg_points: list[LatLng],
    seg_cums: list[float],
) -> tuple[float, float, int] | None:
    if not seg_points:
        return None
    st_lat, st_lng = float(station.latitude), float(station.longitude)
    best_idx = 0
    best_dist = _haversine_miles(st_lat, st_lng, *seg_points[0])
    for i in range(1, len(seg_points)):
        d = _haversine_miles(st_lat, st_lng, *seg_points[i])
        if d < best_dist:
            best_dist = d
            best_idx = i
    return best_dist, seg_cums[best_idx], best_idx


def _is_better(candidate: dict, best: dict | None) -> bool:
    if best is None:
        return True
    if candidate['price_per_gallon'] < best['price_per_gallon']:
        return True
    if candidate['price_per_gallon'] == best['price_per_gallon']:
        return candidate['along_route_miles'] > best['along_route_miles']
    return False
