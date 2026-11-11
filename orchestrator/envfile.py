"""Liest .env nur im Steuerprogramm. Werte werden nie ausgegeben, nur weitergereicht oder geschwärzt."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

# Schlüssel, die nur das Steuerprogramm braucht und die nie an App-Prozesse gehen
ORCHESTRATOR_ONLY_PREFIXES = ("SMTP_", "MAIL_", "STATUS_")

# Was als geheim gilt (für Commit-Prüfung und Schwärzung): Name enthält eines dieser Wörter …
SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD")
# … oder der Schlüssel steht hier ausdrücklich. Werte aus claude.env sind immer geheim.
SECRET_EXTRA_KEYS = ("SMTP_HOST", "MAIL_TO")
# Kürzere Werte werden im Commit nicht gesucht (zu viele Zufallstreffer).
MIN_SECRET_LEN = 12

MAIL_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "MAIL_FROM",
    "MAIL_TO",
)


def is_secret_name(key: str) -> bool:
    upper = key.upper()
    return key in SECRET_EXTRA_KEYS or any(part in upper for part in SECRET_NAME_PARTS)


class Secrets:
    """values: Inhalt von .env. extra: Inhalt weiterer Geheimnis-Dateien (z. B. claude.env),
    die nur geprüft und geschwärzt, aber nie an App-Prozesse weitergegeben werden."""

    def __init__(
        self,
        values: dict[str, str],
        extra: dict[str, str] | None = None,
        extra_label: str = "claude.env",
    ):
        self._values = {k: v for k, v in values.items() if v is not None}
        self._extra = {k: v for k, v in (extra or {}).items() if v is not None}
        self._extra_label = extra_label

    @classmethod
    def from_file(cls, path: Path, extra_path: Path | None = None) -> Secrets:
        def load(p: Path | None) -> dict:
            try:
                return dotenv_values(p) if p is not None and p.exists() else {}
            except OSError:
                return {}

        label = extra_path.name if extra_path is not None else "claude.env"
        return cls(load(path), load(extra_path), extra_label=label)

    def get(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def missing(self, keys: tuple[str, ...]) -> list[str]:
        return [k for k in keys if not self._values.get(k)]

    def app_env(self) -> dict[str, str]:
        """Variablen für Gate-Prozesse (z. B. Evaluation): alles außer Mail-/Status-Zugängen."""
        return {
            k: v
            for k, v in self._values.items()
            if not k.startswith(ORCHESTRATOR_ONLY_PREFIXES)
        }

    def _secret_pairs(self) -> list[tuple[str, str]]:
        """(Name, Wert) aller geheimen Einträge – Namen für Meldungen, Werte nur zum Vergleich."""
        pairs = [(k, v) for k, v in self._values.items() if is_secret_name(k)]
        pairs += [(f"{k} ({self._extra_label})", v) for k, v in self._extra.items()]
        return pairs

    def secret_items(self) -> list[tuple[str, str]]:
        """Geheime Werte ab MIN_SECRET_LEN Zeichen für die Commit-Prüfung."""
        return [(k, v) for k, v in self._secret_pairs() if len(v) >= MIN_SECRET_LEN]

    def redact(self, text: str) -> str:
        """Ersetzt geheime Werte (ab 6 Zeichen) im Text durch ***NAME***. Harmlose Werte wie
        Pfade oder FLASK_ENV bleiben stehen, damit Protokolle lesbar bleiben."""
        for key, value in sorted(self._secret_pairs(), key=lambda kv: -len(kv[1])):
            if len(value) >= 6 and value in text:
                text = text.replace(value, f"***{key}***")
        return text

    def __repr__(self) -> str:  # nie Werte zeigen
        return f"Secrets(keys={sorted(self._values)}, extra={sorted(self._extra)})"
