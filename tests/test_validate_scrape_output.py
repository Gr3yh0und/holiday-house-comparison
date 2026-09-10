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


def test_validate_rejects_all_scrapes_failed_even_with_placeholder_houses():
    """_scrape_one_house always appends a placeholder house even when every
    scrape fails, so `houses` alone can never be empty for this failure mode --
    only scrape_stats (attempted vs. failed) can tell it apart from real data.
    """
    trips = [{'name': 'Trip A', 'houses': [{'name': 'House 1'}, {'name': 'House 2'}]}]
    scrape_stats = {'attempted': 2, 'failed': 2}
    assert _validate_scrape_output(trips, scrape_stats) is False


def test_validate_accepts_partial_scrape_success():
    trips = [{'name': 'Trip A', 'houses': [{'name': 'House 1'}, {'name': 'House 2'}]}]
    scrape_stats = {'attempted': 2, 'failed': 1}
    assert _validate_scrape_output(trips, scrape_stats) is True


def test_validate_accepts_manual_only_houses_with_no_scrape_attempts():
    """Houses with no house_url are never counted as scrape attempts -- an
    all-manual dataset must not trip the failure gate."""
    trips = [{'name': 'Trip A', 'houses': [{'name': 'House 1'}]}]
    scrape_stats = {'attempted': 0, 'failed': 0}
    assert _validate_scrape_output(trips, scrape_stats) is True
