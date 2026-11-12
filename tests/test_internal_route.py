"""POST /internal/sync (P8): Token, Start im Hintergrund, Sperre."""

import sys

import pytest
from flask import Flask

from app import limiter
from app.routes import internal as internal_route
from app.services.rvk_sync import swap

TOKEN = "geheimes-test-token-1234567890"


class FakePopen:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        stdout = kwargs.get("stdout")
        if stdout is not None and hasattr(stdout, "write"):
            stdout.write(b"Abgleich gestartet\n")
        return object()


@pytest.fixture
def setup(tmp_path):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        RATELIMIT_ENABLED=False,
        SYNC_TOKEN=TOKEN,
        SYNC_LOCK_PATH=str(tmp_path / "sync.lock"),
        SYNC_LOG_PATH=str(tmp_path / "logs" / "sync.log"),
    )
    limiter.init_app(app)
    popen = FakePopen()
    app.extensions["sync_popen"] = popen
    app.register_blueprint(internal_route.bp)
    return app.test_client(), popen, tmp_path


def test_without_or_wrong_token_is_403(setup):
    client, popen, _ = setup
    assert client.post("/internal/sync").status_code == 403
    assert (
        client.post("/internal/sync", headers={"X-Sync-Token": "falsch"}).status_code
        == 403
    )
    assert popen.calls == []


def test_empty_configured_token_is_always_403(setup):
    client, popen, _ = setup
    client.application.config["SYNC_TOKEN"] = ""
    assert (
        client.post("/internal/sync", headers={"X-Sync-Token": ""}).status_code == 403
    )
    assert popen.calls == []


def test_header_token_starts_detached_process(setup):
    client, popen, tmp_path = setup
    response = client.post("/internal/sync", headers={"X-Sync-Token": TOKEN})
    assert response.status_code == 202
    assert response.get_json() == {"status": "started"}
    (args, kwargs) = popen.calls[0]
    assert args[0] == sys.executable
    assert args[1] == str(internal_route.SYNC_SCRIPT)
    assert internal_route.SYNC_SCRIPT.is_absolute()
    assert kwargs["start_new_session"] is True
    # Token nie in den Argumenten, nie im Log
    assert all(TOKEN not in str(a) for a in args)
    assert TOKEN not in repr(kwargs)
    log_text = (tmp_path / "logs" / "sync.log").read_text()
    assert "Abgleich gestartet" in log_text
    assert TOKEN not in log_text


def test_query_token_still_allowed(setup):
    client, popen, _ = setup
    assert client.post(f"/internal/sync?token={TOKEN}").status_code == 202
    assert len(popen.calls) == 1


def test_running_sync_gives_409(setup):
    client, popen, tmp_path = setup
    with swap.SyncLock(tmp_path / "sync.lock"):
        response = client.post("/internal/sync", headers={"X-Sync-Token": TOKEN})
    assert response.status_code == 409
    assert response.get_json() == {"status": "running"}
    assert popen.calls == []
