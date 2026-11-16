"""Zustandsmaschine: arbeitet die Phasen Schritt für Schritt ab.

Schritte je Phase: branch → plan → review → (revise → review) → implement → gates
→ (repair → gates) → report → ship. Nach jedem Schritt: state.json speichern, Statusseite schreiben.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from string import Template

from . import claude_runner as cr
from .claude_runner import Invocation, build_args
from .config import ORCH_DIR, Phase, Settings, extract_phase_prompt
from .envfile import Secrets
from .git_ops import CO_AUTHOR, SafetyError
from .notify import Mail, render_mail
from .outbox import Outbox, mail_from
from .state import (
    BEREIT,
    FERTIG,
    LAEUFT,
    PAUSIERT,
    WARTET_LIMIT,
    WARTET_STOERUNG,
    add_message,
    now_iso,
)
from .status_page import STEP_LABELS

PROMPTS_DIR = ORCH_DIR / "prompts"
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "freigabe": {"type": "boolean"},
        "gruende": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["freigabe", "gruende"],
    "additionalProperties": False,
}
READ_TOOLS = ["Read", "Glob", "Grep"]
MIN_PLAN_CHARS = 200


class Pause(Exception):
    def __init__(
        self,
        reason: str,
        question: str | None = None,
        resume_step: str | None = None,
        notify: bool = True,
        kind: str = "eingreifen",
    ):
        super().__init__(reason)
        self.reason = reason
        self.question = question
        self.resume_step = resume_step
        self.notify = notify
        self.kind = kind  # eingreifen | haltepunkt


class Wait(Exception):
    def __init__(self, status: str, seconds: int, message: str):
        super().__init__(message)
        self.status = status
        self.seconds = seconds
        self.message = message


def _tpl(name: str, **values) -> str:
    return Template((PROMPTS_DIR / name).read_text(encoding="utf-8")).safe_substitute(
        **values
    )


def _fmt_metrics(m: dict | None) -> str:
    if not m:
        return "–"
    return ", ".join(f"{k} {v:.3f}" for k, v in m.items())


class Engine:
    def __init__(
        self,
        settings: Settings,
        store,
        runner,
        gates,
        git,
        mailer,
        status,
        secrets: Secrets,
        *,
        prompts_md: str,
        sleep=time.sleep,
        now=lambda: datetime.now(UTC),
        log=print,
        dry_run: bool = False,
        outbox: Outbox | None = None,
    ):
        self.s = settings
        self.store = store
        self.runner = runner
        self.gates = gates
        self.git = git
        self.mailer = mailer
        self.status = status
        self.secrets = secrets
        self.prompts_md = prompts_md
        self.sleep = sleep
        self.now = now
        self.log = log
        self.dry_run = dry_run
        self.outbox = outbox or Outbox(settings.outbox_dir, log=log)

    # ------------------------------------------------------------------ Hauptschleife

    def run(self, only: str | None = None, forever: bool = True) -> str:
        """Arbeitet, bis alles fertig/pausiert ist (forever=False) oder dauerhaft (Dienst)."""
        if only:
            self.prepare_only(only)
        while True:
            state = self.store.load()
            self._flush_outbox(state)
            st = state["status"]
            if st in (FERTIG, PAUSIERT):
                if not forever:
                    return st
                self.sleep(self.s.idle_poll_s)
                continue
            if st in (WARTET_LIMIT, WARTET_STOERUNG):
                remaining = self._seconds_until(state.get("wait_until"))
                if remaining > 0 and not self.dry_run:
                    self.sleep(min(remaining, self.s.idle_poll_s))
                    continue
                state["status"] = LAEUFT
                state["wait_until"] = None
                add_message(state, f"{state['phase']}: neuer Versuch nach Wartezeit")
                self._save(state)
            if st == BEREIT:
                state["status"] = LAEUFT
                add_message(
                    state, f"Steuerprogramm gestartet, weiter mit {state['phase']}"
                )
                self._save(state)
            if self.s.pause_path.exists() and not self.dry_run:
                self._pause(
                    state,
                    Pause(
                        "Angehalten über die Datei orchestrator/PAUSE",
                        resume_step=state["step"],
                        notify=False,
                    ),
                )
                continue
            if only and state["phase"] != only:
                state["status"] = BEREIT
                add_message(
                    state, f"--only {only}: Phase abgeschlossen, Steuerprogramm wartet"
                )
                self._save(state)
                return BEREIT
            self.step_once(state)

    def prepare_only(self, only: str) -> None:
        self.s.phase(only)  # prüft, ob es die Phase gibt
        with self.store.update() as state:
            if state["status"] == PAUSIERT:
                raise ValueError(
                    "Das Steuerprogramm ist pausiert – erst mit --resume freigeben."
                )
            if state["phases"].get(only, {}).get("status") == "fertig":
                raise ValueError(f"{only} ist bereits fertig.")
            if state["phase"] != only:
                cur = state["phase"]
                if (
                    state["step"] != "branch"
                    and state["phases"][cur].get("status") != "offen"
                ):
                    raise ValueError(
                        f"{cur} ist gerade in Arbeit (Schritt {state['step']}). Erst abschließen."
                    )
                state["phase"], state["step"] = only, "branch"
                state["counters"] = {"review": 0, "repair": 0, "transient": 0}
            if state["status"] == FERTIG:
                state["status"] = BEREIT

    def step_once(self, state: dict) -> None:
        phase = self.s.phase(state["phase"])
        step = state["step"]
        self.log(f"{phase.id}: {STEP_LABELS.get(step, step)}")
        try:
            nxt = getattr(self, f"_step_{step}")(state, phase)
        except Pause as p:
            self._pause(state, p)
            return
        except Wait as w:
            self._wait(state, w)
            return
        except SafetyError as exc:
            self._pause(
                state, Pause(f"Schutzprüfung hat angeschlagen: {exc}", resume_step=step)
            )
            return
        except Exception as exc:  # noqa: BLE001 – nie abstürzen, sondern Mensch rufen
            detail = self.secrets.redact(f"{type(exc).__name__}: {exc}")
            self._pause(
                state,
                Pause(
                    "Technischer Fehler im Steuerprogramm",
                    question=detail,
                    resume_step=step,
                ),
            )
            return
        if nxt:
            state["step"] = nxt
        self._save(state)

    # ------------------------------------------------------------------ Schritte

    def _step_branch(self, state: dict, phase: Phase) -> str:
        self.git.prepare_worktree()
        self.git.start_branch(phase.branch)
        info = state["phases"].setdefault(phase.id, {})
        info.update(status="läuft", branch=phase.branch, started_at=now_iso())
        state["counters"] = {"review": 0, "repair": 0, "transient": 0}
        add_message(state, f"{phase.id}: Branch {phase.branch} bereit")
        return "plan"

    def _step_plan(self, state: dict, phase: Phase, revision: str = "") -> str:
        prompt = _tpl(
            "plan.md",
            preamble=self._preamble(phase),
            revision=revision,
            phase_prompt=self._phase_prompt(phase),
        )
        args = build_args(
            mode="plan",
            model=self.s.model,
            allow=[*READ_TOOLS, "WebSearch", *self._web_rules(phase)],
            deny=list(self.s.deny),
            settings_file=None,
            tools=[*READ_TOOLS, "WebFetch", "WebSearch"],
        )
        res = self._claude(state, phase, "revise" if revision else "plan", args, prompt)
        text = res.text.strip()
        if len(text) < MIN_PLAN_CHARS:
            raise Pause(
                "Der Plan ist leer oder zu kurz",
                question=text[:1000] or "(leer)",
                resume_step="plan",
            )
        info = state["phases"][phase.id]
        info["plan_text"] = text
        info["plan_file"] = self._write_run(phase, "plan.md", text)
        add_message(
            state, f"{phase.id}: Plan {'überarbeitet' if revision else 'erstellt'}"
        )
        return "review"

    def _step_revise(self, state: dict, phase: Phase) -> str:
        info = state["phases"][phase.id]
        reasons = "\n".join(f"- {g}" for g in info.get("review_reasons", []))
        revision = (
            "\n\n## Überarbeitung\nDie Prüfinstanz hat den bisherigen Plan abgelehnt. Gründe:\n"
            f"{reasons}\n\nÜberarbeite den Plan so, dass alle Gründe ausgeräumt sind, und gib den "
            f"vollständigen neuen Plan aus.\n\n### Bisheriger Plan\n{info.get('plan_text', '')}\n"
        )
        return self._step_plan(state, phase, revision=revision)

    def _step_review(self, state: dict, phase: Phase) -> str:
        info = state["phases"][phase.id]
        claude_md = self._read_worktree("CLAUDE.md")
        prompt = _tpl(
            "review.md",
            claude_md=claude_md,
            phase_prompt=self._phase_prompt(phase),
            plan=info.get("plan_text", ""),
        )
        args = build_args(
            mode="dontAsk",
            model=self.s.review_model,
            allow=READ_TOOLS,
            deny=list(self.s.deny),
            settings_file=None,
            json_schema=REVIEW_SCHEMA,
            tools=READ_TOOLS,
            persist=False,
        )
        res = self._claude(
            state, phase, "review", args, prompt, check_needs_human=False
        )
        verdict = (
            res.structured
            if isinstance(res.structured, dict)
            else _json_from_text(res.text)
        )
        if not isinstance(verdict, dict) or not isinstance(
            verdict.get("freigabe"), bool
        ):
            raise Pause(
                "Prüfinstanz lieferte kein gültiges JSON",
                question=res.text[:1500],
                resume_step="review",
            )
        reasons = [str(g) for g in verdict.get("gruende", [])]
        self._write_run(
            phase,
            f"review-{state['counters']['review']}.json",
            json.dumps(verdict, ensure_ascii=False, indent=2),
        )
        if verdict["freigabe"]:
            info["review_notes"] = reasons
            add_message(state, f"{phase.id}: Plan freigegeben")
            return "implement"
        info["review_reasons"] = reasons
        if state["counters"]["review"] == 0:
            state["counters"]["review"] = 1
            add_message(
                state,
                f"{phase.id}: Plan abgelehnt ({len(reasons)} Gründe), wird überarbeitet",
            )
            return "revise"
        raise Pause(
            "Plan auch nach Überarbeitung nicht freigegeben",
            question="\n".join(f"- {g}" for g in reasons),
            resume_step="plan",
        )

    def _step_implement(self, state: dict, phase: Phase) -> str:
        info = state["phases"][phase.id]
        notes = "\n".join(f"- {g}" for g in info.get("review_notes", [])) or "- keine"
        prompt = (
            f"{self._preamble(phase)}\n\n---\n\n# Deine Aufgabe: den freigegebenen Plan umsetzen\n\n"
            f"Setze den Plan vollständig um. Hinweise der Prüfinstanz:\n{notes}\n\n"
            f"## Freigegebener Plan\n\n{info.get('plan_text', '')}\n\n---\n\n"
            f"# Phasen-Auftrag (zur Referenz)\n\n{self._phase_prompt(phase)}"
        )
        res = self._claude(state, phase, "implement", self._worker_args(phase), prompt)
        info["summary"] = res.text[:4000]
        add_message(state, f"{phase.id}: Umsetzung abgeschlossen")
        return "gates"

    def _step_gates(self, state: dict, phase: Phase) -> str:
        info = state["phases"][phase.id]
        baseline = state.get("baseline")
        report = self.gates.run(self.s.worktree, phase.evaluation, baseline)
        info["gates"] = report.summary_lines()
        self._write_run(
            phase,
            f"gates-{datetime.now(UTC):%Y%m%d-%H%M%S}.md",
            "\n".join(report.summary_lines()) + "\n\n" + report.failure_text(),
        )
        if report.ok:
            info["metrics"] = report.metrics
            info["metrics_before"] = (baseline or {}).get("metrics")
            info["eval_report"] = report.report_path
            add_message(
                state,
                f"{phase.id}: Gates grün"
                + (
                    f" – Hit@10 {report.metrics['hit@10']:.3f}"
                    if report.metrics and "hit@10" in report.metrics
                    else ""
                ),
            )
            return "report"
        info["failure"] = report.failure_text()[:8000]
        if state["counters"]["repair"] == 0:
            state["counters"]["repair"] = 1
            add_message(state, f"{phase.id}: Gates rot – ein Reparaturversuch")
            return "repair"
        raise Pause(
            "Gates auch nach dem Reparaturversuch rot",
            question="\n".join(report.summary_lines())
            + "\n\n"
            + info["failure"][:3000],
            resume_step="gates",
        )

    def _step_repair(self, state: dict, phase: Phase) -> str:
        info = state["phases"][phase.id]
        prompt = (
            f"{self._preamble(phase)}\n\n---\n\n# Deine Aufgabe: Gates reparieren\n\n"
            "Die Umsetzung des Plans ist erfolgt, aber mindestens ein Gate ist rot. Behebe die "
            "Ursache (nicht die Prüfung abschwächen, keine Tests löschen oder überspringen). "
            "Führe die Gates danach selbst aus.\n\n"
            f"## Ausgabe der roten Gates\n\n```\n{info.get('failure', '')}\n```\n\n"
            f"## Freigegebener Plan\n\n{info.get('plan_text', '')}\n"
        )
        res = self._claude(state, phase, "repair", self._worker_args(phase), prompt)
        info["summary"] = (info.get("summary", "") + "\n\nReparatur:\n" + res.text)[
            :6000
        ]
        add_message(state, f"{phase.id}: Reparaturversuch abgeschlossen")
        return "gates"

    def _step_report(self, state: dict, phase: Phase) -> str:
        info = state["phases"][phase.id]
        diffstat = self.git.diffstat()
        prompt = _tpl(
            "report.md",
            phase_id=phase.id,
            phase_title=phase.title,
            phase_number=phase.number,
            gates="\n".join(f"- {g}" for g in info.get("gates", [])),
            metrics_before=_fmt_metrics(info.get("metrics_before")),
            metrics_after=_fmt_metrics(info.get("metrics")),
            base=self.s.base_branch,
            diffstat=diffstat,
            plan=info.get("plan_text", "")[:20000],
            summary=info.get("summary", ""),
        )
        rel = f"docs/phasen/phase-{phase.number}.md"
        args = build_args(
            mode="dontAsk",
            model=self.s.report_model,
            allow=[*READ_TOOLS, f"Write(./{rel})", f"Edit(./{rel})"],
            deny=list(self.s.deny),
            settings_file=None,
            tools=[*READ_TOOLS, "Write", "Edit"],
        )
        self._claude(state, phase, "report", args, prompt, check_needs_human=False)
        path = self.s.worktree / rel
        if not self.dry_run and not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                self._fallback_report(phase, info, diffstat), encoding="utf-8"
            )
            add_message(state, f"{phase.id}: Bericht vom Steuerprogramm selbst erzeugt")
        info["report_file"] = rel
        return "ship"

    def _step_ship(self, state: dict, phase: Phase) -> None:
        info = state["phases"][phase.id]
        title = f"Phase {phase.number}: {phase.title}"
        gates = "\n".join(f"- {g}" for g in info.get("gates", []))
        msg = f"{title}\n\nVom Steuerprogramm umgesetzt und geprüft.\n\nGates:\n{gates}\n\n{CO_AUTHOR}"
        self.git.commit_all(msg, self.secrets.secret_items())
        if self.git.commits_ahead() == 0:
            raise Pause(
                "Die Phase hat keine Änderungen erzeugt", resume_step="implement"
            )
        self.git.push(phase.branch)
        body = self._read_worktree(info.get("report_file", "")) or title
        pr = self.git.open_or_get_pr(phase.branch, title, body[:60000])
        merged = self.git.merge_pr(phase.branch)
        info.update(
            status="fertig",
            finished_at=now_iso(),
            pr_url=merged.get("url", pr.get("url")),
        )
        if phase.evaluation and info.get("metrics"):
            state["baseline"] = {
                "metrics": info["metrics"],
                "report": info.get("eval_report"),
                "phase": phase.id,
            }
        state["digest"].append(
            {
                "phase": phase.id,
                "title": phase.title,
                "finished_at": info["finished_at"],
                "pr_url": info["pr_url"],
                "metrics": info.get("metrics"),
                "metrics_before": info.get("metrics_before"),
                "report": info.get("report_file"),
            }
        )
        state["counters"] = {"review": 0, "repair": 0, "transient": 0}
        add_message(
            state, f"{phase.id}: fertig, Pull Request nach {self.s.base_branch} gemergt"
        )

        nxt = self.s.next_phase(phase.id)
        if nxt is None:
            state["status"] = FERTIG
            state["step"] = "done"
            self._notify(state, lambda: self._mail_all_done(state, phase))
            add_message(state, "Alle Phasen fertig")
            return
        state["phase"], state["step"] = nxt.id, "branch"
        if phase.halt_message:
            raise Pause(
                f"Haltepunkt nach {phase.id}",
                question=phase.halt_message,
                resume_step="branch",
                kind="haltepunkt",
            )
        return

    # ------------------------------------------------------------------ Claude-Aufruf

    def _claude(
        self,
        state: dict,
        phase: Phase,
        step: str,
        args: list[str],
        prompt: str,
        check_needs_human: bool = True,
    ) -> cr.ClaudeResult:
        stem = self.s.runs_dir / phase.id / f"{step}-{datetime.now(UTC):%Y%m%d-%H%M%S}"
        res = self.runner.run(
            Invocation(args=args, cwd=self.s.worktree, prompt=prompt, log_stem=stem)
        )
        self._check_auth_expiry(state, res.auth_expires_at)
        if res.kind == cr.LIMIT:
            raise Wait(
                WARTET_LIMIT,
                self.s.limit_wait_s,
                f"{phase.id}: Claude-Nutzungslimit erreicht – neuer Versuch in "
                f"{self.s.limit_wait_s // 60} min",
            )
        if res.kind == cr.TRANSIENT:
            state["counters"]["transient"] = state["counters"].get("transient", 0) + 1
            if state["counters"]["transient"] > self.s.transient_max_attempts:
                raise Pause(
                    "Claude ist dauerhaft gestört",
                    question=res.detail,
                    resume_step=step,
                )
            raise Wait(
                WARTET_STOERUNG,
                self.s.transient_wait_s,
                f"{phase.id}: kurze Störung bei Claude – neuer Versuch in "
                f"{self.s.transient_wait_s // 60} min",
            )
        if res.kind == cr.AUTH:
            raise Pause(
                "Claude-Anmeldung ungültig oder abgelaufen",
                question="Bitte auf dem Server als guenther `claude setup-token` ausführen "
                "(langlebige Anmeldung) oder `claude` starten und /login wählen. Danach --resume.\n\n"
                + res.detail,
                resume_step=step,
            )
        if res.kind == cr.ERROR:
            raise Pause(
                "Claude-Lauf fehlgeschlagen", question=res.detail, resume_step=step
            )
        state["counters"]["transient"] = 0
        if check_needs_human and res.needs_human:
            raise Pause(
                f"Rückfrage von Claude in {phase.id}",
                question=res.needs_human,
                resume_step=step,
            )
        return res

    def _worker_args(self, phase: Phase) -> list[str]:
        return build_args(
            mode="dontAsk",
            model=self.s.model,
            allow=[*self.s.allow, *phase.extra_bash, *self._web_rules(phase)],
            deny=list(self.s.deny),
            settings_file=None,
        )

    def _web_rules(self, phase: Phase) -> list[str]:
        return [f"WebFetch(domain:{d})" for d in phase.web_domains]

    def _preamble(self, phase: Phase) -> str:
        return _tpl(
            "worker_preamble.md",
            phase_id=phase.id,
            phase_title=phase.title,
            branch=phase.branch,
            eval_command=self.s.eval_command,
            tolerance=f"{self.s.hit10_tolerance:.2f}",
            answers=self._answers(phase),
        )

    def _phase_prompt(self, phase: Phase) -> str:
        return extract_phase_prompt(self.prompts_md, phase.id)

    def _answers(self, phase: Phase) -> str:
        path = self.s.answers_dir / f"{phase.id}.md"
        if not path.exists():
            return ""
        return (
            "\n## Antworten von Günther auf frühere Rückfragen (verbindlich)\n\n"
            + path.read_text(encoding="utf-8").strip()
            + "\n"
        )

    # ------------------------------------------------------------------ Zustand, Pause, Mails

    def _save(self, state: dict) -> None:
        self.store.save(state)
        try:
            self.status.write(state)
        except OSError as exc:
            self.log(f"Statusseite nicht geschrieben: {exc}")

    def _pause(self, state: dict, p: Pause) -> None:
        state["status"] = PAUSIERT
        state["pause"] = {
            "reason": p.reason,
            "question": p.question,
            "kind": p.kind,
            "resume_step": p.resume_step or state["step"],
            "since": now_iso(),
        }
        info = state["phases"].get(state["phase"], {})
        if info.get("status") == "läuft":
            info["status"] = "pausiert"
        add_message(state, f"{state['phase']}: angehalten – {p.reason}")
        self.log(f"PAUSE: {p.reason}")
        if p.notify:
            self._notify(state, lambda: self._mail_pause(state, p))
        self._save(state)

    def _wait(self, state: dict, w: Wait) -> None:
        state["status"] = w.status
        state["wait_until"] = (self.now() + timedelta(seconds=w.seconds)).isoformat(
            timespec="seconds"
        )
        add_message(state, w.message)
        self.log(w.message)
        self._save(state)

    def _seconds_until(self, iso: str | None) -> float:
        if not iso:
            return 0
        return (datetime.fromisoformat(iso) - self.now()).total_seconds()

    def _notify(self, state: dict, build: Callable[[], Mail]) -> None:
        """Mail bauen und senden. Darf den Ablauf NIE anhalten: jeder Fehler wird protokolliert,
        die Mail kommt in die Warteschlange und wird später erneut versucht."""
        try:
            mail = build()
        except Exception as exc:  # noqa: BLE001
            err = self.secrets.redact(f"{type(exc).__name__}: {exc}")
            self.log(f"Mail konnte nicht erstellt werden: {err}")
            add_message(state, f"Mail konnte nicht erstellt werden: {err}")
            return
        if self.dry_run:  # Probelauf: nie senden, nichts in die Warteschlange
            self.log(f"würde senden: {mail.subject}")
            add_message(state, f"würde senden: {mail.subject}")
            return
        try:
            self.mailer.send(mail)
        except Exception as exc:  # noqa: BLE001
            err = self.secrets.redact(f"{type(exc).__name__}: {exc}")
            self.log(
                f"Mail nicht gesendet ({mail.subject}): {err} – kommt in die Warteschlange"
            )
            add_message(
                state, f"Mail nicht gesendet, wird wiederholt: {mail.subject} ({err})"
            )
            self.outbox.add(mail, err, now=self.now())
            return
        self.log(f"Mail gesendet: {mail.subject}")
        add_message(state, f"Mail gesendet: {mail.subject}")

    def _flush_outbox(self, state: dict) -> None:
        """Fällige Mails aus der Warteschlange erneut senden. Bricht nach dem ersten Fehlschlag ab
        (der Server ist dann vermutlich insgesamt nicht erreichbar). Hält den Ablauf nie an."""
        if self.dry_run:
            return
        changed = False
        try:
            for legacy in (
                state.pop("outbox", []) or []
            ):  # alte Ablage in state.json übernehmen
                self.outbox.add(
                    mail_from(legacy), "aus state.json übernommen", now=self.now()
                )
                changed = True
            state.pop("outbox_last_try", None)
            for path, data in self.outbox.due(now=self.now()):
                try:
                    self.mailer.send(mail_from(data))
                except Exception as exc:  # noqa: BLE001
                    err = self.secrets.redact(f"{type(exc).__name__}: {exc}")
                    self.outbox.mark_failed(path, data, err, now=self.now())
                    self.log(
                        f"Wartende Mail erneut nicht gesendet ({data.get('subject')}): {err}"
                    )
                    changed = True
                    break
                self.outbox.remove(path)
                self.log(f"Wartende Mail gesendet: {data.get('subject')}")
                add_message(state, f"Wartende Mail gesendet: {data.get('subject')}")
                changed = True
        except Exception as exc:  # noqa: BLE001
            self.log(
                f"Fehler in der Mail-Warteschlange: {self.secrets.redact(str(exc))}"
            )
        if changed:
            self._save(state)

    def _check_auth_expiry(self, state: dict, expires: str | None) -> None:
        if not expires:
            return
        state["auth_expires_at"] = expires
        try:
            left = datetime.fromisoformat(expires) - self.now()
        except ValueError:
            return
        if (
            left < timedelta(days=self.s.auth_warn_days)
            and state.get("auth_warned_for") != expires
        ):
            state["auth_warned_for"] = expires
            self._notify(
                state,
                lambda: render_mail(
                    "Claude-Anmeldung läuft bald ab",
                    f"Die Anmeldung von Claude Code auf dem Server läuft in {max(left.days, 0)} Tag(en) "
                    "ab. Danach hält das Steuerprogramm an.",
                    [
                        {
                            "heading": "So erneuern",
                            "paragraphs": [
                                (
                                    "Per SSH als guenther anmelden und `claude setup-token` ausführen (erzeugt eine "
                                    "langlebige Anmeldung für den Betrieb ohne Bildschirm) – oder `claude` starten "
                                    "und /login wählen."
                                ),
                                "Das Steuerprogramm läuft bis dahin normal weiter.",
                            ],
                        }
                    ],
                    self.s.status_url,
                ),
            )

    # ------------------------------------------------------------------ Mail-Inhalte

    def _mail_pause(self, state: dict, p: Pause) -> Mail:
        pid = state["phase"]
        if p.kind == "haltepunkt":
            return render_mail(
                p.reason,
                p.question or "",
                [
                    {
                        "heading": "So geht es weiter",
                        "paragraphs": [
                            (
                                "Wenn du so weit bist: per SSH ins Repo ~/dev/rvkompass wechseln und "
                                "`.venv/bin/python -m orchestrator --resume` ausführen. Das Steuerprogramm "
                                f"macht dann mit {pid} weiter."
                            )
                        ],
                    },
                    self._phase_rows_section(state),
                ],
                self.s.status_url,
            )
        rows = [
            ("Phase", pid),
            ("Schritt", STEP_LABELS.get(state["step"], state["step"])),
            ("Grund", p.reason),
            ("Zeit", now_iso()),
        ]
        sections = [{"heading": "Was ist passiert", "rows": rows}]
        if p.question:
            sections.append({"heading": "Frage bzw. Details", "pre": p.question[:6000]})
        sections.append(
            {
                "heading": "So geht es weiter",
                "paragraphs": [
                    "1. Per SSH auf den Server, ins Repo ~/dev/rvkompass wechseln.",
                    (
                        f"2. Falls eine Frage gestellt wurde: Antwort in orchestrator/antworten/{pid}.md "
                        "schreiben (einfacher Text genügt). Die Antwort gilt für alle weiteren Läufe dieser Phase."
                    ),
                    (
                        "3. `.venv/bin/python -m orchestrator --resume` ausführen. Der Dienst macht innerhalb "
                        "einer Minute weiter."
                    ),
                    "Protokolle der Claude-Läufe liegen unter orchestrator/runs/"
                    + pid
                    + "/.",
                ],
            }
        )
        return render_mail(
            f"Eingreifen nötig: {pid} – {p.reason}",
            "Das Steuerprogramm hat angehalten und wartet auf dich.",
            sections,
            self.s.status_url,
        )

    def _phase_rows_section(self, state: dict) -> dict:
        rows = []
        for ph in self.s.phases:
            info = state["phases"].get(ph.id, {})
            m = info.get("metrics") or {}
            hit = f" · Hit@10 {m['hit@10']:.3f}" if "hit@10" in m else ""
            rows.append((f"{ph.id} {ph.title}", f"{info.get('status', 'offen')}{hit}"))
        return {"heading": "Stand aller Phasen", "rows": rows}

    def _mail_all_done(self, state: dict, last: Phase) -> Mail:
        sections = [self._phase_rows_section(state)]
        if last.halt_message:
            sections.insert(
                0, {"heading": "Live-Schaltung", "paragraphs": [last.halt_message]}
            )
        return render_mail(
            f"Alles fertig – P0 bis {last.id} abgeschlossen",
            "Das Steuerprogramm hat alle Phasen umgesetzt, geprüft und nach "
            f"{self.s.base_branch} gemergt. main wurde nicht verändert.",
            sections,
            self.s.status_url,
        )

    def digest_mail(self, entries: list[dict]) -> Mail:
        sections = []
        for e in entries:
            rows = [
                ("Fertig seit", e.get("finished_at", "–")),
                ("Pull Request", e.get("pr_url") or "–"),
                ("Kennzahlen vorher", _fmt_metrics(e.get("metrics_before"))),
                ("Kennzahlen nachher", _fmt_metrics(e.get("metrics"))),
                ("Bericht", e.get("report") or "–"),
            ]
            sections.append(
                {"heading": f"{e['phase']} – {e.get('title', '')}", "rows": rows}
            )
        return render_mail(
            f"Wochenbrief: {len(entries)} Phase(n) fertig",
            "Diese Phasen wurden seit dem letzten Wochenbrief abgeschlossen.",
            sections,
            self.s.status_url,
        )

    # ------------------------------------------------------------------ Hilfen

    def _write_run(self, phase: Phase, name: str, content: str) -> str:
        path = self.s.runs_dir / phase.id / name
        if not self.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return str(path)

    def _read_worktree(self, rel: str) -> str:
        if not rel:
            return ""
        path = self.s.worktree / rel
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            if rel == "CLAUDE.md":  # Fallback: CLAUDE.md des Haupt-Repos
                return (self.s.repo_root / "CLAUDE.md").read_text(encoding="utf-8")
            return ""

    def _fallback_report(self, phase: Phase, info: dict, diffstat: str) -> str:
        gates = "\n".join(f"- {g}" for g in info.get("gates", []))
        return (
            f"# Phase {phase.id} – {phase.title}\n\n## Übersicht\n1. Worum es ging\n"
            "2. Kennzahlen vorher/nachher\n3. Geänderte Dateien\n4. Zusammenfassung\n\n"
            f"## Worum es ging\nSiehe Phasen-Auftrag {phase.id} in prompts/PROMPTS.md.\n\n"
            f"## Kennzahlen vorher/nachher\n- Vorher: {_fmt_metrics(info.get('metrics_before'))}\n"
            f"- Nachher: {_fmt_metrics(info.get('metrics'))}\n\nGates:\n{gates}\n\n"
            f"## Geänderte Dateien\n```\n{diffstat}\n```\n\n"
            f"## Zusammenfassung\n{info.get('summary', '–')}\n"
        )


def _json_from_text(text: str) -> dict | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def digest_key(entry: dict) -> str:
    return f"{entry.get('phase')}@{entry.get('finished_at')}"


def pending_digest(entries: list[dict], sent: set[str]) -> list[dict]:
    """Fertige Phasen, die noch in keinem Wochenbrief standen."""
    return [e for e in entries if digest_key(e) not in sent]


def resume(store, pause_path: Path) -> str:
    """--resume: Pause aufheben, Zähler passend zurücksetzen. Liefert eine Meldung."""
    with store.update() as state:
        if pause_path.exists():
            pause_path.unlink()
        st = state["status"]
        if st == PAUSIERT:
            p = state.get("pause") or {}
            step = p.get("resume_step") or state["step"]
            state["step"] = step
            if step == "gates":
                state["counters"]["repair"] = 0
            if step in ("plan", "revise", "review"):
                state["counters"]["review"] = 0
            state["counters"]["transient"] = 0
            info = state["phases"].get(state["phase"], {})
            if info.get("status") == "pausiert":
                info["status"] = "läuft"
            state["pause"] = None
            state["status"] = LAEUFT
            add_message(
                state, f"Fortgesetzt durch --resume: {state['phase']}, Schritt {step}"
            )
            return (
                f"Fortgesetzt: {state['phase']}, Schritt {STEP_LABELS.get(step, step)}."
            )
        if st in (WARTET_LIMIT, WARTET_STOERUNG):
            state["wait_until"] = None
            add_message(state, "Wartezeit durch --resume verkürzt")
            return "Wartezeit aufgehoben – nächster Versuch sofort."
        if st == FERTIG:
            return "Alle Phasen sind fertig – nichts fortzusetzen."
        return f"Nichts zu tun (Zustand: {st})."
