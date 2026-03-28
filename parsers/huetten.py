import json
import re
from datetime import datetime
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

_COUNTRY_NAMES = {
    'AT': 'Österreich', 'DE': 'Deutschland', 'CH': 'Schweiz',
    'IT': 'Italien', 'FR': 'Frankreich',
    'Austria': 'Österreich', 'Germany': 'Deutschland', 'Switzerland': 'Schweiz',
    'Italy': 'Italien', 'France': 'Frankreich',
}

def _normalize_country(s):
    s = s.strip()
    return _COUNTRY_NAMES.get(s, s)


EMPTY = {
    'location': 'N/A',
    'address': 'N/A',
    'rooms': 'N/A',
    'sqm': 'N/A',
    'bathrooms': 'N/A',
    'room_config': [],
    'price': 'N/A',
    'time': 'N/A',
    'train_station': 'N/A',
    'supermarket': 'N/A',
    'rating': 'N/A',
    'persons': 'N/A',
    'sauna': 'N/A',
}

_HEADERS = {
    'Accept-Language': 'de',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}


def scrape(url, _driver=None):
    result = dict(EMPTY, room_config=[])

    checkin_date, persons, nights = _parse_url_params(url)
    base_url = url.split('#')[0]

    try:
        resp = requests.get(base_url, timeout=15, headers=_HEADERS)
        soup = BeautifulSoup(resp.content, 'html.parser')
        text = soup.get_text(' ', strip=True)

        ld = _parse_json_ld(soup)

        # Name / location
        h1 = soup.find('h1')
        result['location'] = h1.get_text(strip=True) if h1 else ld.get('name', 'N/A')

        # Address
        addr = ld.get('address', {})
        city = addr.get('addressLocality', '')
        country = _normalize_country(addr.get('addressCountry', ''))
        result['address'] = f"{city}, {country}" if city else 'N/A'

        # Persons
        capacity = ld.get('maximumAttendeeCapacity')
        result['persons'] = str(capacity) if capacity else 'N/A'

        # Rating
        agg = ld.get('aggregateRating', {})
        if agg.get('ratingValue'):
            score = round(float(agg['ratingValue']))
            count = agg.get('ratingCount', '')
            result['rating'] = f"{score}% ({count} Bewertungen)" if count else f"{score}%"

        # sqm
        sqm_m = re.search(r'(\d+)\s*qm', text, re.I)
        result['sqm'] = f"{sqm_m.group(1)} m²" if sqm_m else 'N/A'

        # Bathrooms: count "X Badezimmer" occurrences and sum them
        bath_count = sum(int(m.group(1)) for m in re.finditer(r'(\d+)\s*Badezimmer', text))
        result['bathrooms'] = str(bath_count) if bath_count else 'N/A'

        # Room config: expand "Nx DZ mit <description>" to N individual entries
        room_config = []
        for li in soup.select('ul.cst-list li'):
            t = li.get_text(strip=True)
            m = re.match(r'(\d+)x\s*DZ\s+mit\s+(.*)', t, re.I)
            if m:
                count = int(m.group(1))
                desc = re.sub(r'\s*\(\d+\s*Personen?\)\s*$', '', m.group(2), flags=re.I).strip()
                room_config.extend([desc] * count)
        result['room_config'] = room_config
        result['rooms'] = str(len(room_config)) if room_config else 'N/A'

        # Sauna: check equipment section only to avoid false positives from nav links
        equip_div = soup.find('div', class_='hrt-indicator-group-equipment')
        equip_text = equip_div.get_text(' ', strip=True) if equip_div else ''
        amenities = ld.get('amenityFeature', [])
        result['sauna'] = 'Ja' if (any('Sauna' in a for a in amenities)
                                   or re.search(r'\bSauna\b', equip_text)) else 'Nein'

        # Price: match checkin date against the weekly price table
        price_sec = soup.find(id=re.compile(r'price', re.I))
        if price_sec and checkin_date:
            result['price'] = _price_for_date(price_sec, checkin_date, persons or 8)
            result['price_10'] = _price_for_date(price_sec, checkin_date, 10)

        # Extra costs (Nebenkosten), excluding Kaution
        nk_div = soup.find('div', class_='Nebenkosten')
        if nk_div and checkin_date:
            result['extra_costs'] = _extra_costs_for_persons(nk_div, checkin_date, nights or 7, persons or 8)
            result['extra_costs_10'] = _extra_costs_for_persons(nk_div, checkin_date, nights or 7, 10)
            result['total_costs'] = _sum_prices(result['price'], result['extra_costs'])
            result['total_costs_10'] = _sum_prices(result['price_10'], result['extra_costs_10'])

        result['time'] = 'Available'
        print(f"  [huetten] {result['location']} | {result['address']} | "
              f"{result['persons']} Pers. | {result['rooms']} Zimmer | "
              f"{result['sqm']} | {result['price']} | {result['rating']}")

    except Exception as e:
        print(f"  [huetten] error scraping {url}: {e}")
        import traceback
        traceback.print_exc()
        return {k: 'Error' for k in result}

    return result


def _parse_url_params(url):
    """Extract checkin date, person count, and nights from the URL fragment."""
    fragment = urlparse(url).fragment
    if not fragment:
        return None, None, None
    qs = fragment.split('?', 1)[-1] if '?' in fragment else fragment
    params = parse_qs(qs)
    date_str = params.get('calendar_date_from', [None])[0]
    persons_str = params.get('persons_adults', [None])[0]
    nights_str = params.get('calendar_stays', [None])[0]
    checkin = None
    if date_str:
        try:
            checkin = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            pass
    persons = int(persons_str) if persons_str and persons_str.isdigit() else None
    nights = int(nights_str) if nights_str and nights_str.isdigit() else None
    return checkin, persons, nights


def _price_for_date(price_section, checkin_date, persons):
    """Return the weekly price for checkin_date and persons from the price table."""
    rows = price_section.find_all('tr')
    if not rows:
        return 'N/A'

    # Determine person-count thresholds from the header row
    header = rows[0].get_text(' ', strip=True)
    thresholds = [int(m.group(1)) for m in re.finditer(r'bis\s+(\d+)\s*Pers', header)]

    for row in rows[1:]:
        cells = [td.get_text(strip=True) for td in row.find_all('td')]
        if len(cells) < 2:
            continue
        date_m = re.search(r'(\d{2}\.\d{2}\.\d{4})\s*bis\s*(\d{2}\.\d{2}\.\d{4})', cells[0])
        if not date_m:
            continue
        from_d = datetime.strptime(date_m.group(1), '%d.%m.%Y').date()
        to_d = datetime.strptime(date_m.group(2), '%d.%m.%Y').date()
        if not from_d <= checkin_date < to_d:
            continue

        # Pick the column matching the person count
        col = next((i + 1 for i, t in enumerate(thresholds) if persons <= t), len(thresholds))
        if col >= len(cells):
            col = len(cells) - 1
        price_text = cells[col]
        if price_text == 'X':
            return 'N/A'
        # Normalise "€ 2.090,--" → "2.090 €"
        price_m = re.search(r'[\d.]+', price_text.replace(',--', ''))
        return f"{price_m.group(0)} €" if price_m else 'N/A'

    return 'N/A'


def _sum_prices(*prices):
    """Sum German-formatted price strings like '2.090 €', '709 €'. Returns 'N/A' if any is missing."""
    total = 0.0
    for p in prices:
        if not p or p == 'N/A':
            return 'N/A'
        s = p.strip().replace('€', '').strip()
        s = s.replace('.', '').replace(',', '.')
        try:
            total += float(s)
        except ValueError:
            return 'N/A'
    return f"{round(total)} €"


def _extra_costs_for_persons(nk_div, checkin_date, nights, persons):
    """Return total extra costs (excluding Kaution) as a formatted string."""
    total = 0.0
    found_any = False

    for li in nk_div.find_all('li'):
        text = re.sub(r'\s+', ' ', li.get_text(' ', strip=True))

        if re.search(r'\bKaution\b', text, re.I):
            continue
        if re.search(r'\bHaustier\b|\bHund\b|\bKatz', text, re.I):
            continue

        # Skip items gated by a future "ab DD.MM.YYYY" date that hasn't arrived yet
        date_m = re.search(r'\bab\s+(\d{2}\.\d{2}\.\d{4})', text)
        if date_m:
            cutoff = datetime.strptime(date_m.group(1), '%d.%m.%Y').date()
            if checkin_date < cutoff:
                continue

        amount_m = re.search(r'[€\u20ac]\s*([\d.]+(?:,\d+)?)', text)
        if not amount_m:
            continue
        amount = float(amount_m.group(1).replace('.', '').replace(',', '.'))

        if re.search(r'pro\s+Person\b.{0,20}\bAufenthalt|pro\s+Person/Aufenthalt', text, re.I):
            total += amount * persons
        elif re.search(r'pro\s+Person\b.{0,10}\b(Tag|Nacht)|pro\s+Person/(Tag|Nacht)', text, re.I):
            total += amount * persons * nights
        elif re.search(r'pro\s+Aufenthalt', text, re.I):
            total += amount
        else:
            continue

        found_any = True

    return f"{round(total)} €" if found_any else 'N/A'


def _parse_json_ld(soup):
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            d = json.loads(tag.string or '')
            if isinstance(d, dict) and d.get('@type') == 'LodgingBusiness':
                return d
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


if __name__ == '__main__':
    import sys
    url = sys.argv[1] if len(sys.argv) > 1 else 'https://www.huetten.com/de/huette/untersoellhof-rt45507.html#/vsc.php?calendar_date_from=2027-02-13&persons_adults=8&calendar_stays=7&c[id_hotel]=7691&set_language=de'  # pylint: disable=line-too-long
    result = scrape(url)
    rc = result.pop('room_config', [])
    for k, v in result.items():
        print(f"  {k:<20} {v}")
    for i, r in enumerate(rc, 1):
        print(f"  Schlafzimmer {i:<10} {r}")
