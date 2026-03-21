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
- Sled run fields: `length`, `night_sleighing`, `public_transport`, `walking_time`, `sled_rental`, `avalanche_danger`, `height_top`, `height_bottom`, `elevation_diff`, `slope`, `separate_ascent`, `ascent_aid`, `difficulty`, `operator`, `opening_hours`, `track`
- POI types for house maps: `train`, `supermarket` (custom strings fall back to a pin icon)
- Do not scrape `/rodelbahnen/karte` — returns all `N/A`
- Template is intentionally in German (`lang="de"`)
- `python app.py --from-cache` re-renders HTML from existing `public/data.json` without scraping
