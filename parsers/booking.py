import json
import re

import requests
from bs4 import BeautifulSoup

from parsers.common import EMPTY, normalize_country as _normalize_country, normalize_rating


def scrape(url, driver=None):
    result = dict(EMPTY, room_config=[])

    try:
        if driver:
            driver.get(url)
            import time
            time.sleep(6)
            page_source = driver.page_source
            print(f"  [booking] page source length: {len(page_source)} chars")
            soup = BeautifulSoup(page_source, 'html.parser')
        else:
            response = requests.get(url, headers=_headers(), timeout=15)
            print(f"  [booking] status: {response.status_code}, length: {len(response.content)}")
            soup = BeautifulSoup(response.content, 'html.parser')
        print(f"  [booking] page title: {soup.title.string.strip() if soup.title and soup.title.string else 'N/A'}")
        text = soup.get_text()

        # JSON-LD structured data is present in static HTML (no JS needed)
        ld_data = {}
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                parsed = json.loads(script.string or '')
                if parsed.get('@type') == 'Hotel':
                    ld_data = parsed
                    break
            except Exception:
                pass

        print(f"  [booking] ld+json Hotel found: {bool(ld_data)}")
        if ld_data:
            result['location'] = ld_data.get('name', 'N/A')
            addr = ld_data.get('address', {})
            # booking.com JSON-LD has addressLocality incorrectly set to the street;
            # the town follows the postal code in streetAddress, e.g. "2a Lärchenweg, 5722 Niedernsill, Österreich"
            street = addr.get('streetAddress', '')
            town_m = re.search(r'\d{4,5}\s+([^,]+)', street)
            town = town_m.group(1).strip() if town_m else 'N/A'
            country = _normalize_country(addr.get('addressCountry', ''))
            result['address'] = f"{town}, {country}" if country else town
            agg = ld_data.get('aggregateRating', {})
            if agg:
                result['rating'] = normalize_rating(
                    agg.get('ratingValue'), agg.get('bestRating', 10), agg.get('reviewCount')
                )
            result['description'] = ld_data.get('description', 'N/A')
        else:
            loc = (
                soup.find(attrs={'data-testid': 'property-name'}) or
                soup.find('h2', class_=re.compile(r'pp-header', re.I)) or
                soup.find('h1')
            )
            result['location'] = loc.text.strip() if loc else 'N/A'

        # DOM fallback for rating
        if result['rating'] == 'N/A':
            score_el = soup.find(attrs={'data-review-score': True})
            if score_el:
                result['rating'] = score_el.get('data-review-score')

        rooms_m = re.search(r'(\d+)\s*(Schlafzimmer|Bedroom)', text, re.I)
        result['rooms'] = rooms_m.group(1) if rooms_m else 'N/A'

        # Booked out: the room table row says "An Ihren Reisedaten auf unserer
        # Seite nicht verfügbar". The page then lists "Ab € ..." offers for other
        # dates and other houses, so never take a price from the free text.
        # (The same phrase also sits in an i18n <script> on every page, so only
        # look at the visible room rows.)
        unavailable = any(
            'nicht verfügbar' in row.get_text(' ', strip=True)
            for row in (rt.find_parent('tr') for rt in soup.find_all(attrs={'data-testid': 'rt-name-link'}))
            if row
        ) and not soup.find('div', class_='bui-price-display__value')
        price_el = soup.find(attrs={'data-testid': 'price-and-discounts-price'})
        if unavailable:
            result['price'] = 'N/A'
        elif price_el:
            result['price'] = price_el.text.strip()
        elif _rate_rows(soup):
            # One row per rate and group size: take the cheapest rate for the
            # largest group (the rows also offer smaller groups for less).
            rows = _rate_rows(soup)
            top = max(p for p, _, _ in rows)
            result['price'] = min((v, t) for p, v, t in rows if p == top)[1]
        else:
            # Prefer the discounted total from bui-price-display__value; the first
            # regex match would otherwise land on the strikethrough original price.
            val_el = soup.find('div', class_='bui-price-display__value')
            if val_el:
                span = val_el.find('span', class_='prco-valign-middle-helper')
                result['price'] = (span or val_el).get_text(strip=True)
        # get_text() may concatenate "Bahn" + station name without space,
        # then newline before distance: "BahnLengdorf\n650 m"
        train_m = re.search(r'Bahn\s*([A-Za-zÄÖÜäöüß][^\n\d]*?)\s*\n\s*([\d,.]+\s*m)\b', text)
        if train_m:
            result['train_station'] = f'{train_m.group(1).strip()} {train_m.group(2)}'

        if unavailable:
            result['time'] = 'Unavailable'
        elif result['price'] != 'N/A':
            result['time'] = 'Available'

        # m²: facility badge with data-name-en="room size"
        size_el = (soup.find('div', attrs={'data-name-en': 'room size'})
                   or soup.find(attrs={'data-testid': 'property-highlights'}))
        if size_el:
            sqm_m = re.search(r'(\d+)\s*m²', size_el.get_text())
            result['sqm'] = f'{sqm_m.group(1)} m²' if sqm_m else 'N/A'

        # Free dates: the hprt room table, "Schlafzimmer 1: 1 Doppelbett ... Badezimmer: 2"
        bed_el = soup.find(class_='hprt-roomtype-bed')
        bed_text = re.sub(r'\s+', ' ', bed_el.get_text(' ', strip=True)) if bed_el else ''
        bed_re = r'Schlafzimmer\s*\d+\s*:\s*(.+?)(?=\s*(?:Schlafzimmer\s*\d+|Wohnzimmer|Badezimmer)\s*:|$)'
        for m in re.finditer(bed_re, bed_text):
            result['room_config'].append(m.group(1).strip())

        # Bathrooms: <li class="bathrooms-nr"><span>3</span>
        bath_li = soup.find('li', class_='bathrooms-nr')
        bath_m = re.search(r'Badezimmer\s*:\s*(\d+)', bed_text)
        if bath_m:
            result['bathrooms'] = bath_m.group(1)
        elif bath_li:
            bath_span = bath_li.find('span')
            result['bathrooms'] = bath_span.text.strip() if bath_span else 'N/A'
        else:
            bath_m = re.search(r'(\d+)\s*Badezimmer', text, re.I)
            result['bathrooms'] = bath_m.group(1) if bath_m else 'N/A'

        persons_el = soup.find('span', class_='c-occupancy-icons__multiplier-number')
        if persons_el:
            result['persons'] = persons_el.text.strip()
        else:
            # "max. Personenzahl: 8" per rate row (free dates), or the bare
            # "× 8" next to the room name (booked out) -- take the largest.
            caps = [int(n) for n in re.findall(r'max\. Personenzahl:\s*(\d+)', text)]
            for rt in soup.find_all(attrs={'data-testid': 'rt-name-link'}):
                row = rt.find_parent('tr')
                if row:
                    caps += [int(n) for n in re.findall(r'×\s*(\d+)', row.get_text(' ', strip=True))]
            if caps:
                result['persons'] = str(max(caps))

        # Room config: first .m-rs-bed-display container → one entry per bedroom block
        bed_display = soup.find('div', class_='m-rs-bed-display')
        if bed_display:
            for block in bed_display.find_all('div', class_='m-rs-bed-display__block'):
                label = block.find('div', class_='m-rs-bed-display__label')
                beds = block.find_all('span', class_='m-rs-bed-display__bed-type-name')
                if label and beds:
                    bed_types = ', '.join(b.get_text(strip=True) for b in beds)
                    result['room_config'].append(bed_types)
        # Use room_config length as the authoritative bedroom count when available —
        # the text regex may match a different unit on multi-unit hotel pages.
        if result['room_config']:
            result['rooms'] = str(len(result['room_config']))

        if re.search(r'Bahnhof|train station', text, re.I):
            result['train_station'] = 'Nearby'
        if re.search(r'Supermarkt|supermarket', text, re.I):
            result['supermarket'] = 'Nearby'
        result['sauna'] = 'Ja' if re.search(r'\bSauna\b', text, re.I) else 'Nein'

        print(
            f"  [booking] address: {result['address']}, rooms: {result['rooms']},"
            f" price: {result['price']}, persons: {result['persons']}"
        )

    except Exception as e:
        print(f"  [booking] error scraping {url}: {e}")
        return {k: 'Error' for k in result}

    return result


def _rate_rows(soup):
    """Return (persons, price value, price text) per priced row of the hprt room table."""
    rows = []
    for tr in soup.select('table.hprt-table tr.js-rt-block-row'):
        occ = tr.find(class_='hprt-occupancy-occupancy-info')
        price = tr.find(class_='bui-price-display__value')
        persons_m = re.search(r'Personenzahl:\s*(\d+)', occ.get_text(' ', strip=True)) if occ else None
        if not (persons_m and price):
            continue
        text = re.sub(r'\s+', ' ', price.get_text(' ', strip=True))
        value_m = re.search(r'[\d.]+(?:,\d+)?', text)
        if value_m:
            value = float(value_m.group(0).replace('.', '').replace(',', '.'))
            rows.append((int(persons_m.group(1)), value, text))
    return rows


def _headers():
    from parsers.common import HEADERS
    return HEADERS
