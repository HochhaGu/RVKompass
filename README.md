# RVKompass

Werkzeug zur Unterstützung bei der Klassifikation bibliographischer Ressourcen mit der RVK (Regensburger Verbundklassifikation): Freitext-Suchbegriff eingeben, semantisch angereicherte Vorschläge für passende RVK-Systemstellen erhalten.

Privates Projekt von Günther Hochhauser.

## Lokales Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-dev.lock
cp .env.example .env   # Werte anpassen (CLAUDE_API_KEY, SYNC_TOKEN, SECRET_KEY)
```

`requirements.txt` nennt die direkten Abhängigkeiten. Die Sperrdateien `requirements.lock`
(Betrieb) und `requirements-dev.lock` (zusätzlich Tests und Lint) legen alle Pakete samt
Unterabhängigkeiten auf exakte Versionen fest; erzeugt werden sie mit `python scripts/build_lock.py`
aus der laufenden Umgebung. torch ist die CPU-Variante (daher der zusätzliche Paketindex).

RVK-Daten einmalig laden, Embeddings berechnen und den GND-Abzug aufbauen:

```bash
python scripts/fetch_marcxml_dump.py
python scripts/sync_rvk.py
python scripts/build_embeddings.py
python scripts/sync_gnd.py
```

App starten:

```bash
export FLASK_ENV=development
flask --app app:create_app run --debug
```

## Betrieb auf dem VPS mit Docker

Live läuft RVKompass als Docker-Container mit gunicorn unter `/srv/rvkompass`, getrennt von der
Werkstatt; davor steht Caddy mit HTTPS (`rvkompass.ghochhauser.at`). Im Container laufen per Cron
der monatliche, geprüfte RVK-Abgleich (`scripts/monthly_sync.py`, mit Backup und Info-Mail) und
die monatliche Aktualisierung des lokalen GND-Abzugs (`scripts/sync_gnd.py`). `GET /health` meldet
den Zustand.

Einrichtung, Live-Schaltung, Abgleich, Backups und die Routine „Update und Backup“:
[docs/deployment.md](docs/deployment.md).

## Lizenz

SPDX-License-Identifier: `EUPL-1.2`

Lizenziert unter der European Union Public Licence v. 1.2 (EUPL-1.2). Der Volltext liegt in
`LICENSE` (englische Fassung). Alle Sprachfassungen sind gleichermaßen gültig; deutsche Fassung:
<https://interoperable-europe.ec.europa.eu/sites/default/files/inline-files/EUPL%20v1_2%20DE.txt>
