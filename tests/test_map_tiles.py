"""CARTO basemap key wiring: the key must reach every map's tile URL, come
from the environment or the homelab env file (never the repo), and the page
must still build without one."""

import pytest

flask = pytest.importorskip("flask")

import app as app_module  # noqa: E402  pylint: disable=wrong-import-position


@pytest.fixture(name="no_env_file")
def fixture_no_env_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "HOMELAB_ENV", str(tmp_path / "missing.env"))
    monkeypatch.delenv("CARTO_API_KEY", raising=False)


def test_key_from_environment_wins(tmp_path, monkeypatch):
    env_file = tmp_path / "app.env"
    env_file.write_text("CARTO_API_KEY=from-file\n")
    monkeypatch.setattr(app_module, "HOMELAB_ENV", str(env_file))
    monkeypatch.setenv("CARTO_API_KEY", "from-env")
    assert app_module._carto_api_key() == "from-env"  # pylint: disable=protected-access


def test_key_from_homelab_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / "app.env"
    env_file.write_text("SITE_PASSWORD=x\n# comment\nCARTO_API_KEY=\"abc123\"\n")
    monkeypatch.setattr(app_module, "HOMELAB_ENV", str(env_file))
    monkeypatch.delenv("CARTO_API_KEY", raising=False)
    assert app_module._carto_api_key() == "abc123"  # pylint: disable=protected-access


@pytest.mark.usefixtures("no_env_file")
def test_no_key_gives_plain_tile_url():
    assert app_module._tile_url() == app_module.CARTO_TILE_URL  # pylint: disable=protected-access


def test_rendered_page_carries_key_in_tile_url(monkeypatch, no_env_file):  # pylint: disable=unused-argument
    monkeypatch.setenv("CARTO_API_KEY", "k3y")
    with app_module.app.app_context():
        html = app_module._render_html("T", [], "2026-10-05 12:00", "1.0.0")  # pylint: disable=protected-access
    assert '"https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?key=k3y"' in html
    # Every map goes through the one helper -- no hard-coded keyless tile layer left.
    assert html.count("basemaps.cartocdn.com") == 1
