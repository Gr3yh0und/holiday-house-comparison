"""Tests for the booking.com and fewo-direkt.de house parsers on trimmed-down
copies of their current (2026-10) page layouts."""
import json
from unittest import mock

import pytest
from selenium.common.exceptions import NoSuchElementException, TimeoutException

from parsers import booking, fewo, interhome


class _FakeDriver:
    def __init__(self, html):
        self.page_source = html

    def get(self, _url):
        pass

    def execute_script(self, *_args):
        return 0

    def find_element(self, *_args):
        raise LookupError


def _scrape(module, html):
    driver = _FakeDriver(html)
    if module is fewo:
        fewo._warmed_drivers.add(id(driver))  # pylint: disable=protected-access
    with mock.patch('time.sleep'):
        return module.scrape('https://example.invalid/', driver)


_PAD = '<!--' + 'x' * 6000 + '-->'  # fewo treats pages under 5000 chars as bot pages

_BOOKING_HEAD = '''<html><head><title>Haus Haas</title>
<script type="application/ld+json">{"@type": "Hotel", "name": "Haus Haas",
 "address": {"streetAddress": "Weg 1, 6773 Vandans, Österreich", "addressCountry": "AT"},
 "aggregateRating": {"ratingValue": 8, "bestRating": 10, "reviewCount": 4}}</script>
<script>var i18n = "An Ihren Reisedaten auf unserer Seite nicht verfügbar";</script>
</head><body>
<div data-testid="property-highlights">Die ganze Unterkunft gehört Ihnen 140 m² groß</div>
'''


def _rate_row(persons, price):
    return (f'<tr class="js-rt-block-row"><td class="hprt-occupancy-occupancy-info">'
            f'× {persons} max. Personenzahl: {persons}</td>'
            f'<td><div class="bui-price-display__value">€ {price}</div></td></tr>')


BOOKING_FREE = _BOOKING_HEAD + '''
<table class="hprt-table"><tr class="js-rt-block-row"><td>
<a data-testid="rt-name-link">Haus mit 4 Schlafzimmern</a>
<div class="hprt-roomtype-bed">Schlafzimmer 1: 1 Doppelbett Schlafzimmer 2: 2 Einzelbetten
Wohnzimmer: 2 Schlafsofas Badezimmer: 2</div></td></tr>
''' + _rate_row(8, '3.307') + _rate_row(8, '3.042') + _rate_row(6, '2.726') + '''
</table>
<p>Andere Daten: 11. Feb. – 16. Feb. Ab € 1.234</p></body></html>'''

BOOKING_BOOKED_OUT = _BOOKING_HEAD + '''
<table><tr><th><a data-testid="rt-name-link">Haus mit 4 Schlafzimmern</a></th>
<td>× 8</td><td>An Ihren Reisedaten auf unserer Seite nicht verfügbar</td></tr></table>
<p>Haus Haas verfügbar: 11. Feb. – 16. Feb. 5 Nächte Ab € 3.451</p></body></html>'''


def test_booking_free_dates_take_cheapest_rate_for_full_group():
    r = _scrape(booking, BOOKING_FREE)
    assert r['price'] == '€ 3.042'
    assert r['time'] == 'Available'
    assert r['persons'] == '8'
    assert r['sqm'] == '140 m²'
    assert r['bathrooms'] == '2'
    assert r['room_config'] == ['1 Doppelbett', '2 Einzelbetten']
    assert r['address'] == 'Vandans, Österreich'


def test_booking_booked_out_never_takes_a_price_for_other_dates():
    r = _scrape(booking, BOOKING_BOOKED_OUT)
    assert r['price'] == 'N/A'
    assert r['time'] == 'Unavailable'
    assert r['persons'] == '8'


_FEWO_HEAD = '<html><head><title>Ferienhaus Arche</title></head><body>' + _PAD + '''
<div data-stid="content-hotel-title"><h1>Ferienhaus Arche</h1></div>
<div itemprop="address"><meta itemprop="addressCountry" content="CHE">
<meta itemprop="addressLocality" content="Wengen"></div>
<p>4 Schlafzimmer 3 Badezimmer Platz für 10 Gäste</p>
<div data-stid="content-item"><span>Zimmer</span><h4>Schlafzimmer 1</h4><p>1 King-Bett</p></div>
<div data-stid="rating-tile-view">9,4 Außergewöhnlich 32 Bewertungen</div>
<p>Kommunikation 9,8 von 10 Bewertungen9,69,6 von 10</p>
'''

FEWO_FREE = _FEWO_HEAD + '''<div data-stid="property-offers">Deine Daten sind verfügbar
4.772 € Der aktuelle Preis beträgt 4.772 €. für 1 Ferienunterkunft, 7 Nächte</div></body></html>'''

FEWO_BOOKED_OUT = _FEWO_HEAD + '''<div data-stid="property-offers">Für deinen Reisezeitraum ist
diese Unterkunft bei FeWo-direkt leider nicht verfügbar.</div></body></html>'''


@pytest.mark.parametrize('html, price, time', [
    (FEWO_FREE, '4.772 €', 'Available'),
    (FEWO_BOOKED_OUT, 'N/A', 'Unavailable'),
])
def test_fewo_current_layout(html, price, time):
    r = _scrape(fewo, html)
    assert r['price'] == price
    assert r['time'] == time
    assert r['address'] == 'Wengen, Schweiz'
    assert r['rating'] == '9.4 (32 Bewertungen)'
    assert r['room_config'] == ['1 King-Bett']
    assert (r['rooms'], r['bathrooms'], r['persons']) == ('4', '3', '10')


def test_booking_and_fewo_scrape_the_listing_photo():
    booking_html = BOOKING_FREE.replace(
        '</head>', '<meta property="og:image" content="https://cf.bstatic.com/x/1.jpg"></head>')
    fewo_html = FEWO_FREE.replace(
        '<div itemprop="address">',
        '<meta itemprop="image" content="https://media.vrbo.com/2.jpg"><div itemprop="address">')
    assert _scrape(booking, booking_html)['image_url'] == 'https://cf.bstatic.com/x/1.jpg'
    assert _scrape(fewo, fewo_html)['image_url'] == 'https://media.vrbo.com/2.jpg'


def test_interhome_clean_url_keeps_only_dates_and_guests():
    url = ('https://www.interhome.de/rental/fa4a?duration=7&location=56a7&arrival=2027-02-20&persons=8'
           '&adults=8&sd=5460&pCon=6842%7CEUR%7C2027-02-20&clickId=ZW&searchId=7348&priceRate=refund')
    # pCon/sd/searchId pinned the page to an old search and showed a free house as booked out
    assert interhome._clean_url(url) == (  # pylint: disable=protected-access
        'https://www.interhome.de/rental/fa4a?arrival=2027-02-20&duration=7&adults=8&persons=8')


_INTERHOME_OFFER = {
    'generalTitle': 'Ferienwohnung Josefa am Buchhammerhof',
    'location': {'level1': 'Österreich', 'level4': 'Gemeinde Kaunderberg'},
    'locationLowestLevel': 'Gemeinde Kaunderberg',
    'bedrooms': 4, 'bathrooms': 1, 'persons': 8, 'squareMeter': '120 m²',
    'ratings': {'value': '10,0', 'starValue': '5,0', 'reviewCount': 8},
    'images': [{'large': '//cdn.hometogo.net/large/v2/898/3c9/abc.webp'}],
    'categorizedAmenities': {'groups': [
        {'id': 'top', 'type': 'top', 'items': [{'label': 'WLAN'}]},
        {'id': 'not_included', 'type': 'not_included', 'items': [{'label': 'Sauna'}]},
    ]},
}


class _InterhomeDriver(_FakeDriver):
    def __init__(self, html, badge, price=''):
        super().__init__(html)
        self._texts = {'[data-test="available-badge"]': badge, '[data-test="total-price"]': price}

    def find_element(self, *args):
        css = args[-1]
        if not self._texts.get(css):
            raise NoSuchElementException(css)
        return mock.Mock(text=self._texts[css])

    def find_elements(self, _by, css):
        return [mock.Mock(text=self._texts[css])] if self._texts.get(css) else []


@pytest.mark.parametrize('badge, price, want_price, want_time', [
    ('Deine Daten sind verfügbar', 'Gesamtpreis für 7 Nächte €6,842.00', '6842 €', 'Available'),
    ('Für deine Daten leider ausgebucht', '', 'N/A', 'Unavailable'),
])
def test_interhome_current_layout(badge, price, want_price, want_time):
    html = ('<html><body><script type="application/json" data-rtk-endpoint="rentalOfferDetails">'
            + json.dumps(_INTERHOME_OFFER) + '</script>'
            '<div data-test="rental-description">5-Zimmer-Wohnung 120 m². 4 Doppelzimmer. Bad/Dusche/WC. '
            'Im Ort: Hallenbad, Sauna, Solarium.</div>'
            '</body></html>')
    driver = _InterhomeDriver(html, badge, price)

    # The fake page never changes, so don't sit out the real timeouts.
    class _OneShotWait:  # pylint: disable=too-few-public-methods
        def __init__(self, drv, _timeout):
            self.drv = drv

        def until(self, condition):
            if not condition(self.drv):
                raise TimeoutException()

    with mock.patch('time.sleep'), mock.patch('selenium.webdriver.support.ui.WebDriverWait', _OneShotWait):
        r = interhome.scrape('https://www.interhome.de/rental/x?arrival=2027-02-20&duration=7&adults=8', driver)
    assert r['location'] == 'Ferienwohnung Josefa am Buchhammerhof'
    assert r['address'] == 'Kaunderberg, Österreich'
    assert (r['rooms'], r['bathrooms'], r['persons'], r['sqm']) == ('4', '1', '8', '120 m²')
    assert r['rating'] == '10.0 (8 Bewertungen)'
    assert r['image_url'] == 'https://cdn.hometogo.net/large/v2/898/3c9/abc.webp'
    assert (r['price'], r['time']) == (want_price, want_time)
    assert r['sauna'] == 'Nein', "a sauna in the village or under 'Nicht enthalten' is not the house's"
