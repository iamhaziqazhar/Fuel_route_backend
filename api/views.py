from __future__ import annotations
import hashlib
import logging
import polyline as polyline_lib
from django.core.cache import cache
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from .geocoding import GeocodingError, GeocodingService
from .optimizer import FuelStopOptimizer, OptimizationError
from .routing import RoutingError, RoutingService
from .serializers import OptimizeRequestSerializer, OptimizeResponseSerializer

logger = logging.getLogger(__name__)

RESPONSE_CACHE_TTL_SECONDS = 60 * 60


class OptimizeFuelRouteView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        req = OptimizeRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        start_address = req.validated_data['start_address']
        end_address = req.validated_data['end_address']

        cache_key = _response_cache_key(start_address, end_address)
        cached = cache.get(cache_key)
        if cached is not None:
            return Response({**cached, 'cached': True}, status=status.HTTP_200_OK)

        geocoder = GeocodingService()
        try:
            origin = geocoder.geocode(start_address)
        except GeocodingError as exc:
            return _error(
                'geocoding_failed',
                f'Could not geocode start_address: {exc}',
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            destination = geocoder.geocode(end_address)
        except GeocodingError as exc:
            return _error(
                'geocoding_failed',
                f'Could not geocode end_address: {exc}',
                status.HTTP_400_BAD_REQUEST,
            )
        try:
            route = RoutingService().get_route(
                (origin['lat'], origin['lng']),
                (destination['lat'], destination['lng']),
            )
        except RoutingError as exc:
            logger.warning('routing failed for %r → %r: %s', start_address, end_address, exc)
            return _error(
                'routing_failed',
                str(exc),
                status.HTTP_502_BAD_GATEWAY,
            )
        try:
            polyline_points = polyline_lib.decode(route['polyline_encoded'])
        except Exception as exc:
            logger.exception('failed to decode polyline')
            return _error(
                'polyline_decode_failed',
                f'Routing service returned an unreadable polyline: {exc}',
                status.HTTP_502_BAD_GATEWAY,
            )

        try:
            plan = FuelStopOptimizer().optimize(
                polyline_points, route['total_distance_miles']
            )
        except OptimizationError as exc:
            return Response(
                {
                    'error': 'no_feasible_plan',
                    'message': str(exc),
                    'route_polyline': route['polyline_encoded'],
                    'total_distance_miles': route['total_distance_miles'],
                    'start': _endpoint_summary(start_address, origin),
                    'end': _endpoint_summary(end_address, destination),
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        body = {
            'route_polyline': route['polyline_encoded'],
            'total_distance_miles': route['total_distance_miles'],
            'estimated_duration_seconds': route['estimated_duration'],
            'stops': [_format_stop(s) for s in plan['stops']],
            'total_cost': plan['total_cost'],
            'total_fuel_used_gallons': plan['total_gallons'],
            'final_leg_miles': plan['final_leg_miles'],
            'start': _endpoint_summary(start_address, origin),
            'end': _endpoint_summary(end_address, destination),
        }
        out = OptimizeResponseSerializer(body)
        payload = out.data

        cache.set(cache_key, payload, RESPONSE_CACHE_TTL_SECONDS)
        return Response({**payload, 'cached': False}, status=status.HTTP_200_OK)


def _response_cache_key(start: str, end: str) -> str:
    digest = hashlib.sha1(
        f'{start.strip().lower()}|{end.strip().lower()}'.encode('utf-8')
    ).hexdigest()
    return f'optimize:{digest}'


def _endpoint_summary(query: str, geocoded: dict) -> dict:
    return {
        'query': query,
        'lat': geocoded['lat'],
        'lng': geocoded['lng'],
        'display_name': geocoded.get('display_name', ''),
    }


def _format_stop(stop: dict) -> dict:
    return {
        'name': stop.get('city') or 'Unknown',
        'address': f'{stop.get("city", "Unknown")}, {stop.get("state", "")}'.strip(', '),
        'lat': stop['lat'],
        'lng': stop['lng'],
        'price_per_gallon': stop['price_per_gallon'],
        'cost_for_stop': stop['cost'],
        'distance_from_previous_miles': stop['distance_from_previous_miles'],
        'gallons': stop['gallons'],
        'is_safety_stop': stop['is_safety_stop'],
    }


def _error(code: str, message: str, http_status: int) -> Response:
    return Response({'error': code, 'message': message}, status=http_status)
