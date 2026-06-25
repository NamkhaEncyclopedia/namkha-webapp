"""The /test-mode/* routes are registered at import time only when NAMKHA_TEST_MODE=1.

Reloading app.main mutates global module state, so this stays in one isolated test
that restores the module afterward; it must not shape the rest of the suite.
"""

import importlib

import pytest
from starlette.testclient import TestClient

from app import main


@pytest.fixture
def test_mode_client(monkeypatch):
    monkeypatch.setenv("NAMKHA_TEST_MODE", "1")
    importlib.reload(main)  # re-registers the routes with the env set
    try:
        yield TestClient(main.app)
    finally:
        monkeypatch.delenv("NAMKHA_TEST_MODE", raising=False)
        importlib.reload(main)  # restore the app without the test-mode routes


def test_list_fixtures(test_mode_client):
    response = test_mode_client.get("/test-mode/fixtures")
    assert response.status_code == 200
    assert "year_classic_berlin" in response.json()


def test_get_fixture(test_mode_client):
    response = test_mode_client.get("/test-mode/fixtures/year_classic_berlin")
    assert response.status_code == 200
    assert response.json()["namkha_type"] == "YEAR"


def test_unknown_fixture_is_404(test_mode_client):
    assert test_mode_client.get("/test-mode/fixtures/does_not_exist").status_code == 404


def test_routes_absent_without_env_var(client):
    # The default app (env unset) never registered the route.
    assert client.get("/test-mode/fixtures").status_code == 404
