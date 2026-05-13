from __future__ import annotations

import csv
import json
import random
import statistics
import time
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.geocoding import GeocodingError, GeocodingService
from api.models import FuelPrice


DEFAULT_CHECKPOINT = Path('data/.geocode_cache.json')

GEOCODED_JITTER_DEG = 0.05

STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    'AL': (32.806671, -86.79113),
    'AK': (61.370716, -152.404419),
    'AZ': (33.729759, -111.431221),
    'AR': (34.969704, -92.373123),
    'CA': (36.116203, -119.681564),
    'CO': (39.059811, -105.311104),
    'CT': (41.597782, -72.755371),
    'DE': (39.318523, -75.507141),
    'DC': (38.897438, -77.026817),
    'FL': (27.766279, -81.686783),
    'GA': (33.040619, -83.643074),
    'HI': (21.094318, -157.498337),
    'ID': (44.240459, -114.478828),
    'IL': (40.349457, -88.986137),
    'IN': (39.849426, -86.258278),
    'IA': (42.011539, -93.210526),
    'KS': (38.5266, -96.726486),
    'KY': (37.66814, -84.670067),
    'LA': (31.169546, -91.867805),
    'ME': (44.693947, -69.381927),
    'MD': (39.063946, -76.802101),
    'MA': (42.230171, -71.530106),
    'MI': (43.326618, -84.536095),
    'MN': (45.694454, -93.900192),
    'MS': (32.741646, -89.678696),
    'MO': (38.456085, -92.288368),
    'MT': (46.921925, -110.454353),
    'NE': (41.12537, -98.268082),
    'NV': (38.313515, -117.055374),
    'NH': (43.452492, -71.563896),
    'NJ': (40.298904, -74.521011),
    'NM': (34.840515, -106.248482),
    'NY': (42.165726, -74.948051),
    'NC': (35.630066, -79.806419),
    'ND': (47.528912, -99.784012),
    'OH': (40.388783, -82.764915),
    'OK': (35.565342, -96.928917),
    'OR': (44.572021, -122.070938),
    'PA': (40.590752, -77.209755),
    'RI': (41.680893, -71.51178),
    'SC': (33.856892, -80.945007),
    'SD': (44.299782, -99.438828),
    'TN': (35.747845, -86.692345),
    'TX': (31.054487, -97.563461),
    'UT': (40.150032, -111.862434),
    'VT': (44.045876, -72.710686),
    'VA': (37.769337, -78.169968),
    'WA': (47.400902, -121.490494),
    'WV': (38.491226, -80.954453),
    'WI': (44.268543, -89.616508),
    'WY': (42.755966, -107.30249),
}

STATE_JITTER = {
    'default': (2.0, 2.0),
    'TX': (3.5, 4.5),
    'CA': (3.5, 2.0),
    'MT': (2.0, 4.0),
    'AK': (5.0, 10.0),
    'NM': (2.5, 2.5),
    'CO': (2.0, 3.0),
    'WY': (2.0, 3.0),
    'OR': (2.0, 3.0),
    'NV': (3.0, 2.5),
    'KS': (2.0, 3.0),
    'NE': (1.5, 3.5),
    'OK': (1.5, 3.5),
}


class Command(BaseCommand):
    help = 'Load FuelPrice rows from the OPIS truckstop CSV (synthesized lat/lng).'

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)
        parser.add_argument('--purge', action='store_true',
                            help='Delete existing FuelPrice rows before loading.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse and preview without writing.')
        parser.add_argument('--limit', type=int, default=None,
                            help='Cap on number of stations to load (testing).')
        parser.add_argument(
            '--geocode', action='store_true',
            help='Geocode each unique (city, state) via Nominatim (~1 req/sec).',
        )
        parser.add_argument(
            '--checkpoint', type=str, default=str(DEFAULT_CHECKPOINT),
            help='JSON file persisting geocoded city coords across runs. '
                 'Pass "none" to disable.',
        )

    def handle(self, *args, **opts):
        path = Path(opts['csv_path']).expanduser().resolve()
        if not path.is_file():
            raise CommandError(f'File not found: {path}')

        rows = self._read_csv(path)
        self.stdout.write(f'Read {len(rows)} raw rows from {path.name}')

        us_rows = [r for r in rows if r['State'].strip().upper() in STATE_CENTROIDS]
        skipped_non_us = len(rows) - len(us_rows)
        if skipped_non_us:
            self.stdout.write(f'Skipped {skipped_non_us} non-US rows (CA provinces, etc.)')

        stations = self._dedupe(us_rows)
        self.stdout.write(f'Deduped to {len(stations)} unique truckstops')

        if opts['limit']:
            stations = stations[:opts['limit']]
            self.stdout.write(f'Limited to first {len(stations)} for this run')

        city_coords: dict[str, tuple[float, float]] = {}
        if opts['geocode']:
            checkpoint_path = (
                None if opts['checkpoint'].lower() == 'none'
                else Path(opts['checkpoint']).expanduser().resolve()
            )
            city_coords = self._geocode_unique_cities(stations, checkpoint_path)

        records = [self._build_record(s, city_coords) for s in stations]

        if opts['dry_run']:
            self._preview(records)
            return

        with transaction.atomic():
            if opts['purge']:
                deleted, _ = FuelPrice.objects.all().delete()
                self.stdout.write(f'Purged {deleted} existing FuelPrice rows.')
            created = FuelPrice.objects.bulk_create(records, ignore_conflicts=True)

        self.stdout.write(self.style.SUCCESS(
            f'Inserted up to {len(created)} FuelPrice rows.'
        ))
        total = FuelPrice.objects.count()
        self.stdout.write(f'FuelPrice table now has {total} total rows.')

    def _read_csv(self, path: Path) -> list[dict]:
        with path.open(newline='', encoding='utf-8-sig') as fh:
            reader = csv.DictReader(fh)
            required = {'OPIS Truckstop ID', 'Truckstop Name', 'City', 'State', 'Retail Price'}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f'CSV missing required columns: {sorted(missing)}')
            return list(reader)

    def _dedupe(self, rows: list[dict]) -> list[dict]:
        groups: dict[str, list[dict]] = {}
        for r in rows:
            tid = (r.get('OPIS Truckstop ID') or '').strip()
            if not tid:
                continue
            try:
                price = float((r.get('Retail Price') or '').strip())
            except ValueError:
                continue
            groups.setdefault(tid, []).append({**r, '_price': price})

        out: list[dict] = []
        for tid, group in groups.items():
            prices = [r['_price'] for r in group]
            rep = group[0]
            out.append({
                'id': tid,
                'name': (rep.get('Truckstop Name') or '').strip(),
                'address': (rep.get('Address') or '').strip(),
                'city': (rep.get('City') or '').strip(),
                'state': (rep.get('State') or '').strip().upper(),
                'price': statistics.mean(prices),
            })
        return out

    def _build_record(
        self,
        station: dict,
        city_coords: dict[str, tuple[float, float]],
    ) -> FuelPrice:
        key = self._city_key(station['city'], station['state'])
        if key in city_coords:
            lat, lng = self._jitter_around(
                city_coords[key], station['id'], GEOCODED_JITTER_DEG
            )
        else:
            lat, lng = self._synthesize_coords(station['id'], station['state'])
        price = round(station['price'], 3)
        price_dec = Decimal(f'{price:.3f}')

        name = (station['name'] or 'Unknown')[:128]
        city_label = station['city'] or name
        return FuelPrice(
            city=city_label[:128],
            state=station['state'][:64],
            latitude=Decimal(f'{lat:.6f}'),
            longitude=Decimal(f'{lng:.6f}'),
            regular_price=price_dec,
            diesel_price=price_dec,
            price_per_gallon=price_dec,
        )

    @staticmethod
    def _synthesize_coords(truckstop_id: str, state: str) -> tuple[float, float]:
        center = STATE_CENTROIDS[state]
        lat_range, lng_range = STATE_JITTER.get(state, STATE_JITTER['default'])
        rng = random.Random(int(truckstop_id))
        lat = center[0] + rng.uniform(-lat_range, lat_range)
        lng = center[1] + rng.uniform(-lng_range, lng_range)
        lat = max(-89.999999, min(89.999999, lat))
        lng = max(-179.999999, min(179.999999, lng))
        return lat, lng

    @staticmethod
    def _jitter_around(
        center: tuple[float, float],
        truckstop_id: str,
        range_deg: float,
    ) -> tuple[float, float]:
        rng = random.Random(int(truckstop_id))
        return (
            center[0] + rng.uniform(-range_deg, range_deg),
            center[1] + rng.uniform(-range_deg, range_deg),
        )

    @staticmethod
    def _city_key(city: str, state: str) -> str:
        return f'{city.strip().lower()}|{state.strip().upper()}'

    def _geocode_unique_cities(
        self,
        stations: list[dict],
        checkpoint_path: Path | None,
    ) -> dict[str, tuple[float, float]]:
        coords = self._load_checkpoint(checkpoint_path)
        unique_keys: dict[str, tuple[str, str]] = {}
        for s in stations:
            key = self._city_key(s['city'], s['state'])
            unique_keys.setdefault(key, (s['city'], s['state']))

        to_fetch = [(k, v) for k, v in unique_keys.items() if k not in coords]
        total = len(to_fetch)
        if not to_fetch:
            self.stdout.write(
                f'All {len(unique_keys)} unique cities already in checkpoint; nothing to fetch.'
            )
            return coords

        already = len(unique_keys) - total
        eta_sec = total
        self.stdout.write(
            f'Geocoding {total} new cities ({already} already cached); '
            f'rate-limited to ~1/sec → est. {eta_sec // 60} min {eta_sec % 60} sec.'
        )

        geocoder = GeocodingService()
        started = time.monotonic()
        for i, (key, (city, state)) in enumerate(to_fetch, start=1):
            query = f'{city}, {state}, USA'
            try:
                result = geocoder.geocode(query)
                coords[key] = (result['lat'], result['lng'])
            except GeocodingError as exc:
                self.stdout.write(self.style.WARNING(
                    f'  [{i}/{total}] {query!r}: {exc} (will use state centroid)'
                ))
                continue

            if i % 50 == 0 or i == total:
                elapsed = time.monotonic() - started
                rate = i / max(elapsed, 0.001)
                remaining = (total - i) / max(rate, 0.001)
                self.stdout.write(
                    f'  [{i}/{total}] geocoded; {remaining/60:.1f} min remaining'
                )
                self._save_checkpoint(checkpoint_path, coords)

        self._save_checkpoint(checkpoint_path, coords)
        self.stdout.write(self.style.SUCCESS(
            f'Geocoded {total} new cities in {(time.monotonic() - started)/60:.1f} min '
            f'({len(coords)} total in checkpoint).'
        ))
        return coords

    def _load_checkpoint(self, path: Path | None) -> dict[str, tuple[float, float]]:
        if path is None or not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            self.stdout.write(self.style.WARNING(
                f'Could not read checkpoint {path}: {exc}. Starting fresh.'
            ))
            return {}
        return {k: tuple(v) for k, v in raw.items()}

    def _save_checkpoint(self, path: Path | None, coords: dict) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(json.dumps(coords, indent=2), encoding='utf-8')
        tmp.replace(path)

    def _preview(self, records: list[FuelPrice]) -> None:
        self.stdout.write('Dry run — first 10 rows:')
        for r in records[:10]:
            self.stdout.write(
                f'  {r.city:<35.35} {r.state:<3}  '
                f'({float(r.latitude):>8.3f}, {float(r.longitude):>9.3f})  '
                f'${float(r.price_per_gallon):.3f}/gal'
            )
        if len(records) > 10:
            self.stdout.write(f'  … and {len(records) - 10} more')
        prices = sorted(float(r.price_per_gallon) for r in records)
        n = len(prices)
        self.stdout.write(
            f'\nPrice distribution: min ${prices[0]:.3f}  '
            f'median ${prices[n//2]:.3f}  max ${prices[-1]:.3f}  '
            f'mean ${sum(prices)/n:.3f}'
        )
