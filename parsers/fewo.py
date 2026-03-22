import re
import time

import requests
from bs4 import BeautifulSoup

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
}


def scrape(url, driver=None):
    result = dict(EMPTY, room_config=[])

    try:
        if driver:
            driver.get(url)
            time.sleep(8)
            page_source = driver.page_source
            print(f"  [fewo] page source length: {len(page_source)} chars")
            soup = BeautifulSoup(page_source, 'html.parser')
        else:
            response = requests.get(url, headers=_headers(), timeout=15)
            print(f"  [fewo] response status: {response.status_code}, length: {len(response.content)}")
            soup = BeautifulSoup(response.content, 'html.parser')

        text = soup.get_text()
        print(f"  [fewo] page title: {soup.title.string if soup.title else 'N/A'}")

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

        # Address: content-hotel-address renders "Town, Region"
        addr_el = soup.find(attrs={'data-stid': 'content-hotel-address'})
        result['address'] = addr_el.get_text(strip=True) if addr_el else 'N/A'
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

        # Room/bed config: each bedroom is a data-stid="content-item" with h4 + bed text
        for item in soup.find_all('div', attrs={'data-stid': 'content-item'}):
            h4 = item.find('h4', class_='uitk-heading-6')
            if not h4 or 'Schlafzimmer' not in h4.get_text():
                continue
            bed_el = item.find('div', class_=lambda c: c and 'uitk-type-300' in c and 'uitk-type-regular' in c)
            if bed_el:
                result['room_config'].append(f'{h4.get_text(strip=True)}: {bed_el.get_text(strip=True)}')

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
                re.search(r'Ausgezeichnet\s+(\d+[.,]\d+)', src, re.I) or
                re.search(r'Sehr gut\s+(\d+[.,]\d+)', src, re.I) or
                re.search(r'(\d+[.,]\d+)', src)
            )
        else:
            score_m = (
                re.search(r'(\d+[.,]\d+)\s*/\s*10', text) or
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

    except Exception as e:
        print(f"  [fewo] error scraping {url}: {e}")
        return {k: 'Error' for k in result}

    return result


def _headers():
    return {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
    }
