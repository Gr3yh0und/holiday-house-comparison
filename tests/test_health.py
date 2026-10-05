"""Tests for app._write_health / _read_repo_version -- the health.json
producer behind WEBAPP_PROJECT_STANDARD.md §6a's <route>/health endpoint.

Imports `app`, which requires Flask; skipped in environments where the full
requirements.txt (including Flask/Selenium) isn't installed. CI installs
requirements-dev.txt, so it runs there.
"""
import json

import pytest

flask = pytest.importorskip("flask")

import app as app_module  # noqa: E402  pylint: disable=wrong-import-position


def test_read_repo_version_returns_stripped_content(tmp_path, monkeypatch):
    version_file = tmp_path / "VERSION"
    version_file.write_text("2.3.1\n")
    monkeypatch.setattr(app_module, "VERSION_FILE", str(version_file))
    assert app_module._read_repo_version() == "2.3.1"


def test_read_repo_version_falls_back_to_dev_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "VERSION_FILE", str(tmp_path / "missing-VERSION"))
    assert app_module._read_repo_version() == "dev"


def test_write_health_writes_given_status_and_last_update(tmp_path, monkeypatch):
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.5.0")
    monkeypatch.setattr(app_module, "HEALTH_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(app_module, "VERSION_FILE", str(version_file))

    app_module._write_health("ok", "2026-10-05 12:00")

    health = json.loads((tmp_path / "health.json").read_text())
    assert health == {
        "version": "1.5.0",
        "status": "ok",
        "last_update": "2026-10-05 12:00",
        "extra": {},
    }


@pytest.fixture(name="paths")
def fixture_paths(tmp_path, monkeypatch):
    """Point every file app.py's scrape/render steps touch into tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module, "HEALTH_FILE", str(tmp_path / "public" / "health.json"))
    monkeypatch.setattr(app_module, "VERSION_FILE", str(tmp_path / "VERSION"))
    monkeypatch.setattr(app_module, "HOUSES_CACHE_FILE", str(tmp_path / "cache" / "houses.json"))
    monkeypatch.setattr(app_module, "HOUSES_PARTIAL_FILE", str(tmp_path / "cache" / "houses.partial.json"))
    return tmp_path


def _trips(name="House A"):
    house = dict(app_module._PARSER_EMPTY, room_config=[], name=name, house_url="",
                 checkin="2027-02-13", checkout="2027-02-20")
    return [{"name": "Trip", "checkin": "2027-02-13", "checkout": "2027-02-20", "houses": [house]}]


def test_successful_scrape_replaces_cache_and_drops_partial(paths):
    (paths / "cache").mkdir()
    (paths / "cache" / "houses.partial.json").write_text("{}")

    cached = app_module._store_scrape_result(_trips(), {"attempted": 1, "failed": 0})

    on_disk = json.loads((paths / "cache" / "houses.json").read_text())
    assert on_disk == cached
    assert on_disk["status"] == "ok"
    assert on_disk["trips"][0]["houses"][0]["name"] == "House A"
    assert not (paths / "cache" / "houses.partial.json").exists()


def test_scrape_with_some_failures_is_degraded(paths):  # pylint: disable=unused-argument
    cached = app_module._store_scrape_result(_trips(), {"attempted": 2, "failed": 1})
    assert cached["status"] == "degraded"


def test_failed_scrape_keeps_last_good_data_and_flags_down(paths):
    app_module._store_scrape_result(_trips("Good House"), {"attempted": 1, "failed": 0})
    good = json.loads((paths / "cache" / "houses.json").read_text())

    result = app_module._store_scrape_result([], {"attempted": 0, "failed": 0})

    assert result is None
    after = json.loads((paths / "cache" / "houses.json").read_text())
    assert after["trips"] == good["trips"], "a failed scrape must never replace the last good data"
    assert after["updated_at"] == good["updated_at"]
    assert after["status"] == "down"
    health = json.loads((paths / "public" / "health.json").read_text())
    assert health["status"] == "down"
    assert health["last_update"] == good["updated_at"]


def test_failed_scrape_with_no_prior_cache_writes_nothing_but_health(paths):
    assert app_module._store_scrape_result([], None) is None
    assert not (paths / "cache" / "houses.json").exists()
    health = json.loads((paths / "public" / "health.json").read_text())
    assert health == {**health, "status": "down", "last_update": None}


def test_render_site_uses_cache_and_never_publishes_raw_data(paths):
    (paths / "VERSION").write_text("2.0.0")
    (paths / "public").mkdir()
    (paths / "public" / "data.json").write_text("{}")  # left over from an older build
    cached = {"updated_at": "2026-10-01 08:00", "status": "degraded", "trips": _trips("Rendered House")}

    app_module._render_site({"title": "Test Trip"}, cached, "2.0.0")

    html = (paths / "public" / "index.html").read_text()
    assert "Rendered House" in html
    health = json.loads((paths / "public" / "health.json").read_text())
    assert health["status"] == "degraded"
    assert health["last_update"] == "2026-10-01 08:00", "render must not make old data look fresh"
    assert not (paths / "public" / "data.json").exists()
