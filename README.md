# RVKompass

Werkzeug zur Unterstützung bei der Klassifikation bibliographischer Ressourcen mit der RVK (Regensburger Verbundklassifikation): Freitext-Suchbegriff eingeben, semantisch angereicherte Vorschläge für passende RVK-Systemstellen erhalten.

Privates Projekt von Günther Hochhauser.

## Lokales Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # Werte anpassen (CLAUDE_API_KEY, SYNC_TOKEN, SECRET_KEY)
```

RVK-Daten einmalig laden und Embeddings berechnen:

```bash
python scripts/fetch_marcxml_dump.py
python scripts/sync_rvk.py
python scripts/build_embeddings.py
```

App starten:

```bash
export FLASK_ENV=development
flask --app app:create_app run --debug
```

## Deployment (Netcup Webhosting 4000 VIE)

> **Veraltet** – Betrieb erfolgt künftig auf einem VPS mit Docker; dieser Abschnitt wird in P8 ersetzt.

Siehe Plan-Dokument. Kurzfassung:

1. Python-Version im Netcup-Control-Panel wählen, lokal identische Version für das venv nutzen.
2. `pip install -r requirements.txt` lokal in frischem venv (Ziel-Architektur: Linux x86_64 – ggf. Docker nutzen, um kompatible Wheels für `torch`/`sentence-transformers` zu erhalten).
3. `site-packages`, App-Code, Modellgewichte (`data/models/`) sowie fertige `data/rvk.sqlite3` und `data/embeddings.npy` per SFTP hochladen.
4. `passenger_wsgi.py` als Entry-Point, Secrets über Umgebungsvariablen/`.env` außerhalb des Webroots.
5. Netcup-Cronjob richtet periodischen Aufruf von `/internal/sync?token=...` ein.

## Lizenz

SPDX-License-Identifier: `EUPL-1.2`

Lizenziert unter der European Union Public Licence v. 1.2 (EUPL-1.2). Der Volltext liegt in
`LICENSE` (englische Fassung). Alle Sprachfassungen sind gleichermaßen gültig; deutsche Fassung:
<https://interoperable-europe.ec.europa.eu/sites/default/files/inline-files/EUPL%20v1_2%20DE.txt>
