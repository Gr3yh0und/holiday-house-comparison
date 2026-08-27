"""Tests for app._validate_scrape_output — the §15B output-validation gate.

Imports `app`, which requires Flask; skipped in environments where the full
requirements.txt (including Flask/Selenium) isn't installed. CI installs
requirements-dev.txt, so it runs there.
"""
import pytest

flask = pytest.importorskip("flask")

from app import _validate_scrape_output  # noqa: E402  pylint: disable=wrong-import-position


def test_validate_rejects_empty_trip_list():
    assert _validate_scrape_output([]) is False


def test_validate_rejects_trips_with_no_houses():
    assert _validate_scrape_output([{'name': 'Trip A', 'houses': []}]) is False


def test_validate_accepts_at_least_one_house():
    trips = [
        {'name': 'Trip A', 'houses': []},
        {'name': 'Trip B', 'houses': [{'name': 'House 1'}]},
    ]
    assert _validate_scrape_output(trips) is True
