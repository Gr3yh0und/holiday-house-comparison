"""Template render smoke test for templates/index.html.

Renders the page through app._render_html — the same function the
``--from-cache`` deploy path uses to write public/index.html — with a minimal
trips fixture covering the findings of the 2026-10-28 code review: scraped
strings that must never reach HTML unescaped, a loipe without a distance, a
house without usable coordinates, and non-http links in input data.
"""

import re
import shutil
import subprocess

import pytest

flask = pytest.importorskip("flask")

import app as app_module  # noqa: E402  pylint: disable=wrong-import-position

from parsers.common import EMPTY  # noqa: E402

_XSS_NAME = '<img src=x onerror=alert(1)>'

_SLED_RUN = {
    'name': _XSS_NAME,
    'url': 'https://www.rodelwelten.com/rodelbahn/test',
    'track': [[47.0, 11.0], [47.01, 11.01]],
}

_LOIPE = {'name': 'Testloipe', 'length_km': 3.2, 'distance_km': None,
          'difficulty': 'blau', 'grooming': 'Ja',
          'track': [[47.0, 11.0], [47.01, 11.01]]}


def _house(**kw):
    house = dict(EMPTY, room_config=[], time='check_manually')
    house.update(kw)
    return house


_TRIPS = [
    {
        'name': 'Weihnachten',
        'checkin': '2027-12-23',
        'checkout': '2027-12-30',
        'houses': [
            _house(
                name=_XSS_NAME, lat=47.0, lon=11.0, price='1.200 €',
                rooms='5', persons='10',
                house_url='javascript:alert(1)',
                direct_url='javascript:alert(2)',
                pois=[{'type': 'train', 'label': _XSS_NAME, 'lat': 47.01, 'lon': 11.01}],
                sled_runs=[dict(_SLED_RUN, huts=[{'name': 'Bergbahn', 'url': 'javascript:alert(3)'}])],
                loipen=[_LOIPE],
                train_track=[[47.0, 11.0], [47.01, 11.01]],
            ),
            _house(
                name='Zweite Behausung', lat=47.2, lon=11.2, price='900 €',
                rooms='6', persons='12',
                house_url='https://www.fewo-direkt.de/p1',
                sled_runs=[dict(_SLED_RUN, name='Zweite Rodelbahn',
                                url='https://www.rodelwelten.com/rodelbahn/zwei')],
                train_track=[[47.3, 11.3], [47.31, 11.31]],
            ),
            _house(name='Ohne Koordinaten', rooms='4', persons='8', lat=None, lon=None),
            _house(name='Koordinaten als Text', rooms='4', persons='8', lat='47.4', lon='11.4'),
            _house(name='Nordpol-Haus', rooms='4', persons='8', lat='Nordpol', lon='11.6'),
        ],
    },
    {
        'name': 'Silvester',
        'checkin': '',
        'checkout': '',
        'houses': [
            _house(name='Silvesterhaus', lat=47.5, lon=11.5, price='1.500 €',
                   rooms='7', persons='10'),
        ],
    },
]


@pytest.fixture(name='page')
def fixture_page():
    with app_module.app.app_context():
        return app_module._render_html(  # pylint: disable=protected-access
            'Test', _TRIPS, '2026-10-28 12:00', 'dev',
        )


def _eval_in_node(source):
    """Evaluate a JS expression in node and return its stdout (stripped)."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    result = subprocess.run(
        [node, '-e', source], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _run_function(page, name, args_js):
    """Extract ``function <name>(...)`` from the rendered page and call it in node."""
    match = re.search(rf'function {name}\(.*?\n        \}}', page, re.DOTALL)
    assert match, f'{name}() missing from the rendered page'
    return _eval_in_node(
        'window = { ALL_TRANSLATIONS: { "de-DE": { weekdays: [] } }, CURRENT_LANG: "de-DE" };\n'
        + match.group(0)
        + f'\nconsole.log(String({name}({args_js})));'
    )


# ── HIGH: unescaped HTML in overview-map tooltips ────────────────────────

def test_house_name_never_reaches_raw_html(page):
    assert _XSS_NAME not in page


def test_overview_map_tooltips_escape_values(page):
    assert "escHtml(e.houseName)" in page
    assert "escHtml(e.tripName)" in page
    assert "escHtml(e.price)" in page


def test_bind_tooltip_names_go_through_esc_html(page):
    # every scraped/config string interpolated into a bindTooltip() HTML
    # string is escaped: sled runs (overview + house map), loipes, POIs,
    # bus track label, and the house-name tooltips read back from data-*
    assert "bindTooltip('🛷 ' + escHtml(run.name)" in page
    assert "bindTooltip('⛷️ ' + escHtml(loipe.name)" in page
    assert "bindTooltip(escHtml(poi.label || poi.type || 'POI')" in page
    assert "bindTooltip('🚌 ' + escHtml(busTrack.label || 'Bus')" in page
    assert "bindTooltip(escHtml(el.dataset.name || T('poi.house_label'))" in page
    assert "bindTooltip(escHtml(houseMapEl.dataset.name || " in page


# ── MEDIUM: no http(s) scheme check on scraped/config links ──────────────

def test_javascript_urls_are_not_rendered_as_links(page):
    assert 'href="javascript:' not in page
    # values inside forceescaped data-* JSON attributes are inert data, but
    # nothing may build an anchor from them: every emitted href is checked
    hrefs = re.findall(r'<a\b[^>]*href="([^"]*)"', page)
    assert hrefs
    for href in hrefs:
        assert href.startswith(('https://', 'http://', '#', 'mailto:')), href


def test_data_driven_blank_links_carry_rel_noopener(page):
    anchors = re.findall(r'<a [^>]*target="_blank"[^>]*>', page)
    assert anchors
    for anchor in anchors:
        assert 'rel="noopener noreferrer"' in anchor


# ── MEDIUM: only the first train track is ever drawn ─────────────────────

def test_every_unique_train_track_is_drawn(page):
    assert 'if (trainTrackSeen) return;' not in page
    # dedup must be per unique track (like sledRunMap), not one global flag
    assert re.search(r'var trainTrackSeen = false;', page) is None
    assert 'JSON.stringify(house.trainTrack)' in page


# ── MEDIUM: house map / maps link check only "is defined" ────────────────

def test_no_nan_house_map_for_missing_coordinates(page):
    assert 'data-lat="None"' not in page
    assert 'data-lon="None"' not in page
    assert 'maps?q=,' not in page
    assert 'maps?q=None' not in page


def test_overview_map_only_gets_numeric_coordinates(page):
    # a non-numeric coordinate used to be emitted raw (`lat: Nordpol,`) — a
    # syntax error that kills the whole script block; a numeric string breaks
    # house.lat.toFixed(5) and takes the overview-map IIFE down with it
    bad = [m.group(1) for m in re.finditer(r'^\s*(?:lat|lon):\s*(\S+),?$', page, re.M)
           if not re.match(r'-?\d', m.group(1))]
    assert bad == []


# ── MEDIUM: dead fallback language key ───────────────────────────────────

def test_translation_fallback_uses_a_real_key(page):
    assert "ALL_TRANSLATIONS['de']" not in page
    assert "ALL_TRANSLATIONS['de-DE']" in page


# ── LOW: "None km" ───────────────────────────────────────────────────────

def test_loipe_without_distance_shows_no_none(page):
    assert 'None km' not in page


# ── LOW: sidebar and map use different trip palettes ─────────────────────

def test_sidebar_and_map_share_one_trip_palette(page):
    assert '#2563eb' not in page
    sidebar = re.search(r'border-left-color: (#[0-9a-f]{6}); cursor: pointer', page)
    palette = re.search(r'window\.TRIP_COLORS = \[(.*?)\]', page)
    assert sidebar and palette
    colors = re.findall(r'#[0-9a-f]{6}', palette.group(1))
    assert colors
    assert sidebar.group(1) == colors[0]


# ── unverified → verified real: formatDate has no guard for a bad date ───

@pytest.mark.parametrize('bad', ["''", "'None'", "'kein datum'"])
def test_format_date_never_returns_nan(page, bad):
    assert 'NaN' not in _run_function(page, 'formatDate', f'{bad}, "de-DE"')
