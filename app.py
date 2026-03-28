import argparse
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from flask import Flask, render_template

from parsers import booking, fewo, huetten, interhome, rodelwelten, outdooractive, loipen as loipen_parser
from parsers.common import EMPTY as _PARSER_EMPTY

app = Flask(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'sled_runs.json')
CACHE_FILE_OA = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'outdooractive.json')
LOIPEN_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'loipen.json')
TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translations')

CHROMEDRIVER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'webdriver', 'chromedriver-win64', 'chromedriver.exe'
)
CHROME_BINARY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'webdriver', 'chrome-win64', 'chrome.exe'
)


def load_translations(lang='de') -> dict:
    path = os.path.join(TRANSLATIONS_DIR, f'{lang}.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_all_translations() -> dict:
    result = {}
    for fname in os.listdir(TRANSLATIONS_DIR):
        if fname.endswith('.json'):
            lang_code = fname[:-5]
            with open(os.path.join(TRANSLATIONS_DIR, fname), encoding='utf-8') as f:
                result[lang_code] = json.load(f)
    return result


_translations = load_translations('bar-DE')
_all_translations = load_all_translations()
_lang = 'bar-DE'


def get_version():
    """Return the latest GitHub release tag, falling back to the latest git tag, then 'dev'."""
    try:
        resp = requests.get(
            'https://api.github.com/repos/Gr3yh0und/holiday-house-comparison/releases/latest',
            headers={'Accept': 'application/vnd.github+json'},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get('tag_name', '')
    except Exception:
        pass
    try:
        import subprocess
        tag = subprocess.check_output(
            ['git', 'describe', '--tags', '--abbrev=0'],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if tag:
            return tag
    except Exception:
        pass
    return 'dev'


def _parse_price(price):
    """Return price as float, or None if unparseable."""
    try:
        p = price.strip().lstrip('€').rstrip('€').strip()
        p = p.replace('\xa0', '').replace('\u202f', '').replace(' ', '')
        p = p.replace('.', '').replace(',', '.')
        return float(p)
    except (ValueError, AttributeError, TypeError):
        return None


_COUNTRY_FLAGS = {
    'Österreich': '🇦🇹',
    'Deutschland': '🇩🇪',
    'Schweiz': '🇨🇭',
    'Italien': '🇮🇹',
    'Frankreich': '🇫🇷',
}


_BROKER_NAMES = {
    'fewo-direkt.de': 'fewo-direkt',
    'booking.com': 'Booking.com',
    'huetten.com': 'huetten.com',
}


@app.template_filter('broker_name')
def broker_name(url):
    for domain, name in _BROKER_NAMES.items():
        if domain in url:
            return name
    return ''


@app.template_filter('price_inflate')
def price_inflate(price, pct):
    """Increase a price string by pct percent, return rounded to full euros."""
    val = _parse_price(price)
    if val is None:
        return price
    return f"{round(val * (1 + pct / 100))} €"


@app.template_filter('country_flag')
def country_flag(address):
    """Return the flag emoji for the country at the end of an 'City, Country' address."""
    if not address or address in ('N/A', 'Error'):
        return ''
    country = address.rsplit(',', 1)[-1].strip()
    if country.startswith('Canton of '):
        return _COUNTRY_FLAGS.get('Schweiz', '')
    return _COUNTRY_FLAGS.get(country, '')


@app.template_filter('normalize_price')
def normalize_price(price):
    if price in (None, 'N/A', 'Error'):
        return price
    val = _parse_price(price)
    return f"{int(val)} €" if val is not None else price


@app.template_filter('price_per_person')
def price_per_person(price, persons):
    try:
        price_val = _parse_price(price)
        if price_val is None:
            return None
        persons_val = int(persons)
        if persons_val == 0:
            return None
        return f"{round(price_val / persons_val)} €"
    except (ValueError, AttributeError, TypeError):
        return None


@app.template_filter('dedate')
def dedate(value):
    try:
        d = datetime.strptime(value, '%Y-%m-%d')
        weekdays = _translations.get('weekdays', [])
        day_name = weekdays[d.weekday()] if weekdays else ''
        return f"{d.strftime('%d.%m.%Y')} ({day_name})" if day_name else d.strftime('%d.%m.%Y')
    except (ValueError, TypeError):
        return value


def _fetch_loipen(lat, lon, force_refresh=False):
    """Return nearby Nordic ski trails, cached for 24 h per coordinate."""
    cache_key = f'{lat:.4f},{lon:.4f}'
    if not force_refresh and os.path.exists(LOIPEN_CACHE_FILE):
        with open(LOIPEN_CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
        entry = cache.get(cache_key)
        if entry:
            age = (datetime.now() - datetime.strptime(
                entry['fetched_at'], '%Y-%m-%d %H:%M'
            )).total_seconds()
            if age < 86400:
                print(f'  [loipen] cache hit for {cache_key} ({len(entry["loipen"])} trails)')
                return entry['loipen']
    print(f'  [loipen] fetching Overpass for {cache_key} ...')
    trails = loipen_parser.fetch(lat, lon)
    print(f'  [loipen] found {len(trails)} trails')
    cache = {}
    if os.path.exists(LOIPEN_CACHE_FILE):
        with open(LOIPEN_CACHE_FILE, encoding='utf-8') as f:
            cache = json.load(f)
    cache[cache_key] = {
        'fetched_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'loipen': trails,
    }
    with open(LOIPEN_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return trails


def _make_driver():
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    if os.path.exists(CHROME_BINARY_PATH):
        options.binary_location = CHROME_BINARY_PATH
        print(f'  [driver] using bundled Chrome: {CHROME_BINARY_PATH}')
    else:
        print('  [driver] bundled Chrome not found, falling back to system Chrome')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--window-position=-32000,0')
    driver_path = CHROMEDRIVER_PATH if os.path.exists(CHROMEDRIVER_PATH) else None
    return uc.Chrome(options=options, driver_executable_path=driver_path)


BROKER_DOMAINS = {
    'fewo': 'fewo-direkt.de', 'booking': 'booking.com',
    'huetten': 'huetten.com', 'interhome': 'interhome.',
}


def scrape_house(url, driver=None):
    if 'fewo-direkt.de' in url:
        return fewo.scrape(url, driver)
    if 'booking.com' in url:
        return booking.scrape(url, driver)
    if 'huetten.com' in url:
        return huetten.scrape(url, driver)
    if 'interhome.' in url:
        return interhome.scrape(url, driver)
    return {k: 'N/A' for k in ['location', 'address', 'rooms', 'sqm', 'bathrooms',
                                 'room_config', 'price', 'time', 'train_station',
                                 'supermarket', 'rating', 'persons']}


def inject_dates(url, checkin, checkout):
    """Replace known date parameters in a URL with the given checkin/checkout dates."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    date_map = {
        'chkin': checkin, 'chkout': checkout,
        'd1': checkin, 'd2': checkout,
        'startDate': checkin, 'endDate': checkout,
        'checkin': checkin, 'checkout': checkout,
        'arrival': checkin,  # interhome
    }
    for key, val in date_map.items():
        if key in params:
            params[key] = [val]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


def _render_html(title, trips, updated_at, version):
    return render_template(
        'index.html',
        t=_translations, all_translations=_all_translations, lang=_lang,
        title=title, trips=trips, updated_at=updated_at, version=version,
    )


def _load_cached_house(name, checkin, checkout):
    """Return a previously scraped house from public/data.json if it exists and is < 24 h old."""
    cache_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public', 'data.json')
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path, encoding='utf-8') as f:
            cached = json.load(f)
        updated_at = datetime.strptime(cached.get('updated_at', ''), '%Y-%m-%d %H:%M')
        if (datetime.now() - updated_at).total_seconds() > 86400:
            return None
        for trip in cached.get('trips', []):
            for h in trip.get('houses', []):
                if h.get('name') == name and h.get('checkin') == checkin and h.get('checkout') == checkout:
                    return h
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    return None


def _scrape_one_house(house, trip_checkin, trip_checkout, driver=None, force_refresh=False):
    house_url = (
        inject_dates(house['house_url'], trip_checkin, trip_checkout)
        if trip_checkin and trip_checkout
        else house['house_url']
    )
    print(f"Scraping house: {house['name']} ({house_url[:60]}...)")
    house_info = scrape_house(house_url, driver=driver)
    if house_info is None:
        cached = _load_cached_house(house['name'], trip_checkin, trip_checkout)
        if cached:
            print("  -> bot/scrape failure, using cached data from public/data.json")
            return cached
        print("  -> bot/scrape failure, no usable cache — returning empty result")
        house_info = dict(_PARSER_EMPTY, room_config=[])
    house_info['name'] = house['name']
    house_info['house_url'] = house_url
    for field in ('address', 'rooms', 'persons', 'sqm', 'bathrooms',
                  'room_config', 'price', 'time', 'rating',
                  'supermarket', 'train_station', 'sauna', 'nearest_sled_run', 'notes'):
        if house.get(field):
            house_info[field] = house[field]
    if 'image_url' in house:
        house_info['image_url'] = house['image_url']
    if 'lat' in house and 'lon' in house:
        house_info['lat'] = house['lat']
        house_info['lon'] = house['lon']
    if 'direct_url' in house:
        house_info['direct_url'] = house['direct_url']
    if 'pois' in house:
        house_info['pois'] = house['pois']
    if 'train_track' in house:
        house_info['train_track'] = house['train_track']
    house_info['checkin'] = trip_checkin
    house_info['checkout'] = trip_checkout
    house_info['sled_runs'] = []
    for sled_run_url in house.get('sled_run_urls', []):
        if 'outdooractive.com' in sled_run_url:
            sled_run_info = outdooractive.scrape(sled_run_url, force_refresh=force_refresh)
        else:
            sled_run_info = rodelwelten.scrape(sled_run_url, force_refresh=force_refresh)
        sled_run_info['url'] = sled_run_url
        if not sled_run_info.get('name') or sled_run_info['name'] == 'N/A':
            sled_run_info['name'] = sled_run_url.rstrip('/').split('/')[-1].replace('-', ' ').title()
        house_info['sled_runs'].append(sled_run_info)
    def _length_m(length):
        m = re.search(r'([\d.,]+)\s*(km|m)\b', length, re.IGNORECASE)
        if not m:
            return 0
        val = float(m.group(1).replace(',', '.'))
        return val * 1000 if m.group(2).lower() == 'km' else val
    house_info['sled_runs'].sort(key=lambda r: _length_m(r['length']), reverse=True)
    if house.get('lat') and house.get('lon'):
        house_info['loipen'] = _fetch_loipen(
            house['lat'], house['lon'], force_refresh=force_refresh
        )
    else:
        house_info['loipen'] = []
    return house_info


def _normalize_input(data):
    """Convert house-centric input format to trip-centric format if needed.

    House-centric format has a top-level 'houses' list where each house
    contains a 'trips' array with per-trip name/dates (and optional overrides).
    This is converted to the canonical trip-centric format used throughout the app.
    """
    if 'trips' in data:
        return data
    trips = {}
    trip_order = []
    for house in data.get('houses', []):
        house_base = {k: v for k, v in house.items() if k != 'trips'}
        for trip_entry in house.get('trips', []):
            name = trip_entry['name']
            if name not in trips:
                trips[name] = {
                    'name': name,
                    'checkin': trip_entry.get('checkin', ''),
                    'checkout': trip_entry.get('checkout', ''),
                    'houses': [],
                }
                trip_order.append(name)
            merged = dict(house_base)
            for key, val in trip_entry.items():
                if key not in ('name', 'checkin', 'checkout'):
                    merged[key] = val
            trips[name]['houses'].append(merged)
    return {'title': data.get('title', ''), 'trips': [trips[n] for n in trip_order]}


def build_trip_data(data, driver=None, force_refresh=False, broker_filter=None, limit=None):
    trips = []
    scraped = 0
    for trip in data['trips']:
        trip_checkin = trip.get('checkin', '')
        trip_checkout = trip.get('checkout', '')
        houses = []
        for house in trip['houses']:
            if limit is not None and scraped >= limit:
                break
            house_url = (
                inject_dates(house['house_url'], trip_checkin, trip_checkout)
                if trip_checkin and trip_checkout
                else house['house_url']
            )
            is_skipped = broker_filter and BROKER_DOMAINS.get(broker_filter) not in house_url
            if is_skipped:
                print(f"Skipping house: {house['name']} (not a {broker_filter} URL)")
                continue
            houses.append(_scrape_one_house(
                house, trip_checkin, trip_checkout, driver=driver, force_refresh=force_refresh
            ))
            scraped += 1
        trips.append({
            'name': trip.get('name', ''), 'checkin': trip_checkin,
            'checkout': trip_checkout, 'houses': houses,
        })
    return trips


@app.route('/')
def index():
    with open('input.json', encoding='utf-8') as f:
        data = _normalize_input(json.load(f))
    return _render_html(
        title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'),
        trips=build_trip_data(data),
        updated_at=datetime.now().strftime('%Y-%m-%d %H:%M'),
        version=get_version(),
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Force re-fetch all sled runs, ignoring cache')
    parser.add_argument(
        '--broker', choices=['fewo', 'booking', 'huetten', 'interhome'],
        help='Only scrape houses from this broker',
    )
    parser.add_argument('--limit', type=int, help='Maximum number of houses to scrape')
    parser.add_argument(
        '--from-cache', action='store_true',
        help='Re-render HTML from existing public/data.json without scraping',
    )
    parser.add_argument(
        '--house', type=str, metavar='NAME',
        help='Scrape only this house (substring match), patch data.json and re-render',
    )
    parser.add_argument(
        '--lang', default='bar-DE',
        choices=['de-DE', 'en-GB', 'fr-FR', 'nl-NL', 'bar-DE', 'bar-AT', 'gsw-CH', 'nds-DE', 'pfl-DE'],
        help='Language for the rendered page (default: bar-DE)',
    )
    args = parser.parse_args()

    _translations = load_translations(args.lang)
    _all_translations = load_all_translations()
    _lang = args.lang

    start_time = time.time()
    version = get_version()

    if args.house:
        needle = args.house.lower()
        with open('input.json', encoding='utf-8') as f:
            data = _normalize_input(json.load(f))
        # Find all matching houses across trips
        matches = [
            (trip, house)
            for trip in data['trips']
            for house in trip['houses']
            if needle in house['name'].lower()
        ]
        if not matches:
            print(f"No house found matching '{args.house}'. Available houses:")
            for trip in data['trips']:
                for house in trip['houses']:
                    print(f"  [{trip.get('name', '')}] {house['name']}")
            raise SystemExit(1)
        # Allow multiple matches only when all share the exact same name (same house, multiple trips)
        unique_names = {h['name'] for _, h in matches}
        if len(unique_names) > 1:
            print(f"Multiple different houses match '{args.house}':")
            for trip, house in matches:
                print(f"  [{trip.get('name', '')}] {house['name']}")
            print("Use a more specific name.")
            raise SystemExit(1)

        print(f"Matched {len(matches)} entr{'y' if len(matches)==1 else 'ies'}: {matches[0][1]['name']}")
        for trip, house in matches:
            print(f"  [{trip.get('name', '')}]")

        rodelwelten.load_cache(CACHE_FILE)
        outdooractive.load_cache(CACHE_FILE_OA)

        # Group matches by unique (checkin, checkout) — prices differ per date range
        date_groups = {}
        for trip, house in matches:
            key = (trip.get('checkin', ''), trip.get('checkout', ''))
            date_groups.setdefault(key, (trip, house))

        driver = None
        try:
            driver = _make_driver()
        except Exception as e:
            print(f"Selenium unavailable ({e}), falling back to requests")
        try:
            scraped = {}  # key -> fresh house data
            for key, (trip, house) in date_groups.items():
                checkin, checkout = key
                print(f"Scraping for dates {checkin} → {checkout}")
                scraped[key] = _scrape_one_house(house, checkin, checkout, driver=driver, force_refresh=args.force)
        finally:
            if driver:
                driver.quit()

        rodelwelten.save_cache()
        outdooractive.save_cache()

        with open('public/data.json', encoding='utf-8') as f:
            cached = json.load(f)
        replaced = 0
        house_name = matches[0][1]['name']
        for t in cached['trips']:
            key = (t.get('checkin', ''), t.get('checkout', ''))
            if key not in scraped:
                continue
            for i, h in enumerate(t['houses']):
                if h['name'] == house_name:
                    t['houses'][i] = scraped[key]
                    replaced += 1
        if replaced == 0:
            print(f"House '{house_name}' not found in public/data.json — appending to first matching trip.")
            first_trip, first_house = matches[0]
            key = (first_trip.get('checkin', ''), first_trip.get('checkout', ''))
            for t in cached['trips']:
                if t['name'] == first_trip.get('name', ''):
                    t['houses'].append(scraped[key])
                    replaced += 1
                    break
        if replaced == 0:
            print("Warning: could not find matching trip in public/data.json either.")
        else:
            print(f"Patched {replaced} entr{'y' if replaced==1 else 'ies'} in public/data.json")

        updated_at = datetime.now().strftime('%Y-%m-%d %H:%M')
        cached['updated_at'] = updated_at
        with open('public/data.json', 'w', encoding='utf-8') as f:
            json.dump(cached, f, ensure_ascii=False, indent=2)
        print("Updated public/data.json")

        with app.app_context():
            html_content = _render_html(
                title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'),
                trips=cached['trips'], updated_at=updated_at, version=version,
            )
        with open('public/index.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"Done in {time.time() - start_time:.1f}s")
        raise SystemExit(0)

    if args.from_cache:
        with open('public/data.json', encoding='utf-8') as f:
            cached = json.load(f)
        with open('input.json', encoding='utf-8') as f:
            data = _normalize_input(json.load(f))
        with app.app_context():
            html_content = _render_html(
                title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'),
                trips=cached['trips'], updated_at=cached['updated_at'], version=version,
            )
        with open('public/index.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"HTML re-rendered from cache in {time.time() - start_time:.1f}s")
        raise SystemExit(0)

    rodelwelten.load_cache(CACHE_FILE)
    outdooractive.load_cache(CACHE_FILE_OA)

    with open('input.json', encoding='utf-8') as f:
        data = _normalize_input(json.load(f))

    driver = None
    try:
        driver = _make_driver()
        print("Using Selenium (headless Chrome) for JS-rendered pages")
    except Exception as e:
        print(f"Selenium unavailable ({e}), falling back to requests")

    try:
        trip_data = build_trip_data(
            data, driver=driver, force_refresh=args.force,
            broker_filter=args.broker, limit=args.limit,
        )
    finally:
        if driver:
            driver.quit()

    rodelwelten.save_cache()
    outdooractive.save_cache()

    updated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    with open('public/data.json', 'w', encoding='utf-8') as f:
        json.dump({'updated_at': updated_at, 'trips': trip_data}, f, ensure_ascii=False, indent=2)
    print("Data saved to public/data.json")

    with app.app_context():
        html_content = _render_html(
            title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'),
            trips=trip_data, updated_at=updated_at, version=version,
        )

    with open('public/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)

    print("Static site generated in public/index.html")
    print(f"Done in {time.time() - start_time:.1f}s")
