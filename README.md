# holiday-house-comparison

A static site generator for comparing holiday houses suitable for sledding (Rodeln) trips. Sled run data is scraped from rodelwelten.com and house data from fewo-direkt.de / booking.com, then pre-rendered into a static HTML comparison page.

App classification (profile, route) lives in [`homelab.yml`](homelab.yml); status against the
infrastructure repo's deployment standard lives in
[`compliance/holiday-house-comparison.md`](https://github.com/Gr3yh0und/infrastructure/blob/main/compliance/holiday-house-comparison.md)
in the (private) infrastructure repo — this repo is public, so its compliance detail lives there
instead of at the repo root (see that standard's §1b).

## Setup

1. Install dependencies: `pip install -r requirements.txt` (includes pylint)
2. Place Chrome for Testing and the matching ChromeDriver in `webdriver/` (used for JS-rendered house pages): `chrome-linux64/chrome` + `chromedriver-linux64/chromedriver` on Linux, `chrome-win64/chrome.exe` + `chromedriver-win64/chromedriver.exe` on Windows. Without a display (server, SSH), run the scrape under `xvfb-run -a`.
3. Copy `input.template.json` to `input.json` and fill in your trips, houses, and sled run URLs.
4. For working maps, set a CARTO basemap key (free at [carto.com/basemaps/apikey](https://carto.com/basemaps/apikey)): `CARTO_API_KEY=...` in `/etc/homelab/holiday-house-comparison.env` on the server, or as an environment variable elsewhere. It is read at build time and is visible in the page source, so restrict it to your domains in the CARTO dashboard and never commit it. Without it every map tile shows "API KEY REQUIRED".
5. (Optional) Edit `config.json` to adjust global defaults (see [Configuration](#configuration)).
6. Scrape and generate the static site: `python app.py` (see [Scrape vs. render](#scrape-vs-render))
7. Open or host `public/index.html`.
8. (Optional) Set up deployment — see [Deployment](#deployment).

## Scrape vs. render

Scraping and publishing are two separate steps:

1. **Scrape** — `python app.py --scrape-only` scrapes every house (about 8 minutes) and saves the
   result to `cache/houses.json`. Run it when you want fresh prices. The file is only replaced
   when the run succeeds: a run in progress writes `cache/houses.partial.json`, so stopping it or
   a failed run never loses the last good data. A run where every scrape fails keeps the old data
   and marks it `status: down`.
2. **Render** — `python app.py --from-cache` builds `public/index.html` and `public/health.json`
   from `cache/houses.json` in under a second, with no network access. Both deploy paths below
   run only this step, so a deploy never scrapes, and both sites always show the same data.

Plain `python app.py` does both in one go. `health.json`'s `last_update` is the time of the last
successful scrape (ISO 8601, UTC), not of the deploy.

Typical update: `python app.py --scrape-only`, check the result, then deploy. In this repo's Claude
session, `/scrape` does the first step for you: it starts the scrape in the background, reports
25/50/75/100 %, and sums up the result (`.claude/skills/scrape/SKILL.md`). The scraper prints a
`[progress] 5/18 houses (27%)` line after each house for this.

On a machine without a screen (server, SSH), run the scrape under a virtual one — Chrome must run
non-headless to get past the brokers' bot checks:

```bash
xvfb-run -a .venv/bin/python app.py --scrape-only
```

`--broker` and `--limit` scrape only some houses. Their results are merged into the existing
`cache/houses.json` (matched by trip and house name); the other houses keep their last data.

A scraped house is one of:
- **Available** with a price — free on the trip dates.
- **Unavailable** with no price — booked out (fewo-direkt.de and booking.com detect this).
- A **failure** — the parser found no data at all (bot page, crash, changed layout). The house
  falls back to its last cached data (if under `house_cache_ttl_h` old) or all `N/A`, and the run's
  status becomes `degraded`. If every house fails, the cache is not replaced and the status is `down`.

## Deployment

There are two targets, published by two different tools:

| Target | Where | Access control | Tool |
|---|---|---|---|
| **homelab** (`local`) | `https://server.fritz.box/holiday-house-comparison/` | Authelia (proxy) | `/deploy` — the shared `infrastructure/scripts/deploy.sh` |
| **public** (FTP) | external FTP host | PHP password gate (`deploy_auth/`) | `./deploy.sh` / `.\deploy.ps1` in this repo |

### Homelab target (`/deploy`)

Run `/deploy` in this repo's Claude session, or directly:

```bash
bash "${INFRA_DIR:-/samba/projects/infrastructure}"/scripts/deploy.sh --dry-run local
bash "${INFRA_DIR:-/samba/projects/infrastructure}"/scripts/deploy.sh local
```

It runs the tests, bumps `VERSION`, updates `CHANGELOG.md`, tags and pushes, runs
`python app.py --from-cache` (render only, see above), then rsyncs `public/` into
`/samba/server/www/holiday-house-comparison/releases/<version>/` and points `current` at it.
`/rollback` points `current` back at the previous release. Settings are in `deploy.config`
(gitignored; copy `deploy.config.example`).

The proxy serves this folder as static files and never runs PHP, so the page is the plain
`index.html` — no PHP gate here. A PHP file in that folder would be sent as plain text.
`health.json` is served without login at `/holiday-house-comparison/health`.

### Public target (FTP)

`deploy.sh`/`deploy.ps1` run `python app.py --from-cache` to render `public/index.html` from the
scraped data (no scraping, see [Scrape vs. render](#scrape-vs-render)), turn it into a
**password-gated** `index.php`, and upload it via FTP alongside the gate itself (`deploy_auth/`).
Use whichever matches your OS.

**Why gated, and why PHP:** the site republishes data scraped from commercial rental listing
sites on a public host — the exposure is legal/ToS, not personal data (the listings themselves are
public). The gate is not decorative: pages deploy as `.php` specifically so it runs *before* any
content is sent, it fails closed (a missing secret file 500s rather than serving the page
unprotected), and it uses `hash_equals`/`hash_hmac` throughout for timing-safe comparison. See
[`compliance/holiday-house-comparison.md`](https://github.com/Gr3yh0und/infrastructure/blob/main/compliance/holiday-house-comparison.md)
§5 for the full reasoning.

**Setup (both scripts share the same config):**

On the deploy server, put the keys in `/etc/homelab/holiday-house-comparison.env` (see
`.env.example`; owned by the deploying user, mode `0600`). Elsewhere, copy
`deploy.config.template` to `deploy.config` (gitignored) — it's only read when the env file
doesn't exist (WEBAPP_PROJECT_STANDARD.md §4):

```
FTP_HOST=ftp.example.com
FTP_USER=username
FTP_PASS=password
FTP_REMOTE_PATH=/example.com
SITE_PASSWORD=change-me
```

`SITE_PASSWORD` never leaves the machine running the deploy script — it's salted and hashed
locally into `deploy_auth/auth_secret.php` (generated, gitignored) before that file is uploaded.
Changing it invalidates every saved login at once.

**Windows (PowerShell):**

```powershell
.\deploy.ps1                    # builds, then uploads as index.php
.\deploy-test.ps1               # same, but as index-test.php (for testing)
```

Requires `curl.exe`, built into Windows 10+.

**Unix (bash):**

```bash
chmod +x deploy.sh deploy-test.sh
./deploy.sh                    # builds, then uploads as index.php
./deploy-test.sh               # same, but as index-test.php (for testing)
```

Requires `curl` and `openssl`, plus `sha256sum` (Linux) or `shasum` (macOS, tried as a fallback) —
all available by default on macOS and most Linux distributions.

**Rollback:** every deploy snapshots the exact gated bytes it uploads under `releases/prod/` (or
`releases/test/` for `deploy-test.*`), keeping the last 5. FTP has no atomic flip, so a rollback
re-uploads a known-good snapshot verbatim instead of rebuilding:

```bash
./deploy.sh --rollback        # re-publish the release before the current one
./deploy.sh --rollback 2      # go back 2 releases instead of 1
```

```powershell
.\deploy.ps1 -Rollback        # re-publish the release before the current one
.\deploy.ps1 -Rollback -Steps 2
```

`deploy-test.sh`/`deploy-test.ps1` support the same flags against their own, separate release
history — a test rollback can never touch the prod release line or vice versa.

## Usage

```
python app.py [--scrape-only | --from-cache] [--force] [--broker fewo|booking|huetten|interhome] [--limit N] [--house NAME] [--lang de-DE|en-GB|fr-FR|nl-NL|bar-DE|bar-AT|gsw-CH|nds-DE|pfl-DE]
```

| Flag | Description |
|------|-------------|
| `--force` | Re-fetch all sled run data, ignoring the local cache |
| `--broker fewo\|booking\|huetten\|interhome` | Only scrape houses from this broker (skips others). The result is merged into `cache/houses.json`. |
| `--limit N` | Stop after scraping N houses. The result is merged into `cache/houses.json`. |
| `--scrape-only` | Scrape and update `cache/houses.json`, but do not render |
| `--from-cache` | Render from `cache/houses.json` without scraping (what deploys run) |
| `--house NAME` | Scrape only one house (case-insensitive substring match), patch `cache/houses.json`, and re-render (with `--scrape-only`: no re-render). If the house appears in multiple trips with different dates, each trip is scraped separately. |
| `--lang de-DE\|en-GB\|fr-FR\|nl-NL\|bar-DE\|bar-AT\|gsw-CH\|nds-DE\|pfl-DE` | Language for the rendered page (default: `bar-DE`). `bar-DE` = Bavarian, `bar-AT` = Tyrolean, `gsw-CH` = Swiss German, `nds-DE` = Hamburg (Low German), `pfl-DE` = Karlsruhe (Badisch-Pfälzisch). |

Outputs:
- `public/index.html` — the static comparison page
- `public/health.json` — status for monitoring (`/health`): `version`, `status` (`ok`/`degraded`/`down`), `last_update` (last successful scrape, ISO 8601 UTC)
- `cache/houses.json` — scraped house data, the input for every render (`cache/houses.partial.json` while a scrape runs)
- `cache/sled_runs.json` — rodelwelten.com sled run cache (TTL: 1 day)
- `cache/outdooractive.json` — outdooractive.com sled run cache (TTL: 1 day)
- `cache/loipen.json` — Overpass API Nordic ski trail cache (TTL: 1 day, keyed by house coordinates)

## Input Format

`input.json` uses a **house-centric** structure: a top-level `houses` list where each house declares the trips it belongs to. This avoids duplicating house data across trips when the same property is available on multiple date ranges.

```json
{
  "title": "Rodelurlaub 2027",
  "houses": [
    {
      "name": "House Name - Ort",
      "lat": 47.0,
      "lon": 11.0,
      "image_url": "https://...",
      "house_url": "https://www.fewo-direkt.de/... or https://www.booking.com/...",
      "direct_url": "https://gastgeber-website.de/",
      "nearest_sled_run": "Hoher Sattel (2,4 km · 4 min)",
      "pois": [
        { "type": "train", "label": "Bahnhof Ortsname", "lat": 47.01, "lon": 11.01 },
        { "type": "supermarket", "label": "Spar Ortsname", "lat": 47.02, "lon": 11.02 }
      ],
      "sled_run_urls": [
        "https://www.rodelwelten.com/rodelbahnen/detail/run-name",
        "https://www.outdooractive.com/de/route/rodeln/region/name/12345678/"
      ],
      "trips": [
        { "name": "Option A - 4 Nächte", "checkin": "YYYY-MM-DD", "checkout": "YYYY-MM-DD" },
        { "name": "Option B - 7 Nächte", "checkin": "YYYY-MM-DD", "checkout": "YYYY-MM-DD" }
      ]
    }
  ]
}
```

A fully-documented template entry is kept at the top of the `houses` list in `input.json` with `"template": true`. It is ignored by the scraper — copy it, remove the `template` flag, and fill in the relevant fields to add a new house.

Each entry in `trips` specifies which date range the house is available for. Any scraped field (e.g. `price`, `time`) can also be set as a per-trip override directly inside the trip entry:

```json
"trips": [
  { "name": "Option A", "checkin": "2027-02-17", "checkout": "2027-02-21", "price": "3200" },
  { "name": "Option B", "checkin": "2027-02-13", "checkout": "2027-02-20", "price": "4100" }
]
```

Trip-level overrides take precedence over both house-level values and scraped data.

> **Old prices:** a `price` set here always wins, also when the scrape finds the house booked out.
> The page then shows the old price next to "Unavailable". The scraper prints a warning for this
> case — remove the `price` line when you start a new comparison.

The order of trips in the rendered output follows the order houses appear in the `houses` list (first occurrence of each trip name determines its position).

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
| `time` | Availability status. `"Available"` / `"Unavailable"` get translated labels and colour coding. Omitting `house_url` sets this to `"check_manually"` automatically (shown as a translated "please check manually" label). | `"Available"` |
| `rating` | Rating | `"9.2"` |
| `supermarket` | Distance to nearest supermarket | `"Coop (380 m · 5 min)"` |
| `train_station` | Distance to nearest train station | `"Bahnhof Lauterbrunnen (370 m · 5 min)"` |
| `bus_stop` | Distance to nearest bus stop | `"Neustift Neugasteig (0,3 km · 3 min)"` |
| `sauna` | Sauna available | `"Ja"` or `"Nein"` |
| `nearest_sled_run` | Nearest sled run with distance/time | `"Sulwald – Isenfluh – Lauterbrunnen (0,3 km · 4 min)"` |
| `image_url` | Photo on the house card. Optional: without it, the listing's main photo is used. A dead URL (not an image, or an error) also falls back to the listing photo at scrape time. | `"https://cf.bstatic.com/…jpg"` |
| `notes` | Free-text note shown on the house card (above the booking buttons) | `"Nur per E-Mail buchbar."` |
| `train_track` | List of `[lat, lon]` points for a train/rack-railway line shown as a red dashed overlay on maps | `[[46.598, 7.908], [46.605, 7.920]]` |
| `bus_track` | Object with `label` (tooltip text) and `points` (list of `[lat, lon]`) for a bus route shown as a blue dashed overlay on the house map | `{"label": "Bus 590N · Innsbruck → Neustift", "points": [[47.26, 11.40], ...]}` |
| `loipen_radius_m` | Per-house override for the Nordic trail search radius in metres (default from `config.json`) | `15000` |
| `disable_loipen` | Set to `true` to skip the Overpass auto-discovery for this house. Manually specified `loipen_urls` are still parsed. | `true` |
| `loipen_urls` | List of rodelwelten.com or outdooractive.com URLs for specific Loipen to include manually. Merged with Overpass results and always parsed even when `disable_loipen` is set. | `["https://www.outdooractive.com/de/route/..."]` |

> **Tip:** `bus_stop` is auto-calculated from a `pois` entry with `"type": "bus"` when running `app.py`. `supermarket` and `train_station` can be set the same way. All three can be overridden manually.

**Houses without a broker URL:** If a house is booked directly (no fewo-direkt / booking.com URL), omit `house_url` entirely and set `direct_url` to the host's website. No scraping will be attempted; all fields must be provided as overrides in `input.json`. The availability field is automatically set to a translated "check manually" label.

Example:

```json
{
  "name": "My House",
  "house_url": "https://...",
  "price": "2388",
  "persons": "8",
  "rooms": "5",
  "supermarket": "Spar (2,0 km · 24 min)",
  "train_station": "Bahnhof Ort (650 m · 8 min)"
}
```

### POI types

Points of interest (`pois`) are shown on the house location map. Supported `type` values:

| Type | Icon | Color |
|------|------|-------|
| `train` | 🚉 | Green |
| `supermarket` | 🛒 | Orange |
| `bus` | 🚌 | Blue |
| *(any other string)* | 📍 | Purple |

A `bus` POI also auto-populates the `bus_stop` distance field on the house card (walking speed assumed for ≤ 1.5 km, driving speed otherwise).

## Configuration

Global defaults live in `config.json` at the project root. Edit this file to tune behaviour without touching code:

```json
{
    "loipen_radius_m": 10000,
    "loipen_cache_ttl_h": 24,
    "house_cache_ttl_h": 24,
    "fewo_cooldown_s": [20, 45]
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `loipen_radius_m` | `10000` | Overpass API search radius (metres) for Nordic ski trails |
| `loipen_cache_ttl_h` | `24` | Hours before the Loipen cache entry expires |
| `house_cache_ttl_h` | `24` | Hours before the `cache/houses.json` fallback cache is considered stale |
| `fewo_cooldown_s` | `[20, 45]` | Random delay range (seconds) between consecutive fewo-direkt scrapes |

Any key from the table above can also be added directly to a house entry in `input.json` to override the global value for that house only:

```json
{ "name": "House in remote valley", "loipen_radius_m": 15000 }
```

## How It Works

1. **House scraping** — four parsers are supported, selected automatically by URL:
   - **fewo-direkt.de / booking.com** — uses a real, non-headless Chrome (`undetected-chromedriver`, the bundled Chrome for Testing in `webdriver/`) to get past bot detection. The browser keeps its own user-agent: faking a different OS (e.g. a Windows user-agent on Linux Chrome) makes fewo-direkt's DataDome answer every page with a slider captcha. For fewo-direkt, the session is warmed up by visiting the homepage first (cookie consent, human-like scrolling) before navigating to the listing, and a configurable random cooldown (`fewo_cooldown_s` in `config.json`, default 20–45 s) is applied between houses. Only if Chrome cannot start does fewo fall back to `curl_cffi` (Chrome TLS fingerprint impersonation), which gets the page but not the price. Extracts location, price, bedroom count, bed configuration, sauna availability, and more.
     - fewo-direkt: address from the page's schema.org data (`addressLocality`/`addressCountry`), overall rating from the rating tile, availability from the offers box ("Deine Daten sind verfügbar" / "leider nicht verfügbar").
     - booking.com: when the room table says "nicht verfügbar", the house is `Unavailable` and gets **no** price — the page then lists "Ab € …" offers for other dates and other houses, which must not be taken. When free, the price is the cheapest rate for the largest group size in the room table. m² comes from the highlights, beds and bathrooms from the room table. Bed entries containing "Schlafsofa" are flagged with ⚠️ in the UI. When no structured bedroom blocks are found, falls back to parsing the free-text description (`[data-stid="content-markup"]`) using the same fluid-text parser as interhome. If fewo-direkt returns a bot/rate-limit page (DataDome challenge, "Warum diese Kontrolle?", etc.), the scrape is aborted and the previous result from `cache/houses.json` is used instead, provided it is less than `house_cache_ttl_h` hours old.
   - **huetten.com** — uses plain `requests` (no browser needed); extracts all fields from static HTML and the JSON-LD `LodgingBusiness` block. Price is resolved from the on-page weekly price table by matching the checkin date and person count parsed from the URL fragment (`#/vsc.php?calendar_date_from=…&persons_adults=…`). Nebenkosten (additional costs) are parsed separately and folded into the displayed Gesamtpreis; Kaution is excluded. Prices for both 8 and 10 persons are looked up from the table directly.
   - **interhome.de** (now part of HomeToGo) — uses Chrome (Selenium) because the site is a React SPA. Waits for the availability badge (`[data-test="available-badge"]`) to settle after the background pricing API call completes (up to 45 s, plus up to 20 s for a first "ausgebucht" to flip to "verfügbar"), then grabs the total price directly from the live DOM element. The URL is cut down to `arrival`, `duration`, `adults`, `persons`, `children`: a link copied from a search carries offer context (`pCon`, `sd`, `searchId`, `offerId`, `clickId`, …) that pins the page to the old search — with it, a free house showed "Für deine Daten leider ausgebucht". Name, address, bedrooms, bathrooms, persons, m², rating and photo come from the page's state JSON (`<script data-rtk-endpoint="rentalOfferDetails">`), read from the first page snapshot because the page removes it after loading. Sauna is read from the amenity list only (the description also lists the village's sauna). Availability is detected from the badge text: "verfügbar" → `Available`, "ausgebucht" → `Unavailable`. Room count and bed configuration are parsed from the rendered description text (`[data-test="rental-description"]`) using a shared fluid-text parser that handles patterns like `"3 abgeschrägte Zimmer, jedes Zimmer mit 1 franz. Bett (160cm)"`.

   Shared parser utilities (country normalisation, rating normalisation, JSON-LD parsing, bed description cleaning, fluid-text room config parsing) live in `parsers/common.py`.

   Fields present in `input.json` override scraped values for all brokers.

   **Photos:** every house parser also scrapes the listing's main photo (booking.com `og:image`, fewo-direkt schema.org `image`, huetten.com JSON-LD `image`, interhome state JSON). An `image_url` in `input.json` still wins — but the scraper first checks that it answers with an image. Listing photos get deleted (a booking.com photo URL that worked before now returns 404), so a dead `image_url` falls back to the scraped photo, with a log line.
2. **Sled run scraping** — two parsers are supported, selected automatically by URL:
   - **rodelwelten.com** — fetches `/detail/` pages with `requests`/BeautifulSoup and parses the details table (length, elevation, night sledding, public transport, sled rental, etc.). Hut/Alm info (name + website) is extracted from `div.hut-content` blocks. GPX tracks are downloaded (or assembled from inline JSON segments) and downsampled for map display. Cached for 24 hours in `cache/sled_runs.json`.
   - **outdooractive.com** — parses JSON-LD structured data embedded in the page for length, elevation, difficulty, ascent aid, and operator. Additional fields (night sledding, public transport, sled rental, opening hours) are inferred from page text. The GPX track is downloaded via the public `download.tour.gpx?i={id}` endpoint and downsampled. Cached for 24 hours in `cache/outdooractive.json`.
3. **Nordic ski trail (Loipen) discovery** — runs automatically for every house that has `lat`/`lon` coordinates. Queries the [Overpass API](https://overpass-api.de/) for all OSM elements tagged `piste:type=nordic` within the configured radius (default 10 km, overridable per house with `loipen_radius_m`). Results are deduplicated by name (relations take priority over individual ways), sorted by distance to the house, and cached for the configured TTL (default 24 h) in `cache/loipen.json` (keyed by rounded coordinates). Each trail carries name, difficulty, grooming style, calculated length (Haversine), and a downsampled track for map display. Coverage depends on OpenStreetMap data — areas where Loipen have not yet been mapped with `piste:type=nordic` will return no results.
4. **Date injection** — known date query parameters (`chkin`, `chkout`, `startDate`, `endDate`, `checkin`, `checkout`, `arrival`, etc.) in house URLs are replaced with the configured trip dates before scraping.
5. **Rendering** — the Jinja2 template in `templates/index.html` renders all trips and houses into a card-based comparison layout with interactive Leaflet maps.
   - Prices are normalised to `XXXX €` format. Per-person price is shown for 8 persons; a 10-person row is shown when a separate 10-person price is available or when the scraped max-person count is ≥ 10. For fewo/booking the 10-person price is estimated as the 8-person price +2%.
   - Ratings are normalised to `X.X (N Bewertungen)` format on a 0–10 scale regardless of the source scale (fewo-direkt 0–10, booking.com 0–10, huetten.com 0–100, interhome 0–5). Normalisation is applied at parse time via `parsers/common.py:normalize_rating()`.
   - Address is normalised to "City, Country" format with a country flag emoji. Swiss canton names (e.g. "Canton of Bern") are resolved to "Schweiz".
   - A data quality warning box appears at the top if the scraped bedroom count does not match the number of `room_config` entries; clicking a house name jumps directly to its card.
   - The last-updated timestamp and version are shown as chips below the page title, alongside the language switcher and a **persons filter** (👥 dropdown, range 6–12). Selecting a minimum hides all house cards that accommodate fewer persons; houses with no persons data are always shown.
   - The trips area scrolls horizontally on desktop. A sticky scrollbar is pinned to the bottom of the viewport so it remains accessible without scrolling to the end of the page.
   - Unavailable houses (`time == "Unavailable"`) are visually marked with a diagonal red stripe pattern on the header and photo, plus a centred badge.

## Supported Sources

| Type      | Supported sites                                          |
|-----------|----------------------------------------------------------|
| Houses    | fewo-direkt.de, booking.com, huetten.com, interhome.de   |
| Sled runs | rodelwelten.com (detail pages only), outdooractive.com   |
| Loipen    | OpenStreetMap via Overpass API (auto, no URLs needed)    |

## Maps

Each house card shows two types of maps:

- **House map** — shows the house location (🏠) plus any configured POIs (train stations, supermarkets, etc.). Sled run tracks are overlaid in blue, Nordic ski trails (Loipen) in green dashed lines. If a `train_track` is defined (see below), it is rendered as a red dashed line with a tooltip explaining the route.
- **Sled run maps** — shown per sled run (expand to view); displays the GPX route in blue with the house location (🏠) for distance reference.
- **Loipen maps** — shown per trail (expand to view); displays the OSM trail geometry in green dashed style with the house location (🏠) for distance reference.

The overview map at the top groups houses by location. If two or more houses share the same coordinates (to 5 decimal places), they are merged into a single marker with a split-colour gradient and a tooltip listing each house name, trip, and price separately. Sled run tracks are drawn per trip colour; train tracks are drawn in red dashed style.

### Train track overlay

For car-free villages (e.g. Wengen), a `train_track` field can be added to a house in `input.json` to draw the rail connection on the map:

```json
{
  "name": "Ferienhaus Arche - Wengen",
  "train_track": [
    [46.59852, 7.90809],
    [46.60545, 7.92065]
  ]
}
```

The track is rendered as a **red dashed polyline** on both the overview map and the per-house card map, with a tooltip explaining that no car access is available and where to park.

## Languages

The page UI is fully translated. A language switcher is shown in the chip row below the title and persists the selection via `localStorage`. Supported locales:

| Code | Flag | Language |
|------|------|----------|
| `de-DE` | 🇩🇪 | German |
| `en-GB` | 🇬🇧 | English |
| `fr-FR` | 🇫🇷 | French |
| `nl-NL` | 🇳🇱 | Dutch |
| `bar-DE` | 🥨 | Bavarian dialect |
| `bar-AT` | 🇦🇹 | Tyrolean dialect |
| `gsw-CH` | 🇨🇭 | Swiss German |
| `nds-DE` | ⚓ | Hamburg dialect (Low German / Hamburgisch) |
| `pfl-DE` | ☀️ | Karlsruhe dialect (Badisch-Pfälzisch) |

The server-side default is set with `--lang` (default: `bar-DE`). All translations are embedded in the page at generation time so switching is instant.

## Comparing Houses

Each house card has a round checkbox above it. Selecting two or more houses shows a sticky bar at the bottom of the page with a **Compare** button. Clicking it opens a modal table showing all key fields side by side.

- Rows where all selected houses have the **same value** are shown with a subtle grey background.
- Rows where values **differ** are highlighted in yellow.
- The **"Show differences only"** toggle hides identical rows so only the differences remain.

The compare bar label, button text, modal title, and diff-only checkbox are all fully translated and switch instantly with the language selector.

## Data Quality Warnings

If the scraped bedroom count (`rooms`) does not match the number of entries in `room_config`, a highlighted warning box is shown at the top of the page listing all affected houses. Each house name is a clickable link that jumps to the relevant card. The affected card header also shows a small translated rooms badge. Bed entries containing "Schlafsofa" are flagged inline with ⚠️.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Runs on every push via GitHub Actions (`.github/workflows/tests.yml`, Python 3.11–3.13) and before
every `/deploy` (`TEST_CMD` in `deploy.config.example`). The parser tests
(`tests/test_parsers_house_pages.py`) use trimmed copies of the current fewo-direkt.de and
booking.com page layouts — when a site changes its markup, update them together with the parser.

## Linting

[Pylint](https://pylint.readthedocs.io/) runs automatically on every push and pull request via GitHub Actions (`.github/workflows/lint.yml`). The workflow lints `app.py` and `parsers/` and fails if the score drops below 7.0.

To run locally:

```bash
pylint app.py parsers/
```

Configuration is in `.pylintrc`. Project-specific suppressions (e.g. `too-many-locals` for scrapers, `missing-*-docstring`) are documented there.

## Notes

- House scraping requires Chrome to be running in non-headless mode to bypass bot detection. On a desktop a browser window appears during scraping; without a screen, use `xvfb-run -a` (see [Scrape vs. render](#scrape-vs-render)).
- If fewo-direkt returns a bot or rate-limit page, the scraper falls back to the last result in `cache/houses.json` (TTL: 24 hours). If no fresh cache is available the house is rendered with all fields as `N/A`.
- huetten.com is scraped with plain `requests` — no browser needed.
- Sled run map pages (e.g. `/rodelbahnen/karte`) return all `N/A` — only use `/detail/` URLs for rodelwelten.com.
- For outdooractive.com, use route detail URLs in the form `/de/route/rodeln/.../ID/`. The URL fragment (everything after `#`) is ignored.
- The Flask `@app.route('/')` enables a live server mode (`flask run`) that scrapes on every request, but the primary workflow is static generation via `python app.py`.
- After adding new sled runs, run with `--force` to bypass the cache and pick up newly scraped fields (e.g. huts).
- When using `--house` for a house that appears in multiple trips, each unique date range is scraped separately so prices are correct per trip.
- `room_config` entries should contain only the bed description (e.g. `"1 Doppelbett"`); the bedroom label and number are generated automatically by the template and translated.
- The version chip in the header resolves in order: latest GitHub release tag → latest local git tag → `dev`. Create a GitHub release to have it appear automatically.
