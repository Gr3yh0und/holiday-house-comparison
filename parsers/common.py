"""Shared utilities for all house parsers."""
import json
import random
import re


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
    'bus_stop': 'N/A',
    'supermarket': 'N/A',
    'rating': 'N/A',
    'persons': 'N/A',
    'sauna': 'N/A',
}

_USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
]


def random_user_agent():
    """Return a random browser User-Agent string."""
    return random.choice(_USER_AGENTS)


def random_headers():
    """Return request headers with a randomly chosen User-Agent."""
    return {
        'Accept-Language': 'de-DE,de;q=0.9',
        'User-Agent': random_user_agent(),
    }


# Default static headers (kept for backwards compatibility)
HEADERS = {
    'Accept-Language': 'de-DE,de;q=0.9',
    'User-Agent': _USER_AGENTS[0],
}

_COUNTRY_NAMES = {
    # ISO codes
    'AT': 'Österreich', 'DE': 'Deutschland', 'CH': 'Schweiz',
    'IT': 'Italien', 'FR': 'Frankreich',
    # English names
    'Austria': 'Österreich', 'Germany': 'Deutschland', 'Switzerland': 'Schweiz',
    'Italy': 'Italien', 'France': 'Frankreich',
}


def normalize_country(s):
    return _COUNTRY_NAMES.get(s.strip(), s.strip())


def parse_json_ld(soup, schema_type):
    """Return the first JSON-LD object matching schema_type, or {}."""
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            d = json.loads(tag.string or '')
            if isinstance(d, dict) and d.get('@type') == schema_type:
                return d
        except (json.JSONDecodeError, TypeError):
            pass
    return {}


def clean_bed_desc(bed_desc):
    """Normalise a raw bed description string."""
    bed_desc = re.sub(r',\s*Länge\s*\d+\s*cm', '', bed_desc)
    bed_desc = re.sub(r',?\s*(?:(?:Bad|Dusche|WC)(?:\s*/\s*(?:Bad|Dusche|WC))+)', '', bed_desc)
    bed_desc = re.sub(r'\b1\s*[xX]\s*(\d+cm)', r'\1', bed_desc)
    bed_desc = re.sub(r'(\d+)\s*cm', r'\1cm', bed_desc)
    return bed_desc.strip().strip(',').strip()


def parse_room_config(desc):
    """Extract bedroom entries from a fluid property description (Interhome style).

    Handles patterns like:
      '1 Zimmer 20 m² mit 1 franz. Bett (160cm)'
      '1 Zimmer 28 m2 mit 1 Diwanbett (130cm), 1 franz. Bett (160cm)'
      '3 abgeschrägte Zimmer, jedes Zimmer mit 1 franz. Bett (160cm)'
    """
    abbrevs = r'\b(franz|ca|inkl|exkl|evtl|ggf|usw|etc|max|min|Nr|Str)\.'
    text = re.sub(abbrevs, r'\1', desc, flags=re.I)

    rooms = []
    for seg in re.split(r'\.\s+', text):
        m = re.search(
            r'(\d+)\s+(?:\S+\s+)?Zimmer'
            r'(?:\s+\d+\s*m[²2])?'
            r'(?:\s*,\s*jedes\s+Zimmer)?'
            r'\s+(?:je\s+)?mit\s+'
            r'(.+)',
            seg.strip(), re.I
        )
        if m:
            count = int(m.group(1))
            bed_desc = clean_bed_desc(m.group(2).rstrip('.,'))
            rooms.extend([bed_desc] * count)
    return rooms
