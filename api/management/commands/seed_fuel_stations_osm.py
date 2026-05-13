from __future__ import annotations

import random
import time
from decimal import Decimal

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import FuelPrice


OVERPASS_ENDPOINTS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.kumi.systems/api/interpreter',
    'https://overpass.openstreetmap.fr/api/interpreter',
]
DEFAULT_BBOX = '24.5,-125.0,49.4,-66.9'
USER_AGENT = 'FuelRouteOptimizer/1.0 (contact: haziqazhar1m@gmail.com)'

PRICE_BANDS = [
    (-180.0, -118.0, 4.80),
    (-118.0, -104.0, 3.85),
    (-104.0, -90.0, 3.20),
    (-90.0, -80.0, 3.30),
    (-80.0, -65.0, 3.55),
]


class Command(BaseCommand):
    help = 'Seed FuelPrice from OSM Overpass (real coords) + synthetic prices.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bbox', default=DEFAULT_BBOX,
            help=f'lat_min,lng_min,lat_max,lng_max (default CONUS: {DEFAULT_BBOX})',
        )
        parser.add_argument(
            '--limit', type=int, default=5000,
            help='Max stations to insert (after random sampling).',
        )
        parser.add_argument(
            '--seed', type=int, default=42,
            help='Random seed for synthetic prices (reproducible runs).',
        )
        parser.add_argument(
            '--purge', action='store_true',
            help='Delete all existing FuelPrice rows before loading.',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Fetch and parse but do not write to the database.',
        )
        parser.add_argument(
            '--timeout', type=int, default=180,
            help='Overpass query timeout in seconds.',
        )

    def handle(self, *args, **opts):
        bbox = self._parse_bbox(opts['bbox'])
        rng = random.Random(opts['seed'])

        self.stdout.write(f'Fetching gas stations from OSM Overpass for bbox {bbox}…')
        nodes = self._fetch_overpass(bbox, opts['timeout'])
        self.stdout.write(f'  → {len(nodes)} stations returned')

        if len(nodes) > opts['limit']:
            nodes = rng.sample(nodes, opts['limit'])
            self.stdout.write(f'  → sampled down to {len(nodes)}')

        rows = [self._build_row(n, rng) for n in nodes]
        rows = [r for r in rows if r is not None]
        self.stdout.write(f'  → {len(rows)} rows ready to insert')

        if opts['dry_run']:
            self._preview(rows)
            return

        with transaction.atomic():
            if opts['purge']:
                deleted, _ = FuelPrice.objects.all().delete()
                self.stdout.write(f'Purged {deleted} existing FuelPrice rows.')
            created = FuelPrice.objects.bulk_create(rows, ignore_conflicts=True)
            self.stdout.write(self.style.SUCCESS(
                f'Inserted {len(created)} FuelPrice rows '
                f'(conflicts on duplicate lat/lng silently ignored).'
            ))
            total = FuelPrice.objects.count()
            self.stdout.write(f'FuelPrice table now has {total} total rows.')

    def _fetch_overpass(self, bbox: tuple[float, float, float, float], timeout: int) -> list[dict]:
        lat_min, lng_min, lat_max, lng_max = bbox
        query = (
            f'[out:json][timeout:{timeout}];'
            f'node["amenity"="fuel"]({lat_min},{lng_min},{lat_max},{lng_max});'
            f'out body;'
        )

        last_error: Exception | None = None
        for endpoint in OVERPASS_ENDPOINTS:
            try:
                self.stdout.write(f'  trying {endpoint} …')
                resp = requests.post(
                    endpoint,
                    data={'data': query},
                    headers={'User-Agent': USER_AGENT},
                    timeout=timeout,
                )
                if resp.status_code == 429 or resp.status_code == 504:
                    self.stdout.write(self.style.WARNING(
                        f'  {endpoint} returned {resp.status_code}, trying next mirror'
                    ))
                    time.sleep(2)
                    continue
                resp.raise_for_status()
                body = resp.json()
                return [el for el in body.get('elements', []) if el.get('type') == 'node']
            except requests.RequestException as exc:
                last_error = exc
                self.stdout.write(self.style.WARNING(f'  {endpoint} failed: {exc}'))
                continue

        raise CommandError(f'All Overpass mirrors failed. Last error: {last_error}')

    def _build_row(self, node: dict, rng: random.Random) -> FuelPrice | None:
        try:
            lat = float(node['lat'])
            lng = float(node['lon'])
        except (KeyError, TypeError, ValueError):
            return None
        tags = node.get('tags') or {}

        city = (
            tags.get('addr:city')
            or tags.get('addr:town')
            or tags.get('name')
            or 'Unknown'
        )[:128]
        state = (tags.get('addr:state') or _region_for_lng(lng))[:64]

        regular, mid, premium, diesel = _synthetic_prices(lng, rng)
        return FuelPrice(
            city=city,
            state=state,
            latitude=Decimal(f'{lat:.6f}'),
            longitude=Decimal(f'{lng:.6f}'),
            regular_price=Decimal(f'{regular:.3f}'),
            mid_grade_price=Decimal(f'{mid:.3f}'),
            premium_price=Decimal(f'{premium:.3f}'),
            diesel_price=Decimal(f'{diesel:.3f}'),
            price_per_gallon=Decimal(f'{regular:.3f}'),
        )

    def _preview(self, rows: list[FuelPrice]) -> None:
        self.stdout.write('Dry run — first 10 rows:')
        for r in rows[:10]:
            self.stdout.write(
                f'  {r.city}, {r.state}  ({r.latitude}, {r.longitude})  '
                f'regular ${r.regular_price}  diesel ${r.diesel_price}'
            )
        if len(rows) > 10:
            self.stdout.write(f'  … and {len(rows) - 10} more')

    @staticmethod
    def _parse_bbox(text: str) -> tuple[float, float, float, float]:
        try:
            parts = [float(x) for x in text.split(',')]
        except ValueError as exc:
            raise CommandError(f'Invalid --bbox: {text!r}') from exc
        if len(parts) != 4:
            raise CommandError('--bbox must be lat_min,lng_min,lat_max,lng_max')
        lat_min, lng_min, lat_max, lng_max = parts
        if lat_min >= lat_max or lng_min >= lng_max:
            raise CommandError('--bbox: min must be less than max for both axes')
        return lat_min, lng_min, lat_max, lng_max


def _region_for_lng(lng: float) -> str:
    if lng < -118.0:
        return 'West'
    if lng < -104.0:
        return 'Mountain'
    if lng < -90.0:
        return 'Plains'
    if lng < -80.0:
        return 'South'
    return 'Northeast'


def _synthetic_prices(lng: float, rng: random.Random) -> tuple[float, float, float, float]:
    base = 3.50
    for lo, hi, value in PRICE_BANDS:
        if lo <= lng < hi:
            base = value
            break
    regular = base + rng.uniform(-0.25, 0.30)
    mid_grade = regular + rng.uniform(0.20, 0.35)
    premium = regular + rng.uniform(0.45, 0.70)
    diesel = regular + rng.uniform(0.10, 0.55)
    return regular, mid_grade, premium, diesel
