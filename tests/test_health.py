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


def test_write_health_ok_sets_version_and_last_update(tmp_path, monkeypatch):
    version_file = tmp_path / "VERSION"
    version_file.write_text("1.5.0")
    monkeypatch.setattr(app_module, "HEALTH_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(app_module, "VERSION_FILE", str(version_file))

    app_module._write_health("ok")

    health = json.loads((tmp_path / "health.json").read_text())
    assert health == {
        "version": "1.5.0",
        "status": "ok",
        "last_update": health["last_update"],
        "extra": {},
    }
    assert health["last_update"] is not None


def test_write_health_down_preserves_previous_last_update(tmp_path, monkeypatch):
    health_file = tmp_path / "health.json"
    monkeypatch.setattr(app_module, "HEALTH_FILE", str(health_file))
    monkeypatch.setattr(app_module, "VERSION_FILE", str(tmp_path / "VERSION"))

    app_module._write_health("ok")
    first_last_update = json.loads(health_file.read_text())["last_update"]

    app_module._write_health("down")
    second = json.loads(health_file.read_text())

    assert second["status"] == "down"
    assert second["last_update"] == first_last_update, (
        "a failed run must not bump last_update -- it reflects the last "
        "successful scrape, not this process's own runtime"
    )


def test_write_health_down_with_no_prior_run_has_no_last_update(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "HEALTH_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(app_module, "VERSION_FILE", str(tmp_path / "VERSION"))

    app_module._write_health("down")

    health = json.loads((tmp_path / "health.json").read_text())
    assert health["status"] == "down"
    assert health["last_update"] is None


def test_write_health_degraded_bumps_last_update(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "HEALTH_FILE", str(tmp_path / "health.json"))
    monkeypatch.setattr(app_module, "VERSION_FILE", str(tmp_path / "VERSION"))

    app_module._write_health("degraded")

    health = json.loads((tmp_path / "health.json").read_text())
    assert health["status"] == "degraded"
    assert health["last_update"] is not None
