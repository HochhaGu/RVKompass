"""Befehle des Steuerprogramms.

python -m orchestrator               Dauerbetrieb (so startet ihn der systemd-Dienst)
python -m orchestrator --dry-run     zeigt, was passieren würde (ändert nichts)
python -m orchestrator --only P0     genau eine Phase abarbeiten, dann stoppen
python -m orchestrator --resume      nach einer Pause weitermachen
python -m orchestrator --status      Kurzfassung anzeigen und Statusseite neu schreiben
python -m orchestrator --test-mail   Testmail senden
python -m orchestrator --send-digest Wochenbrief senden (systemd-Timer, montags)
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import shutil
import sys
from datetime import datetime

from .claude_runner import ClaudeRunner
from .config import load_settings
from .dryrun import DryGates, DryGit, DryMailer, DryRunner, DryStatus
from .engine import Engine, digest_key, pending_digest, resume
from .envfile import Secrets
from .gates import Gates
from .git_ops import Git
from .notify import Mailer, render_mail
from .outbox import Outbox
from .state import MemoryStore, StateStore, now_iso
from .status_page import STEP_LABELS, StatusWriter, local_time


def _logger(settings):
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logfile = settings.logs_dir / "orchestrator.log"

    def log(msg: str) -> None:
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        try:
            print(line, flush=True)
            with open(logfile, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:  # Protokollieren darf den Ablauf nie anhalten
            pass

    return log


@contextlib.contextmanager
def _run_lock(settings):
    settings.run_lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(settings.run_lock_path, "w")  # noqa: SIM115 – Sperre lebt so lange wie der Lauf
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        raise SystemExit(
            "Das Steuerprogramm läuft bereits (vermutlich als Dienst). "
            "Für --only bitte zuerst: sudo systemctl stop rvkompass-orchestrator"
        ) from None
    try:
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def build_engine(settings, *, dry_run: bool, log=print) -> Engine:
    secrets = Secrets.from_file(settings.repo_root / ".env")
    prompts_md = (settings.repo_root / "prompts" / "PROMPTS.md").read_text(
        encoding="utf-8"
    )
    ids = [p.id for p in settings.phases]
    store = StateStore(settings.state_path, ids[0], ids)
    gates = Gates(
        settings.venv_bin,
        settings.eval_command,
        settings.hit10_tolerance,
        secrets.app_env(),
        redact=secrets.redact,
    )
    if dry_run:

        def out(msg: str) -> None:
            print(msg, flush=True)

        return Engine(
            settings,
            MemoryStore(ids[0], ids, store.load()),
            DryRunner(out),
            DryGates(gates, out),
            DryGit(settings.worktree, settings.base_branch, out),
            DryMailer(out),
            DryStatus(),
            secrets,
            prompts_md=prompts_md,
            log=lambda m: out(f"→ {m}"),
            dry_run=True,
        )
    claude_bin = shutil.which("claude") or "claude"
    runner = ClaudeRunner(
        claude_bin, settings.venv_bin, settings.claude_timeout_s, redact=secrets.redact
    )
    git = Git(settings.repo_root, settings.worktree, settings.base_branch)
    return Engine(
        settings,
        store,
        runner,
        gates,
        git,
        Mailer(secrets),
        StatusWriter(settings, log=log),
        secrets,
        prompts_md=prompts_md,
        log=log,
    )


def cmd_status(settings) -> int:
    ids = [p.id for p in settings.phases]
    store = StateStore(settings.state_path, ids[0], ids)
    state = store.load()
    print(f"Zustand: {state['status']}")
    print(
        f"Phase:   {state['phase']} – Schritt: {STEP_LABELS.get(state['step'], state['step'])}"
    )
    if state.get("pause"):
        p = state["pause"]
        print(f"Pause:   {p['reason']} (seit {local_time(p.get('since'))})")
        if p.get("question"):
            print("Frage/Details:\n  " + p["question"].replace("\n", "\n  "))
    if state.get("wait_until"):
        print(f"Nächster Versuch: {local_time(state['wait_until'])}")
    print(
        "Phasen:  "
        + "  ".join(
            f"{pid}:{state['phases'][pid].get('status', 'offen')}" for pid in ids
        )
    )
    queue = Outbox(settings.outbox_dir).summary()
    if queue:
        print(f"Wartende Mails: {len(queue)} (neuer Versuch alle 10 Minuten)")
        for m in queue:
            print(
                f"  {m['subject']} – {m['attempts']} Versuch(e), zuletzt: {m['last_error']}"
            )
    print("Letzte Meldungen:")
    for m in state.get("messages", [])[-8:]:
        print(f"  {local_time(m['ts'])}  {m['text']}")
    path = StatusWriter(settings).write(state)
    print(f"Statusseite geschrieben: {path}")
    return 0


def cmd_test_mail(settings) -> int:
    secrets = Secrets.from_file(settings.repo_root / ".env")
    mailer = Mailer(secrets)
    missing = mailer.configured()
    if missing:
        print(f"In .env fehlen: {', '.join(missing)}")
        return 1
    ids = [p.id for p in settings.phases]
    state = StateStore(settings.state_path, ids[0], ids).load()
    mail = render_mail(
        "Testmail des Steuerprogramms",
        "Wenn du diese Mail liest, funktioniert der Mailversand. So sehen die Benachrichtigungen aus.",
        [
            {
                "heading": "Aktueller Stand",
                "rows": [
                    ("Zustand", state["status"]),
                    ("Phase", state["phase"]),
                    ("Schritt", STEP_LABELS.get(state["step"], state["step"])),
                    ("Gesendet", now_iso()),
                ],
            },
            {
                "heading": "Welche Mails kommen",
                "paragraphs": [
                    (
                        "Eingreifen nötig – sofort, wenn das Programm anhält (Rückfrage, rote Gates, "
                        "Haltepunkt nach P0 und P8)."
                    ),
                    "Alles fertig – sofort nach P8.",
                    "Wochenbrief – montags 08:00 mit den Phasen, die in der Woche fertig wurden.",
                ],
            },
        ],
        settings.status_url,
    )
    try:
        mailer.send(mail)
    except RuntimeError as exc:
        print(f"Versand fehlgeschlagen: {exc}")
        return 1
    print("Testmail gesendet.")
    return 0


def cmd_send_digest(settings, log) -> int:
    """Wochenbrief. Schreibt NICHT in state.json (der Dienst könnte gerade eine ältere Kopie
    halten und sie danach zurückschreiben), sondern merkt sich Gesendetes in einer eigenen Datei."""
    engine = build_engine(settings, dry_run=False, log=log)
    state = engine.store.load()
    sent_path = settings.logs_dir / "wochenbrief_gesendet.json"
    sent = (
        set(json.loads(sent_path.read_text(encoding="utf-8")))
        if sent_path.exists()
        else set()
    )
    entries = pending_digest(state.get("digest", []), sent)
    if not entries:
        log("Wochenbrief: keine neuen fertigen Phasen – keine Mail.")
        return 0
    mail = engine.digest_mail(entries)
    try:
        engine.mailer.send(mail)
        log(f"Wochenbrief gesendet ({len(entries)} Phase(n)).")
    except Exception as exc:  # noqa: BLE001 – Dienst holt den Versand nach
        err = engine.secrets.redact(f"{type(exc).__name__}: {exc}")
        engine.outbox.add(mail, err)
        log(
            f"Wochenbrief nicht gesendet ({err}) – liegt in der Warteschlange, der Dienst "
            "versucht es alle 10 Minuten erneut."
        )
    sent.update(digest_key(e) for e in entries)
    sent_path.parent.mkdir(parents=True, exist_ok=True)
    sent_path.write_text(json.dumps(sorted(sent), ensure_ascii=False), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m orchestrator",
        description="Steuerprogramm für die RVKompass-Phasen P0–P8",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="nur zeigen, was passieren würde"
    )
    ap.add_argument(
        "--only", metavar="PHASE", help="genau eine Phase abarbeiten (z. B. P0)"
    )
    ap.add_argument(
        "--resume", action="store_true", help="nach einer Pause weitermachen"
    )
    ap.add_argument(
        "--status", action="store_true", help="Stand anzeigen, Statusseite schreiben"
    )
    ap.add_argument("--test-mail", action="store_true", help="Testmail senden")
    ap.add_argument("--send-digest", action="store_true", help="Wochenbrief senden")
    args = ap.parse_args(argv)
    settings = load_settings()

    if args.status:
        return cmd_status(settings)
    if args.test_mail:
        return cmd_test_mail(settings)
    log = _logger(settings)
    if args.send_digest:
        return cmd_send_digest(settings, log)
    if args.resume:
        ids = [p.id for p in settings.phases]
        msg = resume(StateStore(settings.state_path, ids[0], ids), settings.pause_path)
        log(msg)
        return 0

    only = args.only.upper() if args.only else None
    if args.dry_run:
        print("PROBELAUF – es wird nichts ausgeführt, gespeichert oder gesendet.\n")
        engine = build_engine(settings, dry_run=True)
        try:
            result = engine.run(only=only, forever=False)
        except ValueError as exc:
            print(exc)
            return 1
        final = engine.store.load()
        print(
            f"\nEnde des Probelaufs. Zustand danach: {result}, Phase {final['phase']}."
        )
        if final.get("pause"):
            print(f"Grund: {final['pause']['reason']}")
        return 0

    with _run_lock(settings):
        engine = build_engine(settings, dry_run=False, log=log)
        try:
            result = engine.run(only=only, forever=only is None)
        except ValueError as exc:
            log(str(exc))
            return 1
        log(f"Beendet, Zustand: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
