"""Startet Claude Code ohne Bildschirm (claude -p) und ordnet das Ergebnis ein."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Ergebnisarten
OK = "ok"
LIMIT = "limit"  # Nutzungslimit erreicht → stündlich erneut, keine Mail
TRANSIENT = "transient"  # kurze Störung (Überlast, Netz) → später erneut
AUTH = "auth"  # Anmeldung ungültig/abgelaufen → Mensch nötig
ERROR = "error"  # sonstiger Fehler → Mensch nötig

_LIMIT_RE = re.compile(
    r"usage limit|limit reached|hit your (?:\w+ )?limit|rate[ _-]?limit|out of (?:extra )?usage"
    r"|(?:session|weekly|daily|5-hour) limit|limit will reset|resets? (?:at|in) \d",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"oauth token (?:has )?expired|not logged in|please run /login|run `?claude (?:auth )?login"
    r"|invalid api key|authentication_error|invalid x-api-key|credentials? (?:expired|invalid)",
    re.IGNORECASE,
)
_TRANSIENT_RE = re.compile(
    r"overloaded|internal server error|bad gateway|service unavailable|gateway timeout"
    r"|ECONNRESET|ETIMEDOUT|ENOTFOUND|socket hang up|network error|connection error",
    re.IGNORECASE,
)
_NEEDS_HUMAN_RE = re.compile(r"^[\s>*_`-]*NEEDS_HUMAN:\s*(.+?)[\s*_`]*$", re.MULTILINE)


@dataclass
class ClaudeResult:
    kind: str
    text: str = ""
    structured: dict | None = None
    session_id: str | None = None
    cost_usd: float | None = None
    detail: str = ""
    auth_expires_at: str | None = None
    permission_denials: list = field(default_factory=list)

    @property
    def needs_human(self) -> str | None:
        return find_needs_human(self.text)


def find_needs_human(text: str) -> str | None:
    """Liefert die Frage(n) aus Zeilen 'NEEDS_HUMAN: <Frage>' oder None."""
    questions = [m.group(1).strip() for m in _NEEDS_HUMAN_RE.finditer(text or "")]
    return "\n".join(q for q in questions if q) or None


def _parse_result_json(stdout: str) -> dict | None:
    stdout = (stdout or "").strip()
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    for line in reversed(
        stdout.splitlines()
    ):  # Fallback: letzte JSON-Zeile vom Typ result
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and data.get("type") == "result":
                return data
    return None


def classify(returncode: int, stdout: str, stderr: str) -> ClaudeResult:
    """Reine Funktion: ordnet die Ausgabe eines claude-Laufs ein."""
    data = _parse_result_json(stdout)
    if data is None:
        blob = f"{stdout}\n{stderr}"
        detail = blob.strip()[-2000:] or f"Keine Ausgabe (Exit-Code {returncode})"
        return ClaudeResult(
            kind=_kind_from_text(blob, None, default=ERROR), detail=detail
        )

    text = data.get("result") or ""
    res = ClaudeResult(
        kind=OK,
        text=text if isinstance(text, str) else json.dumps(text, ensure_ascii=False),
        structured=data.get("structured_output"),
        session_id=data.get("session_id"),
        cost_usd=data.get("total_cost_usd"),
        auth_expires_at=data.get("credential_expires_at"),
        permission_denials=data.get("permission_denials") or [],
    )
    if data.get("is_error") or (data.get("subtype") not in (None, "success")):
        status = data.get("api_error_status")
        blob = f"{res.text}\n{stderr}\n{data.get('subtype', '')}"
        res.kind = _kind_from_text(blob, status, default=ERROR)
        res.detail = f"subtype={data.get('subtype')} api_error_status={status}: {res.text[:1500]}"
    elif returncode != 0:
        res.kind = _kind_from_text(
            f"{res.text}\n{stderr}", data.get("api_error_status"), ERROR
        )
        res.detail = f"Exit-Code {returncode}: {stderr.strip()[-1500:]}"
    return res


def _kind_from_text(blob: str, status, default: str) -> str:
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status == 429 or _LIMIT_RE.search(blob):
        return LIMIT
    if status in (401, 403) or _AUTH_RE.search(blob):
        return AUTH
    if status in (500, 502, 503, 504, 529) or _TRANSIENT_RE.search(blob):
        return TRANSIENT
    return default


@dataclass
class Invocation:
    """Ein geplanter claude-Aufruf (für Ausführung und für --dry-run-Anzeige)."""

    args: list[str]
    cwd: Path
    prompt: str
    log_stem: Path

    def shell_preview(self) -> str:
        def q(a: str) -> str:
            return (
                a
                if re.fullmatch(r"[\w./:=@,+-]+", a)
                else "'" + a.replace("'", "'\\''") + "'"
            )

        return f"(cd {self.cwd} && claude {' '.join(q(a) for a in self.args)} < prompt)"


def build_args(
    *,
    mode: str,
    model: str,
    allow: list[str],
    deny: list[str],
    settings_file: Path | None,
    json_schema: dict | None = None,
    tools: list[str] | None = None,
    persist: bool = True,
) -> list[str]:
    """Baut die Argumente für claude -p. mode: plan | dontAsk."""
    args = [
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--permission-mode",
        mode,
    ]
    # Nur Projekt-Einstellungen + unsere eigene Datei, keine persönlichen Freigaben
    args += ["--setting-sources", "project"]
    if settings_file is not None:
        args += ["--settings", str(settings_file)]
    if tools is not None:
        args += ["--tools", ",".join(tools)]
    if allow:
        args += ["--allowedTools", *allow]
    if deny:
        args += ["--disallowedTools", *deny]
    if json_schema is not None:
        args += ["--json-schema", json.dumps(json_schema)]
    if not persist:
        args += ["--no-session-persistence"]
    return args


class ClaudeRunner:
    def __init__(
        self, claude_bin: str, venv_bin: Path, timeout_s: int, redact=lambda s: s
    ):
        self.claude_bin = claude_bin
        self.venv_bin = venv_bin
        self.timeout_s = timeout_s
        self.redact = redact

    def _env(self) -> dict[str, str]:
        env = {
            k: v
            for k, v in os.environ.items()
            # API-Schlüssel nie durchreichen: Claude nutzt die Anmeldung des Kontos
            if k not in ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")
        }
        env["PATH"] = f"{self.venv_bin}:{env.get('PATH', '/usr/bin:/bin')}"
        env["VIRTUAL_ENV"] = str(self.venv_bin.parent)
        return env

    def run(self, inv: Invocation) -> ClaudeResult:
        inv.log_stem.parent.mkdir(parents=True, exist_ok=True)
        inv.log_stem.with_suffix(".prompt.md").write_text(inv.prompt, encoding="utf-8")
        try:
            proc = subprocess.run(
                [self.claude_bin, *inv.args],
                check=False,
                input=inv.prompt,
                cwd=inv.cwd,
                env=self._env(),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            return ClaudeResult(
                kind=ERROR, detail=f"Zeitlimit von {self.timeout_s} s überschritten"
            )
        except OSError as exc:
            return ClaudeResult(kind=ERROR, detail=f"claude nicht startbar: {exc}")
        inv.log_stem.with_suffix(".out.json").write_text(
            self.redact(proc.stdout), encoding="utf-8"
        )
        if proc.stderr.strip():
            inv.log_stem.with_suffix(".stderr.txt").write_text(
                self.redact(proc.stderr), encoding="utf-8"
            )
        res = classify(proc.returncode, proc.stdout, proc.stderr)
        res.text = self.redact(res.text)
        res.detail = self.redact(res.detail)
        return res
