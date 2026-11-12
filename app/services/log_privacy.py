"""Datenschutz in Logs: keine Suchbegriffe, keine IP-Adressen.

- `error_kind` beschreibt einen Fehler nur über Fehlerart und ggf. HTTP-Status. Die Fehlermeldung
  selbst wird nie geloggt, weil sie Suchbegriffe enthalten kann (z. B. lobid-URL mit `?q=…`).
- `RedactingFilter` entfernt als zweite Sicherung IP-Adressen und Query-Strings aus jeder
  Logzeile, auch aus Tracebacks.
- `install` hängt den Filter an die Handler der App und drosselt den Logger von flask-limiter,
  dessen Meldung „ratelimit … exceeded“ die Client-Adresse enthält.
"""

import logging
import re

IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Mindestens zwei Doppelpunkte mit Hex-Gruppen; Uhrzeiten wie 12:30:45 sind rein dezimal und
# haben höchstens zwei Ziffern je Gruppe – sie werden über die Hex-Prüfung unten ausgenommen.
IPV6 = re.compile(
    r"(?<![\w:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{1,4}(?![\w]|:[0-9A-Fa-f])"
)
QUERY_STRING = re.compile(r"\?[^\s'\"<>)]*")

IP_PLACEHOLDER = "<IP>"
QUERY_PLACEHOLDER = "?<…>"

LIMITER_LOGGER = "flask-limiter"


def _looks_like_ipv6(match: re.Match) -> bool:
    text = match.group(0)
    groups = [g for g in text.split(":") if g]
    if "::" in text:
        return True
    # Volle Schreibweise: mindestens eine Gruppe mit Buchstaben oder mehr als zwei Ziffern
    return len(groups) >= 3 and any(len(g) > 2 or not g.isdigit() for g in groups)


def redact(text: str) -> str:
    """Ersetzt IP-Adressen und Query-Strings durch Platzhalter."""
    if not text:
        return text
    text = QUERY_STRING.sub(QUERY_PLACEHOLDER, text)
    text = IPV4.sub(IP_PLACEHOLDER, text)
    return IPV6.sub(
        lambda m: IP_PLACEHOLDER if _looks_like_ipv6(m) else m.group(0), text
    )


def error_kind(exc: BaseException) -> str:
    """Fehlerart und ggf. HTTP-Status, z. B. „HTTPError (HTTP 503)“ – nie die Meldung."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    name = type(exc).__name__
    return f"{name} (HTTP {status})" if isinstance(status, int) else name


class RedactingFilter(logging.Filter):
    """Schreibt Nachricht und Traceback eines Logeintrags bereinigt zurück."""

    _formatter = logging.Formatter()

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 – kaputte Formatierung nicht weiterreichen
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = self._formatter.formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def add_filter(target: logging.Logger | logging.Handler) -> None:
    """Hängt genau einen RedactingFilter an Logger oder Handler."""
    if not any(isinstance(f, RedactingFilter) for f in target.filters):
        target.addFilter(RedactingFilter())


def quiet_limiter_logger() -> None:
    """flask-limiter meldet Überschreitungen auf INFO mit Client-Adresse – erst ab WARNING."""
    logging.getLogger(LIMITER_LOGGER).setLevel(logging.WARNING)


def install(logger: logging.Logger) -> None:
    """Filter an alle Handler des Loggers hängen und flask-limiter drosseln."""
    for handler in logger.handlers:
        add_filter(handler)
    quiet_limiter_logger()
