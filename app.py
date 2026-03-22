import argparse
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from flask import Flask, render_template

from parsers import booking, fewo, rodelwelten

app = Flask(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache', 'sled_runs.json')
TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'translations')

CHROMEDRIVER_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    'webdriver', 'chromedriver-win64', 'chromedriver.exe'
)


def load_translations(lang='de') -> dict:
    path = os.path.join(TRANSLATIONS_DIR, f'{lang}.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


_translations: dict = load_translations()


def _parse_price(price):
    """Return price as float, or None if unparseable."""
    try:
        p = price.strip().lstrip('€').rstrip('€').strip()
        p = p.replace('\xa0', '').replace('\u202f', '').replace(' ', '')
        p = p.replace('.', '').replace(',', '.')
        return float(p)
    except (ValueError, AttributeError, TypeError):
        return None


@app.template_filter('normalize_price')
def normalize_price(price):
    if price in (None, 'N/A', 'Error'):
        return price
    val = _parse_price(price)
    return f"{int(val)}€" if val is not None else price


@app.template_filter('price_per_person')
def price_per_person(price, persons):
    try:
        price_val = _parse_price(price)
        if price_val is None:
            return None
        persons_val = int(persons)
        if persons_val == 0:
            return None
        return f"{round(price_val / persons_val)}€"
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


def _make_driver():
    import undetected_chromedriver as uc

    options = uc.ChromeOptions()
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--window-size=1920,1080')
    options.add_argument('--window-position=-32000,0')
    return uc.Chrome(options=options)


BROKER_DOMAINS = {'fewo': 'fewo-direkt.de', 'booking': 'booking.com'}


def scrape_house(url, driver=None):
    if 'fewo-direkt.de' in url:
        return fewo.scrape(url, driver)
    if 'booking.com' in url:
        return booking.scrape(url, driver)
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
    }
    for key, val in date_map.items():
        if key in params:
            params[key] = [val]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


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
            house_url = inject_dates(house['house_url'], trip_checkin, trip_checkout) if trip_checkin and trip_checkout else house['house_url']
            is_skipped = broker_filter and BROKER_DOMAINS.get(broker_filter) not in house_url
            if is_skipped:
                print(f"Skipping house: {house['name']} (not a {broker_filter} URL)")
                continue
            print(f"Scraping house: {house['name']} ({house_url[:60]}...)")
            house_info = scrape_house(house_url, driver=driver)
            scraped += 1
            house_info['name'] = house['name']
            house_info['house_url'] = house_url
            for field in ('address', 'rooms', 'persons', 'sqm', 'bathrooms',
                          'room_config', 'price', 'time', 'rating',
                          'supermarket', 'train_station'):
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
            house_info['checkin'] = trip_checkin
            house_info['checkout'] = trip_checkout
            house_info['sled_runs'] = []
            for sled_run_url in house.get('sled_run_urls', []):
                sled_run_info = rodelwelten.scrape(sled_run_url, force_refresh=force_refresh)
                sled_run_info['url'] = sled_run_url
                if not sled_run_info.get('name') or sled_run_info['name'] == 'N/A':
                    sled_run_info['name'] = sled_run_url.rstrip('/').split('/')[-1].replace('-', ' ').title()
                house_info['sled_runs'].append(sled_run_info)
            house_info['sled_runs'].sort(
                key=lambda r: int(re.sub(r'[^\d]', '', r['length'])) if re.search(r'\d', r['length']) else 0,
                reverse=True,
            )
            houses.append(house_info)
        trips.append({'name': trip.get('name', ''), 'checkin': trip_checkin, 'checkout': trip_checkout, 'houses': houses})
    return trips


@app.route('/')
def index():
    with open('input.json', encoding='utf-8') as f:
        data = json.load(f)
    return render_template('index.html', t=_translations, title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'), trips=build_trip_data(data), updated_at=datetime.now().strftime('%Y-%m-%d %H:%M'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Force re-fetch all sled runs, ignoring cache')
    parser.add_argument('--broker', choices=['fewo', 'booking'], help='Only scrape houses from this broker')
    parser.add_argument('--limit', type=int, help='Maximum number of houses to scrape')
    parser.add_argument('--from-cache', action='store_true', help='Re-render HTML from existing public/data.json without scraping')
    args = parser.parse_args()

    start_time = time.time()

    if args.from_cache:
        with open('public/data.json', encoding='utf-8') as f:
            cached = json.load(f)
        with open('input.json', encoding='utf-8') as f:
            data = json.load(f)
        with app.app_context():
            html_content = render_template('index.html', t=_translations, title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'), trips=cached['trips'], updated_at=cached['updated_at'])
        with open('public/index.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f"HTML re-rendered from cache in {time.time() - start_time:.1f}s")
        raise SystemExit(0)

    rodelwelten.load_cache(CACHE_FILE)

    with open('input.json', encoding='utf-8') as f:
        data = json.load(f)

    driver = None
    try:
        driver = _make_driver()
        print("Using Selenium (headless Chrome) for JS-rendered pages")
    except Exception as e:
        print(f"Selenium unavailable ({e}), falling back to requests")

    try:
        trip_data = build_trip_data(data, driver=driver, force_refresh=args.force, broker_filter=args.broker, limit=args.limit)
    finally:
        if driver:
            driver.quit()

    rodelwelten.save_cache()

    updated_at = datetime.now().strftime('%Y-%m-%d %H:%M')

    with open('public/data.json', 'w', encoding='utf-8') as f:
        json.dump({'updated_at': updated_at, 'trips': trip_data}, f, ensure_ascii=False, indent=2)
    print("Data saved to public/data.json")

    with app.app_context():
        html_content = render_template('index.html', t=_translations, title=data.get('title', 'Ferienhaus-Vergleich für Rodeln'), trips=trip_data, updated_at=updated_at)

    with open('public/index.html', 'w', encoding='utf-8') as f:
        f.write(html_content)

    print("Static site generated in public/index.html")
    print(f"Done in {time.time() - start_time:.1f}s")
