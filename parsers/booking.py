import json
import re

import requests
from bs4 import BeautifulSoup

from parsers.common import EMPTY, normalize_country as _normalize_country


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
                result['rating'] = (
                    f"{agg.get('ratingValue', '?')} / {agg.get('bestRating', 10)}"
                    f" ({agg.get('reviewCount', '?')} Bewertungen)"
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

        price_el = soup.find(attrs={'data-testid': 'price-and-discounts-price'})
        if price_el:
            result['price'] = price_el.text.strip()
        else:
            # Prefer the discounted total from bui-price-display__value; the first
            # regex match would otherwise land on the strikethrough original price.
            val_el = soup.find('div', class_='bui-price-display__value')
            if val_el:
                span = val_el.find('span', class_='prco-valign-middle-helper')
                result['price'] = (span or val_el).get_text(strip=True)
            else:
                price_m = re.search(r'(?:EUR|€)\s*[\s\u00a0]*([\d.,]+)', text)
                result['price'] = f'€ {price_m.group(1)}' if price_m else 'N/A'

        # get_text() may concatenate "Bahn" + station name without space,
        # then newline before distance: "BahnLengdorf\n650 m"
        train_m = re.search(r'Bahn\s*([A-Za-zÄÖÜäöüß][^\n\d]*?)\s*\n\s*([\d,.]+\s*m)\b', text)
        if train_m:
            result['train_station'] = f'{train_m.group(1).strip()} {train_m.group(2)}'

        avail_m = re.search(r'"b_has_available_rooms"\s*:\s*(true|false)', text)
        if avail_m:
            result['time'] = 'Available' if avail_m.group(1) == 'true' else 'Unavailable'

        # m²: facility badge with data-name-en="room size"
        size_el = soup.find('div', attrs={'data-name-en': 'room size'})
        if size_el:
            sqm_m = re.search(r'(\d+)\s*m²', size_el.get_text())
            result['sqm'] = f'{sqm_m.group(1)} m²' if sqm_m else 'N/A'

        # Bathrooms: <li class="bathrooms-nr"><span>3</span>
        bath_li = soup.find('li', class_='bathrooms-nr')
        if bath_li:
            bath_span = bath_li.find('span')
            result['bathrooms'] = bath_span.text.strip() if bath_span else 'N/A'
        else:
            bath_m = re.search(r'(\d+)\s*Badezimmer', text, re.I)
            result['bathrooms'] = bath_m.group(1) if bath_m else 'N/A'

        persons_el = soup.find('span', class_='c-occupancy-icons__multiplier-number')
        if persons_el:
            result['persons'] = persons_el.text.strip()

        # Room config: first .m-rs-bed-display container → one entry per bedroom block
        bed_display = soup.find('div', class_='m-rs-bed-display')
        if bed_display:
            for block in bed_display.find_all('div', class_='m-rs-bed-display__block'):
                label = block.find('div', class_='m-rs-bed-display__label')
                beds = block.find_all('span', class_='m-rs-bed-display__bed-type-name')
                if label and beds:
                    bed_types = ', '.join(b.get_text(strip=True) for b in beds)
                    result['room_config'].append(bed_types)

        result['time'] = 'Available'
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


def _headers():
    from parsers.common import HEADERS
    return HEADERS
