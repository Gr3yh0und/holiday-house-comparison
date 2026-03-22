# Copilot Instructions

## Project Overview

**holiday-house-comparison** is a Python static site generator that compares holiday houses for sledding trips. It scrapes house listings (fewo-direkt.de, booking.com) and sled run details (rodelwelten.com), then renders a static HTML comparison page.

## Architecture

- `app.py` — orchestrator: CLI entry point, Selenium driver setup, `scrape_house()`, `inject_dates()`, `build_trip_data()`, Flask live-mode route
- `parsers/fewo.py` — fewo-direkt.de scraper (Selenium required; site has bot detection)
- `parsers/booking.py` — booking.com scraper (Selenium required)
- `parsers/rodelwelten.py` — sled run scraper with 1-day file cache in `cache/sled_runs.json`
- `input.json` — trip/house/sled run configuration (gitignored — contains personal data)
- `input.template.json` — example configuration template
- `templates/index.html` — Jinja2 template (German labels, Leaflet maps)
- `public/index.html`, `public/data.json` — generated output (gitignored)
- `webdriver/chromedriver-win64/chromedriver.exe` — local ChromeDriver (gitignored)
- `requirements.txt` — Flask, requests, beautifulsoup4, selenium, undetected-chromedriver, setuptools

## Key Conventions

- `input.json` uses `sled_run_urls` (not `route_urls`)
- Cache entries: `{ "fetched_at": "<iso datetime>", "data": { ... } }`
- House scraping is best-effort — `N/A` is an acceptable fallback
- Scraped house fields: `address`, `rooms`, `persons`, `sqm`, `bathrooms`, `room_config`, `price`, `time`, `rating`, `supermarket`, `train_station`, `sauna`
- Any scraped house field can be overridden in `input.json` by adding it directly to the house entry; the override is applied only when the value is truthy (`house.get(field)`)
- `sauna` is detected via `\bSauna\b` regex on the rendered page text → `'Ja'` / `'Nein'`; defaults to `'N/A'` if the page was not scraped
- Sled run fields: `length`, `night_sleighing`, `public_transport`, `walking_time`, `sled_rental`, `avalanche_danger`, `height_top`, `height_bottom`, `elevation_diff`, `slope`, `separate_ascent`, `ascent_aid`, `difficulty`, `operator`, `opening_hours`, `track`, `huts`
- `huts` is a list of `{"name": str, "url": str | None}` dicts scraped from `div.hut-content` blocks on rodelwelten detail pages
- POI types for house maps: `train`, `supermarket` (custom strings fall back to a pin icon)
- Do not scrape `/rodelbahnen/karte` — returns all `N/A`; only use `/detail/` URLs
- Template is intentionally in German (`lang="de"`); all UI strings are in `translations/de.json`
- Prices are normalised to `XXXX€` format via the `normalize_price` Jinja2 filter; `price_per_person` divides by a fixed integer and returns the same format
- Per-person price is shown for 8 persons always; a second row for 10 persons appears only when `house.persons == "10"`
- `python app.py --from-cache` re-renders HTML from existing `public/data.json` without scraping
- Use `--force` to bypass the sled run cache after adding new routes or new scraped fields
