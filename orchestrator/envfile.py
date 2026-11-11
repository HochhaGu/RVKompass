"""Liest .env nur im Steuerprogramm. Werte werden nie ausgegeben, nur weitergereicht oder geschwärzt."""

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

# Schlüssel, die nur das Steuerprogramm braucht und die nie an App-Prozesse gehen
ORCHESTRATOR_ONLY_PREFIXES = ("SMTP_", "MAIL_", "STATUS_")

MAIL_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "MAIL_FROM",
    "MAIL_TO",
)


class Secrets:
    def __init__(self, values: dict[str, str]):
        self._values = {k: v for k, v in values.items() if v is not None}

    @classmethod
    def from_file(cls, path: Path) -> Secrets:
        return cls(dotenv_values(path) if path.exists() else {})

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

    def redact(self, text: str) -> str:
        """Ersetzt jeden bekannten Geheimwert (ab 6 Zeichen) im Text durch ***."""
        for key, value in self._values.items():
            if len(value) >= 6 and value in text:
                text = text.replace(value, f"***{key}***")
        return text

    def secret_values(self) -> list[str]:
        return [v for v in self._values.values() if len(v) >= 8]

    def __repr__(self) -> str:  # nie Werte zeigen
        return f"Secrets(keys={sorted(self._values)})"
