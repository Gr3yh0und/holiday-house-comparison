"""Tests for the cache-handling fixes from the 2026-10-28 code review.

One test per finding: the --house updated_at stamp, the fragile cache reads
(_load_houses_cache, _fetch_loipen), the bus_stop distance string, and the
LOW-severity items (_write_json_atomic, broker_name, _store_scrape_result,
_normalize_input). All run against tmp_path files, no network.
"""
import json
import os
from datetime import datetime, timedelta

import pytest

flask = pytest.importorskip("flask")

import app as app_module  # noqa: E402  pylint: disable=wrong-import-position


# ---------------------------------------------------------------------------
# MEDIUM: --house must not stamp updated_at when the scrape failed
# ---------------------------------------------------------------------------

def test_house_patch_is_fresh_only_when_something_was_scraped():
    """_house_patch_is_fresh: True only when at least one date group beat the cache."""
    fresh = {'attempted': 1, 'failed': 0, 'cached': 0}
    assert app_module._house_patch_is_fresh(fresh, 1) is True
    # Every date group fell back to cached data — stale, must not re-stamp.
    all_cached = {'attempted': 1, 'failed': 0, 'cached': 1}
    assert app_module._house_patch_is_fresh(all_cached, 1) is False
    # Every scrape failed with no cache (all-N/A placeholder) — also stale.
    all_failed = {'attempted': 1, 'failed': 1, 'cached': 0}
    assert app_module._house_patch_is_fresh(all_failed, 1) is False
    # One group fresh, one cached — the run did produce new data.
    assert app_module._house_patch_is_fresh({'attempted': 2, 'failed': 0, 'cached': 1}, 2) is True


def test_scrape_one_house_marks_cache_fallback_in_stats(monkeypatch):
    """The cache-fallback path records stats['cached'] so the --house caller can
    tell a stale patch from a fresh scrape."""
    marker = {'name': 'Casa Vecchia', 'price': '100 €'}
    monkeypatch.setattr(app_module, 'scrape_house', lambda url, driver=None: None)
    monkeypatch.setattr(app_module, '_load_cached_house', lambda *a: dict(marker))
    stats = {'attempted': 0, 'failed': 0}
    result = app_module._scrape_one_house(
        {'name': 'Casa Vecchia', 'house_url': 'https://www.booking.com/h/1'},
        '2027-01-01', '2027-01-08', stats=stats,
    )
    assert result == marker
    assert stats == {'attempted': 1, 'failed': 0, 'cached': 1}


# ---------------------------------------------------------------------------
# MEDIUM: _load_houses_cache must survive unexpected content, not just
# FileNotFoundError / JSONDecodeError
# ---------------------------------------------------------------------------

def test_load_houses_cache_survives_unreadable_file(tmp_path, monkeypatch):
    """PermissionError, UnicodeDecodeError and IsADirectoryError all mean "no cache"."""
    cache = tmp_path / "houses.json"
    monkeypatch.setattr(app_module, "HOUSES_CACHE_FILE", str(cache))

    cache.write_bytes(b'{"trips": [\xff\xfe broken utf-8}')
    assert app_module._load_houses_cache() is None

    cache.unlink()
    cache.mkdir()
    assert app_module._load_houses_cache() is None
    cache.rmdir()

    cache.write_text('{"trips": []}')
    os.chmod(cache, 0)
    try:
        if os.geteuid() != 0:  # root ignores the permission bits
            assert app_module._load_houses_cache() is None
    finally:
        os.chmod(cache, 0o644)


# ---------------------------------------------------------------------------
# MEDIUM: _fetch_loipen must skip a malformed cache entry instead of crashing
# ---------------------------------------------------------------------------

def _loipen_cache_file(tmp_path, monkeypatch, payload):
    cache = tmp_path / "loipen.json"
    cache.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(app_module, "LOIPEN_CACHE_FILE", str(cache))
    return cache


@pytest.mark.parametrize("entry", [
    {},                                        # no fetched_at, no loipen
    {"loipen": []},                            # no fetched_at
    {"fetched_at": "garbage", "loipen": []},   # malformed timestamp
    {"fetched_at": 12345, "loipen": []},       # wrong type
    {"fetched_at": "2026-10-28 10:00"},        # no loipen list
])
def test_fetch_loipen_skips_malformed_entry(tmp_path, monkeypatch, entry):
    """A corrupt entry is a cache miss (refetch), not a build-aborting crash."""
    fresh = [{"name": "Neue Loipe"}]
    calls = []

    def fake_fetch(lat, lon, radius_m=None, force_refresh=False):
        calls.append((lat, lon))
        return fresh

    monkeypatch.setattr(app_module.loipen_parser, "fetch", fake_fetch)
    _loipen_cache_file(tmp_path, monkeypatch, {"47.1000,11.1000": entry})

    assert app_module._fetch_loipen(47.1, 11.1) == fresh
    assert calls == [(47.1, 11.1)]


def test_fetch_loipen_valid_entry_still_cache_hit(tmp_path, monkeypatch):
    """The fix must not break the normal cache-hit path."""
    cached_trails = [{"name": "Alte Loipe"}]
    entry = {
        "fetched_at": (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
        "loipen": cached_trails,
    }
    monkeypatch.setattr(app_module.loipen_parser, "fetch",
                        lambda *a, **k: pytest.fail("cache hit must not refetch"))
    _loipen_cache_file(tmp_path, monkeypatch, {"47.1000,11.1000": entry})

    assert app_module._fetch_loipen(47.1, 11.1) == cached_trails


# ---------------------------------------------------------------------------
# MEDIUM: bus_stop distance string must not mangle POI labels with a period
# ---------------------------------------------------------------------------

def test_bus_stop_keeps_period_in_poi_label(monkeypatch):
    """'Haltestelle St. Johann' must survive — only the number gets a decimal comma."""
    def fake_scrape(url, driver=None):
        info = dict(app_module._PARSER_EMPTY, room_config=[])
        info['price'] = '100 €'  # not an all-N/A result -> not treated as a scrape failure
        info['bus_stop'] = ''  # parser found no bus stop -> auto-compute from the POI
        return info

    monkeypatch.setattr(app_module, 'scrape_house', fake_scrape)
    monkeypatch.setattr(app_module, "_fetch_loipen", lambda *a, **k: [])
    house = {
        "name": "Casa",
        "house_url": "https://www.booking.com/h/1",
        "lat": 47.100, "lon": 11.100,
        "disable_loipen": False,
        "pois": [{"type": "bus", "label": "Haltestelle St. Johann",
                  "lat": 47.108, "lon": 11.100}],
    }
    result = app_module._scrape_one_house(house, "", "")
    assert result["bus_stop"].startswith("Haltestelle St. Johann (")
    assert "," in result["bus_stop"].split("(")[1]  # decimal comma on the distance


# ---------------------------------------------------------------------------
# LOW: _write_json_atomic — unique temp file + fsync
# ---------------------------------------------------------------------------

def test_write_json_atomic_uses_unique_tmp_and_fsync(tmp_path, monkeypatch):
    path = tmp_path / "out.json"
    # A stale '<path>.tmp' from another run must be left alone (fixed name would clobber it).
    sentinel = tmp_path / "out.json.tmp"
    sentinel.write_text("other run's payload")
    fsynced = []
    monkeypatch.setattr(os, "fsync", lambda fd: fsynced.append(fd))

    app_module._write_json_atomic(str(path), {"a": 1})

    assert json.loads(path.read_text()) == {"a": 1}
    assert sentinel.read_text() == "other run's payload"
    assert fsynced, "os.fsync must run before os.replace"
    # No leftover temp file from this run; only the payload and the other run's sentinel.
    assert {p.name for p in tmp_path.iterdir()} == {"out.json", "out.json.tmp"}


# ---------------------------------------------------------------------------
# LOW: broker_name must match on the host, not anywhere in the URL
# ---------------------------------------------------------------------------

def test_broker_name_matches_host_only():
    assert app_module.broker_name("https://www.booking.com/hotel/de/x.html") == "Booking.com"
    assert app_module.broker_name("https://www.fewo-direkt.de/objekt?foo=1") == "fewo-direkt"
    # Another broker's domain in the path/query must not attribute the link to it.
    assert app_module.broker_name(
        "https://www.example.com/redirect?target=https://www.booking.com/hotel/x") == ""
    assert app_module.broker_name("https://notbooking.com.evil.example/h") == ""


# ---------------------------------------------------------------------------
# LOW: _store_scrape_result failure branch must use previous.get('updated_at')
# ---------------------------------------------------------------------------

def test_store_scrape_result_failure_with_previous_lacking_updated_at(tmp_path, monkeypatch):
    """A hand-edited cache without 'updated_at' must not raise KeyError."""
    monkeypatch.setattr(app_module, "HOUSES_CACHE_FILE", str(tmp_path / "houses.json"))
    monkeypatch.setattr(app_module, "HEALTH_FILE", str(tmp_path / "health.json"))
    (tmp_path / "houses.json").write_text(json.dumps({"trips": [], "status": "ok"}))

    # No trip has houses -> _validate_scrape_output returns False -> failure branch.
    assert app_module._store_scrape_result([], {"attempted": 0, "failed": 0}) is None

    health = json.loads((tmp_path / "health.json").read_text())
    assert health["status"] == "down"
    assert health["last_update"] is None


# ---------------------------------------------------------------------------
# LOW: _normalize_input must raise a pointed error on a missing name
# ---------------------------------------------------------------------------

def test_normalize_input_missing_trip_name_exits_with_message():
    data = {"title": "T", "houses": [
        {"name": "Casa", "trips": [{"checkin": "2027-01-01"}]},
    ]}
    with pytest.raises(SystemExit) as excinfo:
        app_module._normalize_input(data)
    assert 'input.json' in str(excinfo.value) and '"name"' in str(excinfo.value)


def test_normalize_input_missing_house_name_exits_with_message():
    data = {"title": "T", "houses": [{"trips": [{"name": "Weihnachten"}]}]}
    with pytest.raises(SystemExit) as excinfo:
        app_module._normalize_input(data)
    assert 'input.json' in str(excinfo.value) and '"name"' in str(excinfo.value)


def test_normalize_input_trip_centric_missing_house_name_exits():
    """The trip-centric format feeds house['name'] in build_trip_data / --house."""
    data = {"title": "T", "trips": [
        {"name": "Weihnachten", "houses": [{"house_url": "https://x"}]},
    ]}
    with pytest.raises(SystemExit) as excinfo:
        app_module._normalize_input(data)
    assert 'input.json' in str(excinfo.value) and '"name"' in str(excinfo.value)


def test_normalize_input_valid_still_converts():
    """The fix must not break the house-centric -> trip-centric conversion."""
    data = {"title": "T", "houses": [
        {"name": "Casa", "price": "500 €", "template": True, "trips": [{"name": "X"}]},
        {"name": "Chalet", "price": "600 €", "trips": [
            {"name": "Weihnachten", "checkin": "2027-01-01", "checkout": "2027-01-08",
             "price": "700 €"},
        ]},
    ]}
    result = app_module._normalize_input(data)
    assert [t["name"] for t in result["trips"]] == ["Weihnachten"]
    house = result["trips"][0]["houses"][0]
    assert house["name"] == "Chalet"
    assert house["price"] == "700 €"  # trip override wins over the house base
