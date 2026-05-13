from __future__ import annotations
import logging
from typing import Iterable
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

ROUTE_CACHE_TIMEOUT_SECONDS = 60 * 60 * 24
REQUEST_TIMEOUT_SECONDS = 15
METERS_PER_MILE = 1609.344
DEFAULT_PROFILE = 'driving-car'

Coord = tuple[float, float]


class RoutingError(Exception):
    pass


class RoutingService:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        profile: str = DEFAULT_PROFILE,
        cache_timeout: int = ROUTE_CACHE_TIMEOUT_SECONDS,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.api_key = api_key or settings.MAP_ROUTING_API_KEY
        self.base_url = (base_url or settings.MAP_ROUTING_API_BASE_URL).rstrip('/')
        self.profile = profile
        self.cache_timeout = cache_timeout
        self.timeout = timeout

    def get_route(self, start_coords: Coord, end_coords: Coord) -> dict:
        start = self._normalize_coord(start_coords)
        end = self._normalize_coord(end_coords)

        cache_key = (
            f'route:{start[0]:.6f},{start[1]:.6f}'
            f':{end[0]:.6f},{end[1]:.6f}'
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return {**cached, 'source': 'cache'}

        if not self.api_key:
            raise RoutingError(
                'MAP_ROUTING_API_KEY is not configured. Sign up at '
                'https://openrouteservice.org/dev/#/signup and set the key '
                'in your .env file.'
            )
        payload = {
            'coordinates': [[start[1], start[0]], [end[1], end[0]]],
            'instructions': True,
            'units': 'm',
        }
        url = f'{self.base_url}/v2/directions/{self.profile}'

        try:
            response = requests.post(
                url,
                json=payload,
                headers={
                    'Authorization': self.api_key,
                    'Accept': 'application/json, application/geo+json',
                    'Content-Type': 'application/json',
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            logger.warning('OpenRouteService request failed: %s', exc)
            raise RoutingError(f'Routing service unreachable: {exc}') from exc

        if response.status_code >= 400:
            raise RoutingError(
                f'OpenRouteService returned {response.status_code}: {response.text[:200]}'
            )
        result = self._parse_response(response.json())
        cache.set(cache_key, result, self.cache_timeout)
        return {**result, 'source': 'openrouteservice'}

    @staticmethod
    def _normalize_coord(coord: Iterable[float]) -> Coord:
        try:
            lat, lng = (float(x) for x in coord)
        except (TypeError, ValueError) as exc:
            raise RoutingError(
                'Coordinates must be an iterable of two numbers (lat, lng).'
            ) from exc
        if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lng <= 180.0):
            raise RoutingError(f'Coordinates out of range: ({lat}, {lng})')
        return (round(lat, 6), round(lng, 6))

    @staticmethod
    def _parse_response(body: dict) -> dict:
        routes = body.get('routes') or []
        if not routes:
            raise RoutingError('OpenRouteService returned no routes.')
        route = routes[0]

        summary = route.get('summary') or {}
        distance_m = float(summary.get('distance', 0.0))
        duration_s = float(summary.get('duration', 0.0))

        steps: list[dict] = []
        for segment in route.get('segments', []):
            for step in segment.get('steps', []):
                steps.append({
                    'instruction': step.get('instruction', ''),
                    'distance_meters': step.get('distance', 0.0),
                    'duration_seconds': step.get('duration', 0.0),
                    'name': step.get('name', ''),
                    'type': step.get('type'),
                    'way_points': step.get('way_points', []),
                })

        return {
            'total_distance_miles': round(distance_m / METERS_PER_MILE, 3),
            'estimated_duration': duration_s,
            'polyline_encoded': route.get('geometry', ''),
            'steps': steps,
        }
