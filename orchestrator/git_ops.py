"""Git- und GitHub-Schritte des Steuerprogramms. Schützt main hart."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

PROTECTED_BRANCHES = ("main", "master")

# Bekannte Schlüsselformate – unabhängig von .env. Gemeldet wird nur die Art, nie der Wert.
_SECRET_PATTERNS = [
    ("Anthropic-Schlüssel", re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}")),
    (
        "GitHub-Token",
        re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"),
    ),
    ("AWS-Schlüssel", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("privater Schlüssel", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack-Token", re.compile(r"xox[abp]-[A-Za-z0-9-]{10,}")),
]

CO_AUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


class GitError(RuntimeError):
    pass


class SafetyError(RuntimeError):
    """Ein Schutz hat angeschlagen – nie automatisch umgehen, Mensch muss entscheiden."""


# ---------- reine Prüffunktionen ----------


def assert_safe_target(branch: str, base: str) -> None:
    if branch in PROTECTED_BRANCHES or base in PROTECTED_BRANCHES:
        raise SafetyError(
            f"Push/Merge mit main ist verboten (Branch {branch}, Ziel {base})."
        )


def forbidden_paths(paths: list[str]) -> list[str]:
    bad = []
    for p in paths:
        name = p.rsplit("/", 1)[-1]
        if (
            p == "data"
            or p.startswith("data/")
            or name.startswith(".env")
            and name != ".env.example"
            or p.startswith("orchestrator/")
            and (
                name in ("state.json", "PAUSE")
                or "/antworten/" in f"/{p}"
                or "/runs/" in f"/{p}"
            )
        ):
            bad.append(p)
    return bad


def scan_for_secrets(diff: str, secret_items: list[tuple[str, str]]) -> list[str]:
    """Sucht in den hinzugefügten Zeilen eines Diffs nach Geheimnissen.

    secret_items: (Name, Wert). Liefert Meldungen wie "SMTP_PASSWORD in app/config.py" –
    nie den Wert, auch keinen Teil davon."""
    added: dict[str, list[str]] = {}
    current = "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            current = path.removeprefix("b/")
        elif line.startswith("+"):
            added.setdefault(current, []).append(line[1:])
    hits: list[str] = []
    for path, lines in added.items():
        text = "\n".join(lines)
        for name, value in secret_items:
            if value and value in text:
                hits.append(f"{name} in {path}")
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{label} (Muster) in {path}")
    return sorted(set(hits))


# ---------- Ausführung ----------

Runner = Callable[..., subprocess.CompletedProcess]


class Git:
    def __init__(
        self, repo_root: Path, worktree: Path, base: str, run: Runner = subprocess.run
    ):
        self.repo_root = repo_root
        self.worktree = worktree
        self.base = base
        self._run = run

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True) -> str:
        return self._cmd(["git", *args], cwd or self.worktree, check)

    def _gh(self, *args: str, check: bool = True) -> str:
        return self._cmd(["gh", *args], self.worktree, check)

    def _cmd(self, cmd: list[str], cwd: Path, check: bool) -> str:
        proc = self._run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
        if check and proc.returncode != 0:
            raise GitError(
                f"{' '.join(cmd[:3])} …: {(proc.stderr or proc.stdout).strip()[-800:]}"
            )
        return (proc.stdout or "").strip()

    # --- Arbeitskopie ---

    def prepare_worktree(self) -> None:
        """Legt die Roboter-Arbeitskopie an (einmalig) und verlinkt data/."""
        self._git("fetch", "--prune", "origin", cwd=self.repo_root)
        if not (self.worktree / ".git").exists():
            self._git(
                "worktree",
                "add",
                "--detach",
                str(self.worktree),
                f"origin/{self.base}",
                cwd=self.repo_root,
            )
        data_link = self.worktree / "data"
        if not data_link.exists() and not data_link.is_symlink():
            data_link.symlink_to(self.repo_root / "data", target_is_directory=True)

    def start_branch(self, branch: str) -> None:
        """Phasen-Branch von origin/develop anlegen; existiert er schon (Neustart), weiterverwenden."""
        assert_safe_target(branch, self.base)
        self._git("fetch", "--prune", "origin")
        current = self._git("rev-parse", "--abbrev-ref", "HEAD")
        if current == branch:
            return
        exists = self._git(
            "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        )
        if exists:
            self._git("switch", branch)
        else:
            self._git("switch", "-c", branch, f"origin/{self.base}")

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD")

    def commits_ahead(self) -> int:
        out = self._git("rev-list", "--count", f"origin/{self.base}..HEAD")
        return int(out or 0)

    def diffstat(self) -> str:
        self._git("add", "-A")
        return self._git("diff", "--cached", "--stat", f"origin/{self.base}")

    # --- Ausliefern ---

    def commit_all(self, message: str, secret_items: list[tuple[str, str]]) -> bool:
        """Alles stagen, Schutzprüfungen, committen. False, wenn nichts zu committen war."""
        self._git("add", "-A")
        staged = [
            p for p in self._git("diff", "--cached", "--name-only").splitlines() if p
        ]
        if not staged:
            return False
        bad = forbidden_paths(staged)
        if bad:
            self._git("reset", "-q")
            raise SafetyError(f"Verbotene Dateien im Commit: {', '.join(bad[:10])}")
        hits = scan_for_secrets(self._git("diff", "--cached"), secret_items)
        if hits:
            self._git("reset", "-q")
            raise SafetyError(f"Möglicher Schlüssel im Commit: {'; '.join(hits)}")
        self._git("commit", "-q", "-m", message)
        return True

    def push(self, branch: str) -> None:
        assert_safe_target(branch, self.base)
        if self.current_branch() != branch:
            raise SafetyError(
                f"Arbeitskopie steht auf {self.current_branch()}, erwartet {branch}"
            )
        self._git("push", "-u", "origin", f"{branch}:{branch}")

    def pr_info(self, branch: str) -> dict | None:
        out = self._gh(
            "pr", "view", branch, "--json", "url,state,baseRefName,number", check=False
        )
        try:
            return json.loads(out) if out else None
        except json.JSONDecodeError:
            return None

    def open_or_get_pr(self, branch: str, title: str, body: str) -> dict:
        assert_safe_target(branch, self.base)
        info = self.pr_info(branch)
        if info is None or info.get("state") == "CLOSED":
            self._gh(
                "pr",
                "create",
                "--base",
                self.base,
                "--head",
                branch,
                "--title",
                title,
                "--body",
                f"{body}\n\n{PR_FOOTER}",
            )
            info = self.pr_info(branch)
        if info is None:
            raise GitError(f"Pull Request für {branch} nicht auffindbar")
        if info.get("baseRefName") != self.base:
            raise SafetyError(
                f"PR {info.get('url')} zielt auf {info.get('baseRefName')}, nicht {self.base}"
            )
        return info

    def merge_pr(self, branch: str) -> dict:
        info = self.pr_info(branch)
        if info is None:
            raise GitError(f"Kein Pull Request für {branch}")
        assert_safe_target(branch, info.get("baseRefName", ""))
        if info.get("baseRefName") != self.base:
            raise SafetyError(
                f"PR zielt auf {info.get('baseRefName')}, nicht {self.base}"
            )
        if info.get("state") != "MERGED":
            self._gh("pr", "merge", branch, "--merge")
            info = self.pr_info(branch) or info
        if info.get("state") != "MERGED":
            raise GitError(
                f"Merge von {info.get('url')} nicht bestätigt (Status {info.get('state')})"
            )
        return info
