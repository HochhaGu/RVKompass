# RVKompass

Werkzeug zur Unterstützung bei der Klassifikation bibliographischer Ressourcen mit der RVK (Regensburger Verbundklassifikation): Freitext-Suchbegriff eingeben, semantisch angereicherte Vorschläge für passende RVK-Systemstellen erhalten.

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

Siehe Plan-Dokument. Kurzfassung:

1. Python-Version im Netcup-Control-Panel wählen, lokal identische Version für das venv nutzen.
2. `pip install -r requirements.txt` lokal in frischem venv (Ziel-Architektur: Linux x86_64 – ggf. Docker nutzen, um kompatible Wheels für `torch`/`sentence-transformers` zu erhalten).
3. `site-packages`, App-Code, Modellgewichte (`data/models/`) sowie fertige `data/rvk.sqlite3` und `data/embeddings.npy` per SFTP hochladen.
4. `passenger_wsgi.py` als Entry-Point, Secrets über Umgebungsvariablen/`.env` außerhalb des Webroots.
5. Netcup-Cronjob richtet periodischen Aufruf von `/internal/sync?token=...` ein.
