"""Tests for the booking.com and fewo-direkt.de house parsers on trimmed-down
copies of their current (2026-10) page layouts."""
from unittest import mock

import pytest

from parsers import booking, fewo


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
