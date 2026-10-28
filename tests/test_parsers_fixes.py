"""Regression tests for the findings of the 2026-10-28 parsers code review.

Each test targets one finding (B1..B15, G4) with a small HTML/JSON string and
no network: they all fail on the pre-fix code at commit 7ce9e09.
"""
import json
from datetime import date, datetime
from unittest import mock

import pytest
import requests

from parsers import booking, common, huetten, interhome, loipen, outdooractive, rodelwelten


class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, html):
        self.content = html.encode('utf-8')
        self.text = html
        self.status_code = 200
        self.encoding = 'utf-8'

    def raise_for_status(self):
        pass

    def json(self):
        return json.loads(self.text)


def _patch_get(module, html):
    """Patch <module>.requests.get to always return `html`."""
    return mock.patch.object(module.requests, 'get',
                             side_effect=lambda url, **kw: _Resp(html))


# ---------------------------------------------------------------------------
# B1: a stray bui-price-display__value must not flip a booked-out page to
#     Available with the wrong price.
# ---------------------------------------------------------------------------
BOOKING_BOOKED_OUT_WITH_RECOMMENDATION = """
<html><head><title>t</title></head><body>
<table><tr>
  <td><a data-testid="rt-name-link">Apartment A</a></td>
  <td>An Ihren Reisedaten waren diese Unterkunft nicht verfügbar</td>
</tr></table>
<div class="bui-price-display__value">€ 999</div>
</body></html>
"""


def test_b1_stray_price_does_not_override_booked_out():
    with _patch_get(booking, BOOKING_BOOKED_OUT_WITH_RECOMMENDATION):
        result = booking.scrape('https://www.booking.com/hotel/de/x.html')
    assert result['time'] == 'Unavailable'
    assert result['price'] == 'N/A'


# ---------------------------------------------------------------------------
# B14: train station distance must be found even when the station name and the
#      distance sit in inline elements (no literal \n in get_text()).
# ---------------------------------------------------------------------------
BOOKING_TRAIN_INLINE = """
<html><head><title>t</title></head><body>
<p><span>Bahn</span><span>Lengdorf</span></p><div>650 m</div>
</body></html>
"""


def test_b14_train_station_inline_elements():
    with _patch_get(booking, BOOKING_TRAIN_INLINE):
        result = booking.scrape('https://www.booking.com/hotel/de/x.html')
    assert result['train_station'] == 'Lengdorf 650 m'


# ---------------------------------------------------------------------------
# B2: a Nebenkosten block without a price section must not raise
#     KeyError: 'price_10' and turn the whole house into 'Error'.
# ---------------------------------------------------------------------------
HUETTEN_NK_WITHOUT_PRICE = """
<html><head><title>t</title></head><body>
<h1>Testhaus</h1>
<div class="Nebenkosten"><ul>
  <li>Endreinigung pro Aufenthalt 80,00 €</li>
</ul></div>
</body></html>
"""

HUETTEN_URL = ('https://www.huetten.com/de/huette/testhaus-rt1.html'
               '#/vsc.php?calendar_date_from=2027-02-13&persons_adults=8&calendar_stays=7')


def test_b2_nebenkosten_without_price_section():
    with _patch_get(huetten, HUETTEN_NK_WITHOUT_PRICE):
        result = huetten.scrape(HUETTEN_URL)
    assert result['location'] == 'Testhaus'       # not swallowed into 'Error'
    assert result['total_costs'] == 'N/A'


# ---------------------------------------------------------------------------
# G4: with no addressLocality, the country alone is the address.
# ---------------------------------------------------------------------------
HUETTEN_BATHROOMS_AND_ADDRESS = """
<html><head><title>t</title></head><body>
<h1>Testhaus</h1>
<script type="application/ld+json">
{"@type": "LodgingBusiness", "name": "Testhaus",
 "address": {"@type": "PostalAddress", "addressCountry": "AT"}}
</script>
<p>Dieses Haus hat 4 Badezimmer. Schlafzimmer mit 1 Badezimmer ensuite.</p>
</body></html>
"""


def test_g4_address_falls_back_to_country():
    with _patch_get(huetten, HUETTEN_BATHROOMS_AND_ADDRESS):
        result = huetten.scrape('https://www.huetten.com/de/huette/testhaus-rt1.html')
    assert result['address'] == 'Österreich'


# ---------------------------------------------------------------------------
# B8: extra costs written "80,-- €" (euro sign after the amount).
# ---------------------------------------------------------------------------
def test_b8_extra_costs_euro_sign_after_amount():
    from bs4 import BeautifulSoup
    html = ('<div class="Nebenkosten"><ul>'
            '<li>Endreinigung pro Aufenthalt 80,-- €</li>'
            '</ul></div>')
    nk_div = BeautifulSoup(html, 'html.parser').find('div', class_='Nebenkosten')
    total = huetten._extra_costs_for_persons(nk_div, date(2027, 2, 13), 7, 8)
    assert total == '80 €'


# ---------------------------------------------------------------------------
# B10: a badge saying "nicht verfügbar" must not count as available.
# ---------------------------------------------------------------------------
INTERHOME_NEGATED_BADGE = """
<html><head><title>t</title></head><body>
<div data-test="available-badge">Derzeit nicht verfügbar</div>
<div data-test="total-price">6,501.00 CHF</div>
</body></html>
"""


def test_b10_negated_badge_is_unavailable():
    with _patch_get(interhome, INTERHOME_NEGATED_BADGE):
        result = interhome.scrape('https://www.interhome.at/de/p/x?adults=4')
    assert result['time'] == 'Unavailable'


# ---------------------------------------------------------------------------
# B3: a malformed cache entry must fall through to a fresh scrape, not raise
#     KeyError/ValueError out of scrape().
# ---------------------------------------------------------------------------
def test_b3_rodelwelten_cache_entry_without_fetched_at():
    url = 'https://www.rodelwelten.com/rodelbahn/testbahn.html'
    rodelwelten._cache.clear()
    rodelwelten._cache[url] = {'data': {'name': 'alt'}}  # no fetched_at
    try:
        with _patch_get(rodelwelten, '<html><body><h1>Neue Bahn</h1></body></html>'):
            result = rodelwelten.scrape(url)
    finally:
        rodelwelten._cache.clear()
    assert result['name'] == 'Neue Bahn'


def test_b3_outdooractive_cache_entry_with_bad_timestamp():
    url = 'https://www.outdooractive.com/de/rodelbahn/test/12345678.html'
    cache_key = url.split('#')[0].rstrip('/')
    outdooractive._cache.clear()
    outdooractive._cache[cache_key] = {'fetched_at': 'gestern', 'data': {'name': 'alt'}}
    page = ('<html><head><title>t</title></head><body>'
            '<script type="application/ld+json">'
            '{"name": "Neue Bahn", "amenityFeature": [], "potentialAction": {}}'
            '</script></body></html>')
    try:
        with _patch_get(outdooractive, page):
            result = outdooractive.scrape(url)
    finally:
        outdooractive._cache.clear()
    assert result['name'] == 'Neue Bahn'


# ---------------------------------------------------------------------------
# B4: EMPTY results must never share the module-level mutable lists.
# ---------------------------------------------------------------------------
def test_b4_common_empty_result_is_deep_copy():
    r1 = common.empty_result()
    r1['room_config'].append('junk')
    assert common.EMPTY['room_config'] == []
    assert common.empty_result()['room_config'] == []


def test_b4_outdooractive_empty_track_not_shared():
    r1 = outdooractive._empty_result()
    r1['track'].append([1.0, 2.0])
    r1['huts'].append('hut')
    assert outdooractive.EMPTY['track'] == []
    assert outdooractive.EMPTY['huts'] == []
    assert outdooractive._empty_result()['track'] == []


# ---------------------------------------------------------------------------
# B5: a cache hit must hand out a copy, not the live cache object.
# ---------------------------------------------------------------------------
def test_b5_cache_hit_returns_copy():
    url = 'https://www.rodelwelten.com/rodelbahn/testbahn.html'
    entry = {'fetched_at': datetime.now().isoformat(),
             'data': {'name': 'Alt', 'track': [[1.0, 2.0]], 'huts': []}}
    rodelwelten._cache.clear()
    rodelwelten._cache[url] = entry
    try:
        result = rodelwelten.scrape(url)
    finally:
        rodelwelten._cache.clear()
    assert result['name'] == 'Alt'
    result['name'] = 'mutated'
    result['track'].append([9.9, 9.9])
    result['track'][0][0] = 99.0
    result['huts'].append('junk')
    assert entry['data']['name'] == 'Alt'
    assert entry['data']['track'] == [[1.0, 2.0]]
    assert entry['data']['huts'] == []


# ---------------------------------------------------------------------------
# B6: one non-numeric altitude must not discard the whole result.
# ---------------------------------------------------------------------------
def _oa_page(distance='1.200', alt_to='1.550', alt_from='1.400'):
    ld = {
        'name': 'Testbahn',
        'potentialAction': {'distance': {'value': distance}},
        'amenityFeature': [
            {'name': 'altitude_to', 'value': alt_to},
            {'name': 'altitude_from', 'value': alt_from},
        ],
    }
    return ('<html><head><title>t</title></head><body><script type="application/ld+json">'
            + json.dumps(ld) + '</script></body></html>')


OA_URL = 'https://www.outdooractive.com/de/rodelbahn/test/12345678.html'


def test_b6_junk_altitude_keeps_rest_of_result():
    outdooractive._cache.clear()
    try:
        with _patch_get(outdooractive, _oa_page(alt_to='k. A.')):
            result = outdooractive.scrape(OA_URL, force_refresh=True)
    finally:
        outdooractive._cache.clear()
    assert result['name'] == 'Testbahn'
    assert result['height_top'] == 'N/A'
    assert result['height_bottom'] == '1400 m'


def test_b6_numeric_string_altitude_parsed():
    outdooractive._cache.clear()
    try:
        with _patch_get(outdooractive, _oa_page()):
            result = outdooractive.scrape(OA_URL, force_refresh=True)
    finally:
        outdooractive._cache.clear()
    assert result['height_top'] == '1550 m'
    assert result['elevation_diff'] == '150 m'


# ---------------------------------------------------------------------------
# B7: dot as thousands separator: '1.200' must mean 1200 m, not 1.2 m.
# ---------------------------------------------------------------------------
def test_b7_outdooractive_thousands_in_length():
    outdooractive._cache.clear()
    try:
        with _patch_get(outdooractive, _oa_page()):
            result = outdooractive.scrape(OA_URL, force_refresh=True)
    finally:
        outdooractive._cache.clear()
    assert result['length'] == '1.2 km'


def test_b7_rodelwelten_thousands_in_length():
    assert rodelwelten._normalize_length('1.200 m') == '1.2 km'


# ---------------------------------------------------------------------------
# B13: mixed separators must not crash _normalize_length.
# ---------------------------------------------------------------------------
def test_b13_rodelwelten_mixed_separators():
    assert rodelwelten._normalize_length('1.200,5 km') == '1200.5 km'
    assert rodelwelten._normalize_length('1,200 km') == '1.2 km'
    assert rodelwelten._normalize_length('N/A') == 'N/A'
    assert rodelwelten._normalize_length('') == ''


# ---------------------------------------------------------------------------
# B11: normalize_country(None) must return '' instead of AttributeError.
# ---------------------------------------------------------------------------
def test_b11_normalize_country_none():
    assert common.normalize_country(None) == ''
    assert common.normalize_country(' AT ') == 'Österreich'


# ---------------------------------------------------------------------------
# B12: the loipen circuit breaker counts only network failures, exposes
#      reset(), and a parse error must not trip it.
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _reset_loipen_breaker():
    loipen._breaker.reset()
    yield
    loipen._breaker.reset()


class _PostResp:
    def __init__(self, data):
        self._data = data
        self.encoding = 'utf-8'

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_b12_parse_error_does_not_trip_breaker():
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return _PostResp({'elements': [{}]})  # no 'type' key -> parse error

    with mock.patch.object(loipen.requests, 'post', side_effect=fake_post):
        for _ in range(3):
            assert loipen.fetch(47.0, 11.0) is None
    # New code: a parse error returns at once (1 call per fetch) and never
    # trips the breaker. Old code counted it as an endpoint failure, so all 3
    # endpoints were tried per fetch and the breaker opened after the 2nd
    # fetch (6 calls, then 0).
    assert len(calls) == 3
    assert not loipen._breaker.open


def test_b12_network_failure_trips_and_reset_rearms():
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        raise requests.ConnectionError('down')

    with mock.patch.object(loipen.requests, 'post', side_effect=fake_post):
        assert loipen.fetch(47.0, 11.0) is None
        assert loipen.fetch(47.0, 11.0) is None
        assert loipen._breaker.open
        calls.clear()
        assert loipen.fetch(47.0, 11.0) is None
        assert calls == []  # breaker open: no network traffic
        loipen._breaker.reset()
        assert loipen.fetch(47.0, 11.0) is None
        assert len(calls) == len(loipen._OVERPASS_URLS)  # re-armed


# ---------------------------------------------------------------------------
# B15: save_cache() before load_cache() must fail clearly, not with
#      FileNotFoundError from dirname('').
# ---------------------------------------------------------------------------
def test_b15_save_cache_without_load():
    for module in (rodelwelten, outdooractive):
        saved = module._cache_file
        module._cache_file = ''
        try:
            with pytest.raises(RuntimeError):
                module.save_cache()
        finally:
            module._cache_file = saved


# ---------------------------------------------------------------------------
# B4 (callers): the early-exit paths must not hand the shared EMPTY lists out.
# ---------------------------------------------------------------------------
def test_b4_outdooractive_early_exit_fresh_lists():
    outdooractive._cache.clear()
    # No 6-digit route ID -> early return of _empty_result() (was dict(EMPTY)).
    r1 = outdooractive.scrape('https://www.outdooractive.com/de/rodelbahn/x')
    r1['track'].append([1.0, 2.0])
    r1['huts'].append('junk')
    r2 = outdooractive.scrape('https://www.outdooractive.com/de/rodelbahn/x')
    assert r2['track'] == []
    assert r2['huts'] == []
    outdooractive._cache.clear()


def test_b4_rodelwelten_early_exit_fresh_lists():
    # Wrong domain -> early return of _empty_result() (was dict(EMPTY, track=[]),
    # which still shared EMPTY['huts']).
    r1 = rodelwelten.scrape('https://example.com/x')
    r1['huts'].append('junk')
    r2 = rodelwelten.scrape('https://example.com/x')
    assert r2['huts'] == []
