# Copilot Instructions

## Project Overview

**holiday-house-comparison** is a Python static site generator that compares holiday houses for sledding trips. It scrapes house listings (fewo-direkt.de, booking.com, huetten.com, interhome.de), sled runs (rodelwelten.com, outdooractive.com) and Nordic ski trails (OpenStreetMap Overpass), then renders a static HTML comparison page. `README.md` is the full reference; this file is the short version.

## Architecture

- `app.py` — orchestrator: CLI entry point, Chrome driver setup (`_make_driver()`), `scrape_house()`, `inject_dates()`, `build_trip_data()`, scrape/render split, `health.json`, Flask live-mode route
- `parsers/fewo.py` — fewo-direkt.de (Chrome required; DataDome bot detection)
- `parsers/booking.py` — booking.com (Chrome required)
- `parsers/huetten.py` — huetten.com (plain `requests`)
- `parsers/interhome.py` — interhome.de (Chrome; React SPA)
- `parsers/rodelwelten.py`, `parsers/outdooractive.py` — sled runs, 1-day file caches in `cache/`
- `parsers/loipen.py` — Overpass query for `piste:type=nordic`, cached in `cache/loipen.json`
- `parsers/common.py` — shared helpers (`EMPTY` result, country/rating normalisation, bed/room-config parsing)
- `input.json` — houses and trips (gitignored — personal data); `input.template.json` documents the shape
- `config.json` — global defaults (cache TTLs, Loipen radius, fewo cooldown)
- `templates/index.html` — Jinja2 template (Leaflet maps); UI strings in `translations/<lang>.json` (default `bar-DE`)
- `cache/houses.json` — scraped house data, the hand-off between scrape and render (gitignored)
- `public/index.html`, `public/health.json` — generated output (gitignored)
- `webdriver/` — bundled Chrome for Testing + matching chromedriver: `chrome-linux64/` + `chromedriver-linux64/` on Linux, `chrome-win64/` + `chromedriver-win64/` on Windows (gitignored)
- `.claude/skills/` — `/scrape`, `/deploy`, `/rollback`

## Scrape vs. render

- `python app.py --scrape-only` scrapes every house into `cache/houses.json` (about 8 minutes). Progress goes to `cache/houses.partial.json`; `houses.json` is replaced only when the run succeeds. It prints `[progress] N/T houses (P%)` after each house.
- `python app.py --from-cache` renders `public/` from `cache/houses.json` with no network. Both deploy paths run only this.
- Plain `python app.py` does both. `--house NAME` re-scrapes one house and patches the cache. `--broker`/`--limit` results are merged into the existing cache, never replace it.
- Without a screen, run scrapes under `xvfb-run -a` — Chrome must run non-headless.

## Key Conventions

- Never override the browser's user-agent in the Chrome path: a user-agent that does not match the real OS/browser makes DataDome show a captcha on every fewo-direkt page.
- A parser returns `None` for a bot/rate-limit page, a dict full of `'Error'` on a crash, else a dict based on `parsers.common.EMPTY`. `_scrape_one_house` treats `None` and an all-empty result (no location, address, rooms or price) as a failed scrape: cached fallback, counted in `scrape_stats`, run status `degraded`.
- Availability (`time`): `Available` (free, with price), `Unavailable` (booked out, **no** price), `check_manually` (no `house_url`), `N/A` (unknown). Never take a price from a booked-out page — booking.com then lists offers for other dates and other houses.
- House fields: `location`, `address`, `rooms`, `persons`, `sqm`, `bathrooms`, `room_config`, `price`, `time`, `rating`, `supermarket`, `train_station`, `bus_stop`, `sauna`; `N/A` is the fallback.
- Any house field can be overridden in `input.json` (house or per-trip entry); the override applies only when truthy (`house.get(field)`) and wins over scraped data — also over a scraped `Unavailable`.
- `nearest_sled_run` is input-only (not scraped); rendered only when present and non-empty.
- `sauna` is detected via `\bSauna\b` on the page text → `'Ja'` / `'Nein'`.
- Ratings are normalised to `X.X (N Bewertungen)` on a 0–10 scale at parse time (`normalize_rating`).
- Addresses are normalised to `"City, Country"` (German country names; ISO-2/ISO-3 codes and English names are mapped in `parsers/common.py`).
- `input.json` uses `sled_run_urls` (not `route_urls`); only rodelwelten `/detail/` URLs work — `/rodelbahnen/karte` returns all `N/A`.
- Sled run cache entries: `{ "fetched_at": "<iso datetime>", "data": { ... } }`. Use `--force` to bypass after adding routes or new scraped fields.
- Prices are normalised to `XXXX €` via the `normalize_price` filter. Per-person price is shown for 8 persons; the 10-person row uses `total_costs_10` when present, else the price +2 %.
- `health.json`: `last_update` is the last successful scrape in ISO 8601 UTC, not the render or deploy time.

## Tests and lint

- `.venv/bin/python -m pytest -q` (CI: `.github/workflows/tests.yml`; same command as `TEST_CMD`).
- `pylint app.py parsers/` (CI: `.github/workflows/lint.yml`, fail under 7.0).
- Parser tests in `tests/test_parsers_house_pages.py` use trimmed copies of the live page layouts — update them together with the parser when a site changes its markup.
