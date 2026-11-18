"""Betriebsdateien (P8): Dockerfile, Compose, Cron, Caddy, Doku – nur lesend geprüft."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_dockerfile_uses_lock_and_gunicorn():
    dockerfile = read("Dockerfile")
    assert "-r requirements.lock" in dockerfile
    assert "download.pytorch.org/whl/cpu" in dockerfile  # torch==…+cpu
    assert "flask run" not in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/health" in dockerfile
    assert "useradd" in dockerfile
    entrypoint = read("docker/entrypoint.sh")
    assert "gunicorn -c /app/docker/gunicorn.conf.py" in entrypoint
    assert "setpriv --reuid=rvkompass" in entrypoint


def test_gunicorn_config_has_pidfile_and_no_access_log():
    conf = read("docker/gunicorn.conf.py")
    assert "pidfile" in conf and "accesslog = None" in conf
    assert "preload_app = False" in conf


def test_compose_restart_volumes_localhost():
    compose = read("docker-compose.yml")
    assert "restart: unless-stopped" in compose
    assert "/srv/rvkompass/data:/app/data" in compose
    assert "/srv/rvkompass/logs:/app/logs" in compose
    assert "/srv/rvkompass/.env:/app/.env:ro" in compose
    assert '"127.0.0.1:8000:8000"' in compose


def test_crontab_runs_both_scripts_as_app_user():
    lines = [
        line
        for line in read("docker/crontab").splitlines()
        if line.strip() and not line.startswith("#") and "=" not in line.split()[0]
    ]
    assert len(lines) == 2
    for line in lines:
        fields = line.split()
        assert fields[5] == "rvkompass"  # Nutzerfeld in /etc/cron.d
    assert any("scripts/monthly_sync.py" in line for line in lines)
    assert any("scripts/sync_gnd.py" in line for line in lines)
    assert read("docker/crontab").endswith("\n")


def test_caddy_current_file_unchanged_and_live_template():
    current = read("deploy/caddy/rvkompass.caddy")
    assert "503" in current and "/status/" in current
    assert "reverse_proxy" not in current.split("handle {")[-1]
    live = read("deploy/caddy/rvkompass.caddy.live")
    assert "reverse_proxy 127.0.0.1:8000" in live
    assert "/status/" in live and "rvkompass-status.auth" in live
    assert re.search(r"handle /internal/\* \{\s*respond 404", live)


def test_dockerignore_keeps_secrets_and_data_out():
    ignored = read(".dockerignore").splitlines()
    for entry in ("data/", "logs/", ".env", ".venv", "orchestrator/", ".claude/"):
        assert entry in ignored


def test_requirements_list_gunicorn():
    assert re.search(r"^gunicorn==\S+$", read("requirements.txt"), re.MULTILINE)


def test_lock_files_pin_exact_versions():
    runtime = read("requirements.lock")
    assert "FEHLT" not in runtime
    pins = [line for line in runtime.splitlines() if line and not line.startswith("#")]
    for line in pins:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[A-Za-z0-9_.+\-]+", line), line
    names = {line.split("==")[0].lower() for line in pins}
    assert "gunicorn" in names
    assert not any(name.startswith("nvidia") for name in names)  # nur CPU-torch
    dev = read("requirements-dev.lock")
    assert "-r requirements.lock" in dev and "FEHLT" not in dev


def test_deployment_doc_starts_with_overview():
    doc = read("docs/deployment.md")
    first_heading_after_title = next(
        line for line in doc.splitlines() if line.startswith("## ")
    )
    assert first_heading_after_title.startswith("## Übersicht")
    for needle in (
        "Update und Backup",
        "requirements-dev.lock",
        "pip check",
        "rvkompass.ghochhauser.at",
        "docker compose up -d --build",
    ):
        assert needle in doc, needle
    assert "<DOMAIN>" not in doc


def test_caddy_protects_statistics_page_like_status():
    live = read("deploy/caddy/rvkompass.caddy.live")
    match = re.search(r"handle /status/statistik\* \{(.*?)\n\t\}", live, re.DOTALL)
    assert match, "Block für /status/statistik fehlt"
    block = match.group(1)
    assert "import /etc/caddy/rvkompass-status.auth" in block
    assert "reverse_proxy 127.0.0.1:8000" in block
    assert 'Cache-Control "no-store"' in block
    # vor dem statischen Statusordner
    assert live.index("handle /status/statistik*") < live.index("handle_path /status/*")


def test_monthly_report_timer_and_service():
    timer = read("deploy/systemd/rvkompass-monthly-report.timer")
    assert "OnCalendar=*-*-01 07:00:00" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer
    service = read("deploy/systemd/rvkompass-monthly-report.service")
    assert "Type=oneshot" in service
    assert "WorkingDirectory=/srv/rvkompass/app" in service
    assert re.search(
        r"ExecStart=/usr/bin/docker compose exec -T --user rvkompass rvkompass "
        r"python /app/scripts/monthly_report\.py$",
        service,
        re.MULTILINE,
    )
    # Standard ist der Vormonat; kein Probelauf im Timer
    assert "--dry-run" not in service and "--month" not in service
