# holiday-house-comparison

A static site generator for comparing holiday houses suitable for sledding (Rodeln) trips. Sled run data is scraped from rodelwelten.com and house data from fewo-direkt.de / booking.com, then pre-rendered into a static HTML comparison page.

## Setup

1. Install dependencies: `pip install -r requirements.txt`
2. Place the ChromeDriver binary in `webdriver/chromedriver-win64/chromedriver.exe` (used for JS-rendered house pages).
3. Copy `input.template.json` to `input.json` and fill in your trips, houses, and sled run URLs.
4. Generate the static site: `python app.py`
5. Open or host `public/index.html`.

## Usage

```
python app.py [--force] [--broker fewo|booking] [--limit N] [--from-cache]
```

| Flag | Description |
|------|-------------|
| `--force` | Re-fetch all sled run data, ignoring the local cache |
| `--broker fewo\|booking` | Only scrape houses from this broker (skips others) |
| `--limit N` | Stop after scraping N houses |
| `--from-cache` | Re-render HTML from existing `public/data.json` without scraping |

Outputs:
- `public/index.html` — the static comparison page
- `public/data.json` — raw scraped data as JSON
- `cache/sled_runs.json` — sled run cache (TTL: 1 day)

## Input Format

`input.json` contains a list of **trips**, each with a date range and a list of houses. Each house links to its booking page and a set of rodelwelten.com sled run URLs.

```json
{
  "title": "Rodelurlaub 2027",
  "trips": [
    {
      "name": "Option A - 4 Nächte",
      "checkin": "YYYY-MM-DD",
      "checkout": "YYYY-MM-DD",
      "houses": [
        {
          "name": "House Name - Ort",
          "lat": 47.0,
          "lon": 11.0,
          "image_url": "https://...",
          "house_url": "https://www.fewo-direkt.de/... or https://www.booking.com/...",
          "direct_url": "https://gastgeber-website.de/",
          "supermarket": "2 km (Spar)",
          "pois": [
            { "type": "train", "label": "Bahnhof Ortsname", "lat": 47.01, "lon": 11.01 },
            { "type": "supermarket", "label": "Spar Ortsname", "lat": 47.02, "lon": 11.02 }
          ],
          "sled_run_urls": [
            "https://www.rodelwelten.com/rodelbahnen/detail/run-name"
          ]
        }
      ]
    }
  ]
}
```

A house can optionally override the trip-level dates with its own `"checkin"` and `"checkout"` fields.

### Overriding scraped house details

Any field normally scraped from the booking page can be overridden directly in `input.json` by adding it to the house entry. Overrides take precedence over scraped values.

| Field | Description | Example |
|-------|-------------|---------|
| `address` | Location / address | `"Leutasch, Tirol"` |
| `rooms` | Number of bedrooms | `"5"` |
| `persons` | Max. number of persons | `"8"` |
| `sqm` | Living area | `"120 m²"` |
| `bathrooms` | Number of bathrooms | `"2"` |
| `room_config` | List of room descriptions | `["2× Doppelzimmer", "1× Schlafsaal"]` |
| `price` | Total price | `"2388"` or `"2388€"` |
| `time` | Availability status | `"Available"` |
| `rating` | Rating | `"9.2"` |
| `supermarket` | Supermarket distance (text) | `"2 km (Spar)"` |
| `train_station` | Train station distance (text) | `"650 m zu Fuß"` |
| `sauna` | Sauna available | `"Ja"` or `"Nein"` |
| `nearest_sled_run` | Nearest sled run with driving distance/time | `"Hoher Sattel (2,4 km · 4 min)"` |

Example:

```json
{
  "name": "My House",
  "house_url": "https://...",
  "price": "2388",
  "persons": "8",
  "rooms": "5",
  "supermarket": "2 km (Spar)",
  "train_station": "650 m zu Fuß"
}
```

### POI types

Points of interest (`pois`) are shown on the house location map. Supported `type` values:

| Type | Icon | Color |
|------|------|-------|
| `train` | 🚉 | Green |
| `supermarket` | 🛒 | Orange |
| *(any other string)* | 📍 | Purple |

## How It Works

1. **House scraping** — uses headless Chrome (`undetected-chromedriver`) to bypass bot detection on fewo-direkt.de and booking.com, then extracts location, price, bedroom count, bed configuration, sauna availability, and more. Fields present in `input.json` override scraped values.
2. **Sled run scraping** — fetches rodelwelten.com `/detail/` pages with `requests`/BeautifulSoup and parses the details table (length, elevation, night sledding, public transport, sled rental, etc.). Hut/Alm info (name + website) is extracted from the `div.hut-content` blocks on the page. GPX tracks are fetched and downsampled for map display. Results are cached for 24 hours in `cache/sled_runs.json`.
3. **Date injection** — known date query parameters (`chkin`, `chkout`, `startDate`, `endDate`, `checkin`, `checkout`, etc.) in house URLs are replaced with the configured trip dates before scraping.
4. **Rendering** — the Jinja2 template in `templates/index.html` renders all trips and houses into a card-based comparison layout with interactive Leaflet maps. Prices are normalised to `XXXX€` format via the `normalize_price` filter. Per-person price is always shown for 8 persons; a second row for 10 persons is shown when `house.persons == "10"`.

## Supported Sources

| Type      | Supported sites                         |
|-----------|-----------------------------------------|
| Houses    | fewo-direkt.de, booking.com             |
| Sled runs | rodelwelten.com (detail pages only)     |

## Maps

Each house card shows two types of maps:

- **House map** — shows the house location (🏠) plus any configured POIs (train stations, supermarkets, etc.)
- **Sled run maps** — shown per sled run (expand to view); displays the GPX route with the house location (🏠) for distance reference

## Notes

- House scraping requires Chrome to be running in non-headless mode to bypass bot detection. A browser window will briefly appear off-screen during scraping.
- Sled run map pages (e.g. `/rodelbahnen/karte`) return all `N/A` — only use `/detail/` URLs.
- The Flask `@app.route('/')` enables a live server mode (`flask run`) that scrapes on every request, but the primary workflow is static generation via `python app.py`.
- After adding new sled runs, run with `--force` to bypass the cache and pick up newly scraped fields (e.g. huts).
