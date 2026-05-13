from __future__ import annotations
import hashlib
import logging
import threading
import time
from typing import Optional
import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

NOMINATIM_BASE_URL = 'https://nominatim.openstreetmap.org'
USER_AGENT = 'FuelRouteOptimizer/1.0 (contact: haziqazhar1m@gmail.com)'
CACHE_TIMEOUT_SECONDS = 60 * 60 * 24 * 30
REQUEST_INTERVAL_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 10


_MOCK_FORWARD = {
    'new york': (40.7128, -74.0060),
    'new york, ny': (40.7128, -74.0060),
    'los angeles': (34.0522, -118.2437),
    'los angeles, ca': (34.0522, -118.2437),
    'chicago': (41.8781, -87.6298),
    'chicago, il': (41.8781, -87.6298),
    'houston': (29.7604, -95.3698),
    'houston, tx': (29.7604, -95.3698),
    'phoenix': (33.4484, -112.0740),
    'phoenix, az': (33.4484, -112.0740),
    'dallas': (32.7767, -96.7970),
    'dallas, tx': (32.7767, -96.7970),
    'atlanta': (33.7490, -84.3880),
    'atlanta, ga': (33.7490, -84.3880),
    'denver': (39.7392, -104.9903),
    'denver, co': (39.7392, -104.9903),
}


class GeocodingError(Exception):
    pass


class GeocodingService:
    _rate_limit_lock = threading.Lock()
    _last_request_at = 0.0

    def __init__(
        self,
        base_url: str = NOMINATIM_BASE_URL,
        user_agent: str = USER_AGENT,
        cache_timeout: int = CACHE_TIMEOUT_SECONDS,
        request_interval: float = REQUEST_INTERVAL_SECONDS,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip('/')
        self.user_agent = user_agent
        self.cache_timeout = cache_timeout
        self.request_interval = request_interval
        self.timeout = timeout

    def geocode(self, address: str) -> dict:
        if not address or not address.strip():
            raise GeocodingError('Address must be a non-empty string.')

        normalized = address.strip()
        cache_key = self._cache_key('fwd', normalized.lower())
        cached = cache.get(cache_key)
        if cached is not None:
            return {**cached, 'source': 'cache'}

        try:
            data = self._request(
                '/search',
                {'q': normalized, 'format': 'json', 'limit': 1, 'addressdetails': 0},
            )
        except requests.RequestException as exc:
            logger.warning('Nominatim geocode failed for %r: %s', normalized, exc)
            return self._mock_geocode(normalized)

        if not data:
            raise GeocodingError(f'No results found for address: {normalized!r}')

        result = {
            'lat': float(data[0]['lat']),
            'lng': float(data[0]['lon']),
            'display_name': data[0].get('display_name', normalized),
        }
        cache.set(cache_key, result, self.cache_timeout)
        return {**result, 'source': 'nominatim'}

    def reverse_geocode(self, lat: float, lng: float) -> dict:
        try:
            lat_f = float(lat)
            lng_f = float(lng)
        except (TypeError, ValueError) as exc:
            raise GeocodingError('Latitude and longitude must be numeric.') from exc

        if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lng_f <= 180.0):
            raise GeocodingError('Coordinates out of range.')

        cache_key = self._cache_key('rev', f'{lat_f:.6f},{lng_f:.6f}')
        cached = cache.get(cache_key)
        if cached is not None:
            return {**cached, 'source': 'cache'}

        try:
            data = self._request(
                '/reverse',
                {'lat': lat_f, 'lon': lng_f, 'format': 'json', 'zoom': 14},
            )
        except requests.RequestException as exc:
            logger.warning(
                'Nominatim reverse geocode failed for (%s, %s): %s', lat_f, lng_f, exc
            )
            return self._mock_reverse_geocode(lat_f, lng_f)

        if not data or 'display_name' not in data:
            raise GeocodingError(f'No results found for coordinates: ({lat_f}, {lng_f}).')

        result = {
            'lat': lat_f,
            'lng': lng_f,
            'display_name': data['display_name'],
        }
        cache.set(cache_key, result, self.cache_timeout)
        return {**result, 'source': 'nominatim'}

    def _request(self, path: str, params: dict) -> dict | list:
        self._respect_rate_limit()
        response = requests.get(
            f'{self.base_url}{path}',
            params=params,
            headers={'User-Agent': self.user_agent, 'Accept': 'application/json'},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    @classmethod
    def _respect_rate_limit(cls) -> None:
        with cls._rate_limit_lock:
            elapsed = time.monotonic() - cls._last_request_at
            if elapsed < REQUEST_INTERVAL_SECONDS:
                time.sleep(REQUEST_INTERVAL_SECONDS - elapsed)
            cls._last_request_at = time.monotonic()

    @staticmethod
    def _cache_key(prefix: str, value: str) -> str:
        digest = hashlib.sha1(value.encode('utf-8')).hexdigest()
        return f'geocoding:{prefix}:{digest}'

    def _mock_geocode(self, address: str) -> dict:
        key = address.lower().strip()
        if key in _MOCK_FORWARD:
            lat, lng = _MOCK_FORWARD[key]
            return {
                'lat': lat,
                'lng': lng,
                'display_name': f'{address} (mock)',
                'source': 'mock',
            }
        seed = int(hashlib.md5(key.encode('utf-8')).hexdigest()[:8], 16)
        lat = 25.0 + (seed % 2000) / 100.0
        lng = -125.0 + ((seed >> 8) % 5000) / 100.0
        return {
            'lat': lat,
            'lng': lng,
            'display_name': f'{address} (mock approximation)',
            'source': 'mock',
        }

    def _mock_reverse_geocode(self, lat: float, lng: float) -> dict:
        nearest: Optional[tuple[str, float]] = None
        for name, (mlat, mlng) in _MOCK_FORWARD.items():
            distance = (mlat - lat) ** 2 + (mlng - lng) ** 2
            if nearest is None or distance < nearest[1]:
                nearest = (name, distance)
        display = f'Near {nearest[0].title()} (mock)' if nearest else 'Unknown (mock)'
        return {'lat': lat, 'lng': lng, 'display_name': display, 'source': 'mock'}
