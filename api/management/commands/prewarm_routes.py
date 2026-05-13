from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand

from api.geocoding import GeocodingError, GeocodingService
from api.routing import RoutingError, RoutingService


class Command(BaseCommand):
    help = 'Warm geocode + route caches for common city pairs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pairs',
            nargs='+',
            default=None,
            help='Pipe-delimited "Start|End" pairs (overrides settings.PREWARM_ROUTES).',
        )
        parser.add_argument(
            '--profile',
            default='driving-car',
            help='OpenRouteService profile (default: driving-car).',
        )

    def handle(self, *args, **opts):
        pairs = self._parse_pairs(opts['pairs']) or getattr(settings, 'PREWARM_ROUTES', [])
        if not pairs:
            self.stdout.write(self.style.WARNING(
                'No pairs to warm. Set PREWARM_ROUTES in settings or pass --pairs.'
            ))
            return

        geocoder = GeocodingService()
        router = RoutingService(profile=opts['profile'])

        warmed = skipped = 0
        started = time.monotonic()
        for start_address, end_address in pairs:
            label = f'{start_address!r} → {end_address!r}'
            try:
                origin = geocoder.geocode(start_address)
                destination = geocoder.geocode(end_address)
            except GeocodingError as exc:
                self.stdout.write(self.style.WARNING(
                    f'  skip {label}: geocoding failed ({exc})'
                ))
                skipped += 1
                continue

            try:
                route = router.get_route(
                    (origin['lat'], origin['lng']),
                    (destination['lat'], destination['lng']),
                )
            except RoutingError as exc:
                self.stdout.write(self.style.WARNING(
                    f'  skip {label}: routing failed ({exc})'
                ))
                skipped += 1
                continue

            tag = '(cache)' if route.get('source') == 'cache' else '(fresh)'
            self.stdout.write(
                f'  warmed {label}  {route["total_distance_miles"]:.0f} mi {tag}'
            )
            warmed += 1

        elapsed = time.monotonic() - started
        self.stdout.write(self.style.SUCCESS(
            f'Done in {elapsed:.1f}s: {warmed} warmed, {skipped} skipped.'
        ))

    @staticmethod
    def _parse_pairs(raw: list[str] | None) -> list[tuple[str, str]] | None:
        if not raw:
            return None
        pairs: list[tuple[str, str]] = []
        for entry in raw:
            if '|' not in entry:
                raise ValueError(f'Bad --pairs entry (expected "A|B"): {entry!r}')
            start, end = entry.split('|', 1)
            pairs.append((start.strip(), end.strip()))
        return pairs
