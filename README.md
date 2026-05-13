# Fuel Route Optimizer

A Django + DRF service that, given any two US addresses, returns the cheapest
sequence of fuel stops along the driving route — accounting for vehicle range,
miles-per-gallon, and per-station prices.

```
POST /api/optimize/
{
  "start_address": "Los Angeles, CA",
  "end_address":   "New York, NY"
}
→  driving polyline, total distance & duration,
   ~6 optimal refueling stops with prices, gallons, and dollar cost per stop.
```

---

## Problem

Trucks and long-haul drivers must refuel multiple times across a route. Fuel
prices vary by region and even between adjacent stations — a poor sequence of
stops can add hundreds of dollars to a single trip. Given:

- A starting address and a destination
- A vehicle with **500 mi tank range** and **10 mpg** fuel economy
- A database of US fuel stations with per-station prices

…produce the **cheapest feasible sequence of refueling stops** that gets the
vehicle from origin to destination without running out of fuel, plus the route
geometry needed to render the trip on a map.

---

## How it works

```
                ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
 POST /optimize │  Geocoding   │ →  │   Routing    │ →  │  Optimizer   │
   ───────────► │  (Nominatim) │    │ (OpenRoute   │    │  (greedy +   │
                │              │    │  Service)    │    │   bbox DB)   │
                └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
                       │ 30d cache         │ 24h cache         │
                       └───────────────────┴───────────────────┘
                                           │
                                           ▼
                                  ┌──────────────────┐
                                  │  JSON response   │
                                  │   (1h cache)     │
                                  └──────────────────┘
```

1. **Geocode** both addresses via **OpenStreetMap Nominatim** (free, no key).
2. **Route** between the two points via **OpenRouteService** (free key, 2,000 req/day).
3. **Optimize** refueling stops by walking the route polyline and querying the
   `FuelPrice` table for stations within a 50-mile corridor at each decision point.
4. **Return** the full route polyline + ordered list of stops with gallons & cost.

---

## Technologies

| Layer | Choice | Why |
|---|---|---|
| Web framework | **Django 6.0** | Mature ORM, admin, migrations |
| API | **Django REST Framework 3.17** | Serializers, throttling, browsable API |
| Routing | **OpenRouteService** | 2,000 req/day free, encoded polylines, US coverage |
| Geocoding | **OSM Nominatim** | Free, no key, accurate for US addresses |
| Station data | **OSM Overpass** (`amenity=fuel`) | Real coordinates, free, no key |
| Polyline decode | `polyline` 2.0 | Google polyline 5 → `[(lat, lng), …]` |
| Cache | Django `LocMemCache` | Fine for dev; swap for Redis in prod |
| Database | SQLite | Demo default; swap for Postgres for real workloads |

---

## Setup

### Prerequisites

- Python 3.12+ (tested on 3.14)
- A free OpenRouteService API key — sign up at <https://openrouteservice.org/dev/#/signup>

### 1. Clone and create a virtual environment

```bash
git clone <repo-url> fuel_route_optimizer
cd fuel_route_optimizer
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set your ORS key (other keys are optional — geocoding and
fuel-station data are key-less):

```
DJANGO_SECRET_KEY=replace-me-with-a-long-random-string
MAP_ROUTING_API_KEY=eyJ...your-openrouteservice-key...
MAP_ROUTING_API_BASE_URL=https://api.openrouteservice.org
```

### 3. Apply database migrations

```bash
python manage.py migrate
```

### 4. Seed fuel-station data

Pull real US gas station coordinates from OpenStreetMap and assign realistic
synthetic prices (varies by region):

```bash
python manage.py seed_fuel_stations_osm
```

That fetches up to 5,000 stations across the contiguous US and inserts them
into `FuelPrice`. Takes ~60–90 seconds. Options:

```bash
# Smaller region for faster testing
python manage.py seed_fuel_stations_osm --bbox 32.0,-120.0,42.0,-74.0

# Wipe and reload with different price noise
python manage.py seed_fuel_stations_osm --purge --seed 7

# Preview without writing
python manage.py seed_fuel_stations_osm --dry-run
```

If you have a CSV of real fuel prices instead, use the bundled loader:

```bash
python manage.py load_fuel_prices path/to/fuel_prices.csv
```

(Required columns: `city`, `state`, `latitude`, `longitude`. Optional:
`regular_price`, `mid_grade_price`, `premium_price`, `diesel_price`.)

### 5. Run the server

```bash
python manage.py runserver
```

The API is now live at <http://127.0.0.1:8000/api/optimize/>.

---

## API usage

### Endpoint

```
POST /api/optimize/
Content-Type: application/json
```

### Request body

```json
{
  "start_address": "Los Angeles, CA",
  "end_address": "New York, NY"
}
```

Both fields are required, non-empty, and must differ.

### Successful response (200)

```bash
curl -sS -X POST http://127.0.0.1:8000/api/optimize/ \
  -H 'Content-Type: application/json' \
  -d '{"start_address":"Los Angeles, CA","end_address":"New York, NY"}'
```

```json
{
  "route_polyline": "g~vlEnjpiU...encoded-polyline...",
  "total_distance_miles": 2789.4,
  "estimated_duration_seconds": 154321.0,
  "stops": [
    {
      "name": "Flying J",
      "address": "Kingman, Mountain",
      "lat": 35.1894,
      "lng": -114.0530,
      "price_per_gallon": 3.612,
      "cost_for_stop": 96.27,
      "distance_from_previous_miles": 266.5,
      "gallons": 26.65,
      "is_safety_stop": false
    },
    {
      "name": "Love's Travel Stop",
      "address": "Tucumcari, Plains",
      "lat": 35.1717,
      "lng": -103.7250,
      "price_per_gallon": 3.058,
      "cost_for_stop": 81.79,
      "distance_from_previous_miles": 267.5,
      "gallons": 26.75,
      "is_safety_stop": false
    }
    /* … more stops … */
  ],
  "total_cost": 974.83,
  "total_fuel_used_gallons": 278.9,
  "final_leg_miles": 187.3,
  "start": {
    "query": "Los Angeles, CA",
    "lat": 34.0537,
    "lng": -118.2428,
    "display_name": "Los Angeles, Los Angeles County, California, …"
  },
  "end": {
    "query": "New York, NY",
    "lat": 40.7128,
    "lng": -74.0060,
    "display_name": "New York, …"
  },
  "cached": false
}
```

### Error responses

| Status | When | Body |
|---|---|---|
| 400 | Missing / blank / equal addresses | `{"start_address": ["..."]}` (DRF field errors) |
| 400 | Address can't be geocoded | `{"error": "geocoding_failed", "message": "..."}` |
| 422 | No feasible refueling plan (e.g. desert with no stations) | `{"error": "no_feasible_plan", "message": "...", "route_polyline": "...", "total_distance_miles": ...}` |
| 502 | OpenRouteService unreachable / returned an error | `{"error": "routing_failed", "message": "..."}` |

The 422 response still includes the route polyline and distance so the client
can draw the path even when refueling can't be planned.

---

## Optimization algorithm

A **greedy** algorithm with a **range constraint** and a **minimum-advance
constraint** to avoid degenerate "tiny hop" solutions.

```
position = 0
remaining_range = vehicle_range_miles (500)
stops = []

while distance_to_destination > remaining_range:
    # Look ahead within usable range (minus safety margin)
    look_end    = position + (remaining_range - 10)         # usable window
    prefer_from = position + 0.5 * usable_range             # far half of window

    # One DB query per decision point: bounding box around the route segment
    candidates = FuelPrice.objects.filter(
        latitude__range=(min_lat - 50mi_pad, max_lat + 50mi_pad),
        longitude__range=(min_lng - 50mi_pad, max_lng + 50mi_pad),
        price_per_gallon__isnull=False,
    )

    # Two-phase pick:
    #   1. Cheapest station in the FAR half of the window
    #   2. Fall back to cheapest anywhere in the window if the far half is empty
    #   3. Safety stop (3× corridor, accept any priced station) if both empty
    station = best_cheap_in_far_half(candidates) or
              best_cheap_anywhere(candidates)   or
              safety_stop(candidates)

    gallons = (station.along_route_miles - position) / mpg
    cost    = gallons * station.price_per_gallon
    stops.append({station, gallons, cost})

    position = station.along_route_miles
    remaining_range = vehicle_range_miles            # refuel to full
```

### Why "minimum advance"?

A naive "cheapest in remaining range" greedy picks whichever happens to have
the lowest price among the ~50 stations in the next 500 mi. That station is
often only 20–50 mi ahead — so the optimizer advances barely and then repeats,
producing 50 stops and never reaching the destination.

Requiring the picked station to be in the **far half** of the reachable window
keeps each stop ≥ 245 mi apart, which makes the greedy actually finish a
cross-country trip in 5–6 stops while still picking the cheapest option *in
that half*. If no station exists in the far half (sparse desert stretch),
we widen the search to the full window before declaring infeasible.

### Database strategy

One indexed bounding-box query per decision point. The CONUS has ~150,000 gas
stations; querying just the next-500-miles bbox keeps each query under a
few hundred rows. Add a composite index on `(latitude, longitude)` to your
`FuelPrice` model for production-grade performance:

```python
class Meta:
    indexes = [
        models.Index(fields=['latitude', 'longitude']),
    ]
```

GeoDjango / PostGIS would be the natural upgrade if station counts grow into
the millions; the current bounding-box approach is intentionally portable
(works on SQLite for the demo).

---

## Caching

Three independent layers, all using Django's cache framework:

| Layer | Key | TTL | Why |
|---|---|---|---|
| Geocoding | SHA-1 of normalized address | **30 days** | Addresses rarely move |
| Routing | `route:{lat,lng}:{lat,lng}` (6-dp rounded) | **24 hours** | Roads change slowly; protects ORS quota |
| Full response | SHA-1 of `lower(start)\|lower(end)` | **1 hour** | Short-circuits repeat requests entirely |

Repeat calls for the same trip return in single-digit milliseconds with
`"cached": true` in the payload.

**Note on `LocMemCache`:** the cache is per-process. Across `manage.py shell`
invocations the cache evaporates; under a multi-worker WSGI server each worker
has its own cache. For production, swap to Redis in `settings.py`:

```python
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://localhost:6379/1',
    }
}
```

---

## Project structure

```
fuel_route_optimizer/
├── api/
│   ├── geocoding.py          # GeocodingService (Nominatim + mock fallback)
│   ├── routing.py            # RoutingService (OpenRouteService)
│   ├── optimizer.py          # FuelStopOptimizer (greedy + bbox)
│   ├── models.py             # FuelPrice model
│   ├── serializers.py        # DRF request/response serializers
│   ├── views.py              # OptimizeFuelRouteView
│   ├── urls.py
│   └── management/commands/
│       ├── load_fuel_prices.py        # CSV/XLSX loader
│       └── seed_fuel_stations_osm.py  # OSM Overpass seeder
├── fuel_route_optimizer/     # Django project (settings, urls, wsgi)
├── scripts/
│   └── demo_optimize.py      # End-to-end shell demo
├── requirements.txt
├── manage.py
└── README.md
```

---

## Development scripts

```bash
# Run an end-to-end optimization in the Django shell (LA → NYC):
python manage.py shell < scripts/demo_optimize.py

# Inspect what's in the FuelPrice table:
python manage.py shell -c "from api.models import FuelPrice; print(FuelPrice.objects.count(), 'stations')"
```

---

## Loom walkthrough script (≈5 minutes)

Use this as a shot list when recording. Aim for ~5 minutes total.

### 0:00 – 0:30 · Project setup

> "This is the fuel route optimizer. The problem: given two US addresses,
> compute the cheapest sequence of refueling stops for a truck with a
> 500-mile range and 10 mpg."

- Open the project in VS Code, show the file tree briefly.
- Show `requirements.txt` + the `.env` file (mask the ORS key on screen).
- Run in terminal:
  ```bash
  source venv/bin/activate
  python manage.py migrate
  python manage.py seed_fuel_stations_osm
  python manage.py runserver
  ```
- Mention: "OSM Overpass gives us real US gas-station coordinates; synthetic
  prices vary by region so the optimizer has a reason to prefer some
  stations."

### 0:30 – 2:30 · API demo in Postman

> "Single endpoint: `POST /api/optimize/` with start and end addresses."

**Success case (≈45s):**
- Open Postman, show the request body for LA → NYC.
- Hit Send. While it runs (~5–15s), narrate: "First call geocodes both
  addresses, calls OpenRouteService once for the driving route, then runs
  the optimizer."
- Highlight in the response: `total_distance_miles ≈ 2789`, ~6 stops,
  `total_cost`, `is_safety_stop: false` everywhere.
- Point at one stop: "Cheapest priced station within the next 500-mile
  window, picked from a 50-mile corridor around the route."

**Validation error (≈20s):**
- Change the body to identical start/end → Send → show DRF's 400 with
  `"start_address and end_address must differ"`.

**Geocoding error (≈20s):**
- Use `"asdfqwer zzz nowhere"` as `start_address` → Send → show 400 with
  `"error": "geocoding_failed"`.

**Optimization edge case (≈15s):**
- Try a route through a sparse area where no stations are reachable (e.g.
  a tiny seeded subset). Show the 422 with `"error": "no_feasible_plan"`,
  but note the route polyline is still returned so the UI can draw it.

### 2:30 – 4:30 · Code walkthrough

> "Three services compose the pipeline. Each one is independent and cached."

**`api/geocoding.py` (~30s):**
- Show the `GeocodingService` class.
- Highlight: Nominatim User-Agent, 1-req-per-second class-level lock,
  30-day cache, mock fallback so demos don't die when Nominatim is down.

**`api/routing.py` (~30s):**
- `RoutingService.get_route` — show the cache check, single ORS POST,
  response parsing into miles / seconds / polyline / steps.
- Mention: "Exactly one upstream call per unique route; cached for 24h."

**`api/optimizer.py` (~60s):**
- Walk through `FuelStopOptimizer.optimize`:
  - Cumulative haversine along the polyline, scaled to match the
    routing API's true road distance.
  - The main `while` loop: position, remaining range, pick a station.
- Open `_pick_station`. Explain the two-phase greedy: "Cheapest in the
  far half of the tank first — otherwise we'd take tiny hops at the
  cheapest nearby station and never finish."
- Show the bounding-box DB query: "One indexed query per decision point;
  no PostGIS needed."

**`api/views.py` (~15s):**
- Skim `OptimizeFuelRouteView.post`: geocode → route → decode polyline →
  optimize → serialize → cache.
- Point at the 1-hour response cache key.

### 4:30 – 5:00 · Caching demo

> "Three cache layers stack — let me show the speedup."

- In Postman, hit Send on the LA → NYC request **again**.
- Show: response returns in tens of milliseconds, `"cached": true`.
- Quick narration: "Geocoding cached 30 days, routing 24 hours, full
  response 1 hour. Repeat requests skip Nominatim, ORS, and the optimizer
  entirely — just a cache read."

**Close** (5s):
- "Code is in the repo, README has setup steps. Thanks for watching."

---

## License

MIT (or your choice — adjust as needed).
