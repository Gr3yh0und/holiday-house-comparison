"""Tests for parsers/common.py's pure normalisation/parsing helpers.

Kept deliberately to logic with no network/browser dependency, per
WEBAPP_PROJECT_STANDARD.md §7 — the deploy gate needs *some* automated test,
not a full scraper integration suite.
"""
from bs4 import BeautifulSoup

from parsers.common import (
    clean_bed_desc,
    normalize_country,
    normalize_rating,
    parse_json_ld,
    parse_room_config,
)


def test_normalize_country_known_iso_code():
    assert normalize_country('AT') == 'Österreich'


def test_normalize_country_known_english_name():
    assert normalize_country('Switzerland') == 'Schweiz'


def test_normalize_country_unknown_passthrough():
    assert normalize_country('  Spain ') == 'Spain'


def test_normalize_rating_default_scale():
    assert normalize_rating('8.5') == '8.5'


def test_normalize_rating_scales_from_100():
    assert normalize_rating(92, best=100) == '9.2'


def test_normalize_rating_scales_from_5():
    assert normalize_rating(4.0, best=5) == '8.0'


def test_normalize_rating_comma_decimal():
    assert normalize_rating('8,7') == '8.7'


def test_normalize_rating_with_count():
    assert normalize_rating('8.5', count=42) == '8.5 (42 Bewertungen)'


def test_normalize_rating_zero_count_suppressed():
    assert normalize_rating('8.5', count=0) == '8.5'


def test_normalize_rating_invalid_value():
    assert normalize_rating('n/a') == 'N/A'


def test_normalize_rating_zero_best_treated_as_default_scale():
    # best=0 is falsy, so the function falls back to the 0-10 default scale
    # rather than dividing by zero.
    assert normalize_rating('8.5', best=0) == '8.5'


def test_clean_bed_desc_strips_length_suffix():
    assert clean_bed_desc('1 franz. Bett, Länge 200 cm') == '1 franz. Bett'


def test_clean_bed_desc_strips_bathroom_combo():
    assert clean_bed_desc('1 Doppelbett, Bad/Dusche/WC') == '1 Doppelbett'


def test_clean_bed_desc_collapses_1x_prefix():
    assert clean_bed_desc('1x 160cm') == '160cm'


def test_clean_bed_desc_normalises_cm_spacing():
    assert clean_bed_desc('1 Bett 160 cm') == '1 Bett 160cm'


def test_parse_room_config_single_room():
    # The "franz." abbreviation guard strips the trailing period so it isn't
    # mistaken for a sentence boundary by the `.\s+` segment splitter.
    desc = '1 Zimmer 20 m² mit 1 franz. Bett (160cm)'
    assert parse_room_config(desc) == ['1 franz Bett (160cm)']


def test_parse_room_config_multiple_beds_one_room():
    desc = '1 Zimmer 28 m2 mit 1 Diwanbett (130cm), 1 franz. Bett (160cm)'
    assert parse_room_config(desc) == ['1 Diwanbett (130cm), 1 franz Bett (160cm)']


def test_parse_room_config_repeated_rooms():
    desc = '3 abgeschrägte Zimmer, jedes Zimmer mit 1 franz. Bett (160cm)'
    assert parse_room_config(desc) == ['1 franz Bett (160cm)'] * 3


def test_parse_room_config_no_match_returns_empty():
    assert parse_room_config('Keine erkennbare Struktur hier') == []


def test_parse_json_ld_finds_matching_type():
    html = """
    <script type="application/ld+json">{"@type": "Hotel", "name": "Wrong"}</script>
    <script type="application/ld+json">{"@type": "LodgingBusiness", "name": "Right"}</script>
    """
    soup = BeautifulSoup(html, 'html.parser')
    result = parse_json_ld(soup, 'LodgingBusiness')
    assert result == {"@type": "LodgingBusiness", "name": "Right"}


def test_parse_json_ld_no_match_returns_empty_dict():
    html = '<script type="application/ld+json">{"@type": "Hotel"}</script>'
    soup = BeautifulSoup(html, 'html.parser')
    assert parse_json_ld(soup, 'LodgingBusiness') == {}


def test_parse_json_ld_malformed_json_skipped():
    html = '<script type="application/ld+json">{not valid json}</script>'
    soup = BeautifulSoup(html, 'html.parser')
    assert parse_json_ld(soup, 'LodgingBusiness') == {}
