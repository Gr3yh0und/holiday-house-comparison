import json
import re
from urllib.parse import urlparse, parse_qs, urlencode

import requests
from bs4 import BeautifulSoup

_COUNTRY_NAMES = {
    'Schweiz': 'Schweiz', 'Switzerland': 'Schweiz',
    'Österreich': 'Österreich', 'Austria': 'Österreich',
    'Deutschland': 'Deutschland', 'Germany': 'Deutschland',
    'Italien': 'Italien', 'Italy': 'Italien',
    'Frankreich': 'Frankreich', 'France': 'Frankreich',
}

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
    'Accept-Language': 'de-DE,de;q=0.9',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    ),
}


def _clean_url(url):
    """Strip tracking/session params that can cause the pricing API to hang when stale."""
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    for key in ('offerId', 'clickId'):
        params.pop(key, None)
    clean_query = urlencode({k: v[0] for k, v in params.items()})
    return parsed._replace(query=clean_query).geturl()


def scrape(url, driver=None):
    result = dict(EMPTY, room_config=[])
    persons, _, _ = _parse_url_params(url)

    selenium_price_text = ''
    try:
        if driver:
            driver.get(_clean_url(url))
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC
            from selenium.webdriver.common.by import By
            # Wait until availability badge settles to a final state (not the loading placeholder)
            from selenium.common.exceptions import NoSuchElementException, TimeoutException
            def _badge_settled(d):
                try:
                    txt = d.find_element(By.CSS_SELECTOR, '[data-test="available-badge"]').text
                    return any(kw in txt for kw in ('verfügbar', 'ausgebucht'))
                except NoSuchElementException:
                    return False
            # If the pricing API is slow, proceed without price rather than crashing
            try:
                WebDriverWait(driver, 45).until(_badge_settled)
            except TimeoutException:
                print(f"  [interhome] pricing API timed out for {url}")
            badge_els = driver.find_elements(By.CSS_SELECTOR, '[data-test="available-badge"]')
            badge_text = badge_els[0].text if badge_els else ''
            # If available, wait for price and grab it directly from Selenium
            selenium_price_text = ''
            if 'verfügbar' in badge_text:
                try:
                    WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, '[data-test="total-price"]'))
                    )
                    price_el_live = driver.find_element(By.CSS_SELECTOR, '[data-test="total-price"]')
                    selenium_price_text = price_el_live.text
                except TimeoutException:
                    pass
            soup = BeautifulSoup(driver.page_source, 'html.parser')
        else:
            resp = requests.get(url, timeout=15, headers=_HEADERS)
            soup = BeautifulSoup(resp.content, 'html.parser')

        ld = _parse_json_ld(soup)

        # Name
        result['location'] = ld.get('name', 'N/A')

        # Address from location breadcrumb (country, ..., city, property-code)
        crumbs = soup.select('[data-test="location-breadcrumb"] li')
        if len(crumbs) >= 2:
            city = crumbs[-2].get_text(strip=True)
            country_raw = crumbs[0].get_text(strip=True)
            country = _COUNTRY_NAMES.get(country_raw, country_raw)
            result['address'] = f"{city}, {country}"

        # Persons
        result['persons'] = str(persons) if persons else 'N/A'

        # Rating (x / 5 scale)
        agg = ld.get('aggregateRating', {})
        if agg.get('ratingValue'):
            score = float(agg['ratingValue'])
            count = agg.get('reviewCount', '')
            result['rating'] = f"{score} / 5 ({count} Bewertungen)" if count else f"{score} / 5"

        # Full description from the rendered element — JSON-LD description is truncated
        desc_el = soup.find(attrs={'data-test': 'rental-description'})
        desc = re.sub(r'\s+', ' ', desc_el.get_text(' ', strip=True) if desc_el else ld.get('description', ''))

        # SQM — first 2–3 digit m² value (the total area appears before room-level sizes)
        sqm_m = re.search(r'(\d{2,3})\s*m²', desc)
        result['sqm'] = f"{sqm_m.group(1)} m²" if sqm_m else 'N/A'

        # Bathrooms — count Dusche/WC and Bad/WC in the full description
        bath_count = len(re.findall(r'(?:Dusche|Bad)\s*/\s*WC', desc))
        result['bathrooms'] = str(bath_count) if bath_count else 'N/A'

        # Room config
        room_config = _parse_room_config(desc)
        result['room_config'] = room_config
        result['rooms'] = str(len(room_config)) if room_config else 'N/A'

        # Sauna — check amenities section and description
        amenities_el = soup.find(attrs={'data-test': 'amenities'})
        amenities_text = amenities_el.get_text(' ', strip=True) if amenities_el else ''
        result['sauna'] = 'Ja' if re.search(r'\bSauna\b', amenities_text + ' ' + desc, re.I) else 'Nein'

        # Price — interhome uses English number format: 6,501.00 (comma = thousands, dot = decimal)
        # Require at least one comma-separated thousands group to avoid matching "7" in "7 Nächte"
        # Price — prefer text grabbed directly from Selenium (avoids page_source timing issues)
        price_source = selenium_price_text if driver else ''
        if not price_source:
            price_el = soup.find(attrs={'data-test': 'total-price'})
            price_source = price_el.get_text(strip=True) if price_el else ''
        price_m = re.search(r'(\d{1,3}(?:,\d{3})+(?:\.\d+)?)', price_source)
        if price_m:
            result['price'] = f"{round(float(price_m.group(1).replace(',', '')))} €"

        if not driver:
            available_badge = soup.find(attrs={'data-test': 'available-badge'})
            badge_text = available_badge.get_text(strip=True) if available_badge else ''
        result['time'] = 'Available' if 'verfügbar' in badge_text else 'Unavailable'
        print(
            f"  [interhome] {result['location']} | {result['address']} | "
            f"{result['persons']} Pers. | {result['rooms']} Zimmer | "
            f"{result['sqm']} | {result['price']} | {result['rating']}"
        )

    except Exception as e:
        print(f"  [interhome] error scraping {url}: {e}")
        import traceback
        traceback.print_exc()
        return {k: 'Error' for k in result}

    return result


def _parse_url_params(url):
    """Extract persons, arrival, and duration from URL query parameters."""
    params = parse_qs(urlparse(url).query)
    adults_str = params.get('adults', [None])[0]
    persons = int(adults_str) if adults_str and adults_str.isdigit() else None
    arrival = params.get('arrival', [None])[0]
    duration_str = params.get('duration', [None])[0]
    duration = int(duration_str) if duration_str and duration_str.isdigit() else None
    return persons, arrival, duration


def _parse_room_config(desc):
    """Extract individual bedroom entries from the property description text.

    Splits on sentence boundaries while protecting German abbreviations (franz., ca., etc.)
    that end with a period but are not sentence ends.

    Handles patterns like:
      '1 Zimmer 20 m² mit 1 franz. Bett (160 cm, Länge 200 cm)'
      '1 Zimmer 28 m² mit 1 Diwanbett (130 cm), 1 franz. Bett (160 cm)'
      '3 abgeschrägte Zimmer, jedes Zimmer mit 1 franz. Bett (160 cm)'
    """
    # Replace known abbreviation periods with a placeholder so they survive sentence splitting
    abbrevs = r'\b(franz|ca|inkl|exkl|evtl|ggf|usw|etc|max|min|Nr|Str)\.'
    text = re.sub(abbrevs, r'\1', desc, flags=re.I)

    rooms = []
    for seg in re.split(r'\.\s+', text):
        m = re.search(
            r'(\d+)\s+(?:\S+\s+)?Zimmer'        # N [adj] Zimmer
            r'(?:\s+\d+\s*m[²2])?'                 # optional size
            r'(?:\s*,\s*jedes\s+Zimmer)?'         # optional ", jedes Zimmer"
            r'\s+(?:je\s+)?mit\s+'               # mit / je mit
            r'(.+)',                              # bed description (rest of segment)
            seg.strip(), re.I
        )
        if m:
            count = int(m.group(1))
            bed_desc = m.group(2).strip().rstrip('.,')
            bed_desc = re.sub(r',\s*Länge\s*\d+\s*cm', '', bed_desc)
            bed_desc = re.sub(r'(\d+)\s*cm', r'\1cm', bed_desc)
            rooms.extend([bed_desc] * count)
    return rooms


def _parse_json_ld(soup):
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            d = json.loads(tag.string or '')
            if isinstance(d, dict) and d.get('@type') == 'Product':
                return d
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


if __name__ == '__main__':
    import sys
    _DEFAULT_URL = (
        'https://www.interhome.de/schweiz/berner-oberland/lauterbrunnen/'
        'ferienhaus-chalet-am-schaerm-ch3822.102.1/'
        '?adults=10&arrival=2027-02-20&duration=7&offerId=13ba9c29f52d43aa5f54009a7c8753c8'
    )
    _url = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_URL
    _result = scrape(_url)
    _rc = _result.pop('room_config', [])
    for k, v in _result.items():
        print(f"  {k:<20} {v}")
    for i, r in enumerate(_rc, 1):
        print(f"  Schlafzimmer {i:<10} {r}")
