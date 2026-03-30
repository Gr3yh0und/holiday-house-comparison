import random
import re
import time

import requests
from bs4 import BeautifulSoup

from parsers.common import (
    EMPTY, random_headers, random_user_agent,
    parse_room_config as _parse_room_config, clean_bed_desc,
)

_FEWO_HOME = 'https://www.fewo-direkt.de'

# CSS selectors tried in order when looking for a cookie-consent accept button
_COOKIE_SELECTORS = [
    '[data-testid="accept-button"]',
    '#onetrust-accept-btn-handler',
    'button[id*="accept"]',
    '[class*="accept-all"]',
    '[class*="acceptAll"]',
]

# Whether the current driver session has already warmed up on fewo-direkt.de
# Keyed by driver id() so we don't repeat the warmup for every house
_warmed_drivers: set = set()


def _accept_cookies(driver):
    """Click the cookie-consent accept button if one is visible."""
    for sel in _COOKIE_SELECTORS:
        try:
            btn = driver.find_element('css selector', sel)
            if btn.is_displayed():
                btn.click()
                time.sleep(random.uniform(0.8, 1.8))
                print('  [fewo] accepted cookie consent')
                return True
        except Exception:
            pass
    return False


def _human_scroll(driver, steps=None):
    """Scroll down the page in a realistic, non-linear fashion."""
    if steps is None:
        steps = random.randint(3, 6)
    try:
        scroll_height = driver.execute_script('return document.body.scrollHeight') or 2000
        for _ in range(steps):
            target = random.randint(200, min(1400, scroll_height))
            driver.execute_script(f'window.scrollTo({{top: {target}, behavior: "smooth"}});')
            time.sleep(random.uniform(0.6, 1.4))
        # Scroll back to the top before we start reading the page
        driver.execute_script('window.scrollTo({top: 0, behavior: "smooth"});')
        time.sleep(random.uniform(0.4, 0.9))
    except Exception:
        pass


def _warm_up_session(driver):
    """Navigate to the fewo-direkt homepage and act human before hitting a listing."""
    did = id(driver)
    if did in _warmed_drivers:
        return
    print('  [fewo] warming up session on homepage …')
    driver.get(_FEWO_HOME)
    time.sleep(random.uniform(4, 8))
    _accept_cookies(driver)
    _human_scroll(driver)
    time.sleep(random.uniform(2, 5))
    _warmed_drivers.add(did)
    print('  [fewo] session warmed up')

_REGION_COUNTRY = {
    # Austria
    'Tirol': 'Österreich', 'Salzburg': 'Österreich', 'Vorarlberg': 'Österreich',
    'Kärnten': 'Österreich', 'Steiermark': 'Österreich', 'Wien': 'Österreich',
    'Burgenland': 'Österreich', 'Niederösterreich': 'Österreich', 'Oberösterreich': 'Österreich',
    # Germany
    'Bayern': 'Deutschland', 'Baden-Württemberg': 'Deutschland', 'Sachsen': 'Deutschland',
    'Thüringen': 'Deutschland', 'Hessen': 'Deutschland', 'Niedersachsen': 'Deutschland',
    'Rheinland-Pfalz': 'Deutschland', 'Nordrhein-Westfalen': 'Deutschland',
    'Schleswig-Holstein': 'Deutschland', 'Mecklenburg-Vorpommern': 'Deutschland',
    'Brandenburg': 'Deutschland', 'Sachsen-Anhalt': 'Deutschland', 'Saarland': 'Deutschland',
    # Switzerland (canton codes and names)
    'BE': 'Schweiz', 'GR': 'Schweiz', 'VS': 'Schweiz', 'UR': 'Schweiz', 'SZ': 'Schweiz',
    'OW': 'Schweiz', 'NW': 'Schweiz', 'GL': 'Schweiz', 'ZG': 'Schweiz', 'FR': 'Schweiz',
    'SO': 'Schweiz', 'BS': 'Schweiz', 'BL': 'Schweiz', 'SH': 'Schweiz', 'SG': 'Schweiz',
    'AG': 'Schweiz', 'TG': 'Schweiz', 'TI': 'Schweiz', 'VD': 'Schweiz', 'NE': 'Schweiz',
    'GE': 'Schweiz', 'JU': 'Schweiz', 'ZH': 'Schweiz', 'LU': 'Schweiz', 'AR': 'Schweiz', 'AI': 'Schweiz',
    'Bern': 'Schweiz', 'Graubünden': 'Schweiz', 'Wallis': 'Schweiz', 'Tessin': 'Schweiz',
    # Italy / France
    'Südtirol': 'Italien', 'Trentino': 'Italien',
    'Haute-Savoie': 'Frankreich', 'Savoie': 'Frankreich',
}



def scrape(url, driver=None):
    result = dict(EMPTY, room_config=[])

    try:
        if driver:
            ua = random_user_agent()
            driver.execute_cdp_cmd('Network.setUserAgentOverride', {'userAgent': ua})
            print(f"  [fewo] user-agent: {ua[:60]}...")
            _warm_up_session(driver)
            driver.get(url)
            # Let the React SPA hydrate; add some human-like interaction
            time.sleep(random.uniform(5, 9))
            _human_scroll(driver)
            time.sleep(random.uniform(2, 4))
            page_source = driver.page_source
            print(f"  [fewo] page source length: {len(page_source)} chars")
            soup = BeautifulSoup(page_source, 'html.parser')
        else:
            # curl_cffi mimics the real Chrome TLS fingerprint (JA3/JA3S) so
            # DataDome cannot distinguish it from a real browser at the TLS layer.
            try:
                from curl_cffi import requests as cffi_requests
                ua = random_user_agent()
                resp = cffi_requests.get(
                    url,
                    impersonate='chrome124',
                    headers={
                        'User-Agent': ua,
                        'Accept': (
                            'text/html,application/xhtml+xml,application/xml;'
                            'q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8'
                        ),
                        'Accept-Language': 'de-DE,de;q=0.9',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Site': 'none',
                        'Sec-Fetch-User': '?1',
                        'Upgrade-Insecure-Requests': '1',
                    },
                    timeout=20,
                )
                print(f"  [fewo] curl_cffi status: {resp.status_code}, length: {len(resp.content)}")
                soup = BeautifulSoup(resp.content, 'html.parser')
                page_source = resp.text
            except ImportError:
                print('  [fewo] curl_cffi not installed, falling back to requests')
                response = requests.get(url, headers=random_headers(), timeout=15)
                print(f"  [fewo] response status: {response.status_code}, length: {len(response.content)}")
                soup = BeautifulSoup(response.content, 'html.parser')
                page_source = response.text

        page_title = soup.title.string if soup.title else ''
        print(f"  [fewo] page title: {page_title or 'N/A'}")
        # Detect bot/rate-limit pages — fewo-direkt uses DataDome which shows a
        # German challenge page ("Warum diese Kontrolle?") when the IP or
        # browser fingerprint is flagged.
        body_text = soup.get_text()
        bot_page = any(kw in page_title.lower() for kw in (
            'bot', 'mensch', 'too many requests', 'access denied', 'kontrolle',
        )) or any(kw in body_text for kw in (
            'Warum diese Kontrolle',
            'übermenschlicher Geschwindigkeit',
            'DataDome',
        )) or (len(page_source) < 5000)
        if bot_page:
            print("  [fewo] bot/rate-limit page detected — scrape failed")
            return None

        text = soup.get_text()

        # Name: h1 inside content-hotel-title (sibling span holds the property type)
        title_el = soup.find(attrs={'data-stid': 'content-hotel-title'})
        print(f"  [fewo] content-hotel-title found: {title_el is not None}")
        if title_el:
            h1 = title_el.find('h1')
            raw = h1.get_text(strip=True) if h1 else title_el.get_text(strip=True)
            result['location'] = ' '.join(raw.split())
        else:
            h1 = soup.find('h1')
            result['location'] = ' '.join(h1.get_text(strip=True).split()) if h1 else 'N/A'
        print(f"  [fewo] location: {result['location'][:60]}")

        # Address: content-hotel-address renders "Town, Region" — convert to "Town, Country"
        addr_el = soup.find(attrs={'data-stid': 'content-hotel-address'})
        addr_text = addr_el.get_text(strip=True) if addr_el else ''
        if ',' in addr_text:
            city_raw, region = addr_text.rsplit(',', 1)
            region = region.strip()
            # Swiss format: "Wengen BE, BE" — strip canton code suffix from city
            city = re.sub(r'\s+[A-Z]{2}$', '', city_raw.strip())
            region_key = region.removeprefix('Canton of ').strip()
            country = _REGION_COUNTRY.get(region_key, _REGION_COUNTRY.get(region, region))
            result['address'] = f"{city}, {country}"
        else:
            result['address'] = addr_text or 'N/A'
        print(f"  [fewo] address: {result['address']}")

        # Rooms, bathrooms, persons, sqm — all in rendered summary text
        rooms_m = re.search(r'(\d+)\s*Schlafzimmer', text, re.I)
        result['rooms'] = rooms_m.group(1) if rooms_m else 'N/A'
        print(f"  [fewo] rooms: {result['rooms']}")

        bath_m = re.search(r'(\d+)\s*Badezimmer', text, re.I)
        result['bathrooms'] = bath_m.group(1) if bath_m else 'N/A'
        print(f"  [fewo] bathrooms: {result['bathrooms']}")

        persons_m = re.search(r'(?:Platz für|für)\s*(\d+)\s*(?:Gäste|Personen)', text, re.I)
        result['persons'] = persons_m.group(1) if persons_m else 'N/A'
        print(f"  [fewo] persons: {result['persons']}")

        sqm_m = re.search(r'(\d+)\s*m²', text)
        result['sqm'] = f'{sqm_m.group(1)} m²' if sqm_m else 'N/A'
        print(f"  [fewo] sqm: {result['sqm']}")

        # Room/bed config: content-items whose text contains bed keywords
        # Room names are custom (e.g. "Front 1", "Kaminzimmer") — normalise to "Schlafzimmer N"
        bedroom_n = 0
        for item in soup.find_all('div', attrs={'data-stid': 'content-item'}):
            h4 = item.find('h4')
            if not h4:
                continue
            item_text = item.get_text(' ', strip=True)
            bed_re = r'\d?\s*(?:(?:King|Queen|Doppel|Einzel|Etagen|Stock|Schlaf|Franz|Kinder)[- ]?[Bb]ett|Schlafsofa)'
            if re.search(bed_re, item_text, re.I):
                bedroom_n += 1
                bed_text = clean_bed_desc(item_text[len(h4.get_text(strip=True)):].strip())
                result['room_config'].append(bed_text)

        # Fallback: fluid text in data-stid="content-markup" (e.g. Interhome-style descriptions)
        if not result['room_config']:
            for markup in soup.find_all(attrs={'data-stid': 'content-markup'}):
                markup_text = re.sub(r'\s+', ' ', markup.get_text(' ', strip=True))
                parsed = _parse_room_config(markup_text)
                if parsed:
                    result['room_config'] = parsed
                    if result['rooms'] == 'N/A':
                        result['rooms'] = str(len(parsed))
                    break

        # Price: price-summary data-stid; prefer nightly rate
        price_el = soup.find(attrs={'data-stid': 'price-summary'})
        print(f"  [fewo] price-summary found: {price_el is not None}")
        if price_el:
            price_text = price_el.get_text(' ', strip=True)
            print(f"  [fewo] price-summary text: {price_text[:80]}")
            total_m = re.search(r'beträgt\s+([\d.,]+\s*[\xa0\u202f]?€)', price_text)
            if not total_m:
                total_m = re.search(r'([\d.,]+\s*[\xa0\u202f]?€)\s*für\s*1\s*\w', price_text)
            result['price'] = total_m.group(1).strip() if total_m else 'N/A'
        else:
            total_m = re.search(r'beträgt\s+([\d.,]+\s*[\xa0\u202f]?€)', text)
            result['price'] = total_m.group(1).strip() if total_m else 'N/A'
        print(f"  [fewo] price: {result['price']}")

        # Rating: VRBO/Expedia platform shows score in reviews section
        rating_el = (
            soup.find(attrs={'data-stid': 'content-hotel-reviews'}) or
            soup.find(attrs={'data-stid': 'reviews-summary'}) or
            soup.find(attrs={'data-stid': 'reviews-header'})
        )
        if rating_el:
            src = rating_el.get_text(' ', strip=True)
            print(f"  [fewo] rating element text: {src[:80]}")
            score_m = (
                re.search(r'(\d+[.,]\d+)\s*/\s*10', src) or
                re.search(r'(\d+[.,]\d+)\s+von\s+10', src, re.I) or
                re.search(r'Ausgezeichnet\s+(\d+[.,]\d+)', src, re.I) or
                re.search(r'Sehr gut\s+(\d+[.,]\d+)', src, re.I) or
                re.search(r'(\d+[.,]\d+)', src)
            )
        else:
            score_m = (
                re.search(r'(\d+[.,]\d+)\s*/\s*10', text) or
                re.search(r'(\d+[.,]\d+)\s+von\s+10', text, re.I) or
                re.search(r'Ausgezeichnet\s+(\d+[.,]\d+)', text, re.I) or
                re.search(r'Sehr gut\s+(\d+[.,]\d+)', text, re.I)
            )
            src = text
        count_m = re.search(r'(\d+)\s*Bewertung', src, re.I)
        if score_m:
            result['rating'] = score_m.group(1)
            if count_m:
                result['rating'] += f' ({count_m.group(1)} Bewertungen)'
        print(f"  [fewo] rating: {result['rating']}")

        result['time'] = 'Available'
        if re.search(r'Bahnhof|train station', text, re.I):
            result['train_station'] = 'Nearby'
        if re.search(r'Supermarkt|supermarket', text, re.I):
            result['supermarket'] = 'Nearby'
        result['sauna'] = 'Ja' if re.search(r'\bSauna\b', text, re.I) else 'Nein'

    except Exception as e:
        print(f"  [fewo] error scraping {url}: {e}")
        return {k: 'Error' for k in result}

    return result
