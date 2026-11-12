"""gunicorn-Einstellungen für den Container (P8).

Ohne preload_app: Jeder Worker lädt Datenbank, Modell und Embeddings selbst. Nach dem
monatlichen Tausch schickt der Abgleich SIGHUP an den Hauptprozess (pidfile), gunicorn startet
die Worker sanft neu, und sie laden den neuen Stand.

Speicher: Jeder Worker hält die Embeddings (Stand 12.11.2026: 1 203 MB + 63 MB) und das Modell im
Arbeitsspeicher. Zahl der Worker daher per Umgebung GUNICORN_WORKERS einstellbar.
"""

import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 120
graceful_timeout = 60
pidfile = os.environ.get("GUNICORN_PID_PATH", "/tmp/gunicorn.pid")
preload_app = False

# Datenschutz: kein Zugriffsprotokoll (keine IP-Adressen, keine Suchtexte in URLs)
accesslog = None
errorlog = "-"
loglevel = "info"
