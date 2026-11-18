# RVKompass – Betrieb auf dem VPS (Deployment)

Stand: 12.11.2026 (Phase P8). Domain: `rvkompass.ghochhauser.at`.

## Übersicht
1. Überblick: Werkstatt und Live-Betrieb
2. Voraussetzungen
3. Erstinstallation (ohne Live-Schaltung)
4. Live-Schaltung – erst nach Günthers Freigabe
5. Monatlicher RVK-Abgleich
6. GND-Abzug
7. `/health` und Nutzungsstatistik
8. Routine „Update und Backup“
9. Abgleich der Werkstatt-Umgebung (`.venv`) mit der Sperrdatei
10. Einstellungen in `.env`
11. Fehlerbilder

Alle Befehle stehen in grauen Kästen und werden auf dem VPS im Terminal eingegeben. `sudo` heißt:
mit Administratorrechten.

## 1. Überblick: Werkstatt und Live-Betrieb

| | Werkstatt | Live-Betrieb |
|---|---|---|
| Ordner | `~/dev/rvkompass` | `/srv/rvkompass` |
| Zweck | Entwickeln, Tests, Steuerprogramm | Die öffentlich erreichbare App |
| Start | `flask … run --debug` (nur zum Ausprobieren) | Docker-Container mit gunicorn |
| Daten | `~/dev/rvkompass/data/` | `/srv/rvkompass/data/` |

Aufbau unter `/srv/rvkompass`:

```
/srv/rvkompass/
├── app/      Klon des Repos (Branch develop bzw. später main) – daraus wird das Image gebaut
├── data/     Datenbanken, Embeddings, Modell-Cache, Backups (im Container: /app/data)
├── logs/     Protokolle der App und der Abgleiche (im Container: /app/logs)
└── .env      Einstellungen und Schlüssel (im Container: /app/.env, nur lesbar)
```

Im Container laufen zwei Dinge: gunicorn (die App, Port 8000, nur auf 127.0.0.1 erreichbar) und
cron (monatliche Abgleiche). Caddy auf dem VPS nimmt die Anfragen per HTTPS an und reicht sie an
Port 8000 weiter. Der Container startet nach einem Absturz oder Neustart des Servers von selbst
neu (`restart: unless-stopped`).

## 2. Voraussetzungen

- Docker mit Compose-Plugin (`docker compose version` zeigt eine Version).
- Caddy läuft bereits (Statusseite des Steuerprogramms unter `/status/`).
- Platz auf der Platte (gemessen am 12.11.2026, RVK-Stand 2026/2):

| Was | Größe |
|---|---|
| `rvk.sqlite3` | 610,8 MB |
| `embeddings.npy` + `embeddings_meta.json` | 1 203,3 MB + 6,2 MB |
| `register_term_embeddings.npy` + `register_term_meta.json` | 62,5 MB + 0,9 MB |
| **Live-Dateien zusammen** | **1 883,7 MB** |
| `gnd.sqlite3` (208 435 Sachbegriffe, Abzug 20261013) | 77,0 MB |
| RVK-Rohdatei `rvko_marcxml_2026_2.xml` | 1 375,8 MB |
| GND-Rohdatei (gepackt) | 26,3 MB |
| 2 Backups des Abgleichs (2 × Live-Dateien) | 3 767,4 MB |

  Während eines Abgleichs kommen eine Arbeitskopie (≈ 1,9 GB) und die neue Rohdatei (≈ 1,4 GB)
  dazu. **Rechnen Sie mit gut 10 GB freiem Platz** für `/srv/rvkompass/data`.
- Arbeitsspeicher: Jeder gunicorn-Worker hält die Embeddings (≈ 1,27 GB) und das Sprachmodell für
  die Vektorsuche im Speicher; Standard sind 2 Worker. Der monatliche Abgleich lädt die
  Embeddings zusätzlich. Genaue Werte erst nach dem ersten Start mit `docker stats` messen
  (siehe offene Punkte im Phasenbericht). Mit nur einem Worker
  (`GUNICORN_WORKERS=1 docker compose up -d`) lässt sich der Bedarf etwa halbieren.

## 3. Erstinstallation (ohne Live-Schaltung)

Diese Schritte machen die App noch **nicht** öffentlich: Caddy liefert weiterhin „RVKompass ist in
Vorbereitung“ (503).

### 3.1 Ordner anlegen

```bash
sudo mkdir -p /srv/rvkompass/data /srv/rvkompass/logs
sudo chown -R "$USER":"$USER" /srv/rvkompass
git clone https://github.com/HochhaGu/RVKompass.git /srv/rvkompass/app
cd /srv/rvkompass/app && git checkout develop
```

Der Container arbeitet als Nutzer `rvkompass` mit derselben Nutzer-ID wie Sie (`id -u`, meist
1000). Ist Ihre ID eine andere, beim Bauen angeben: `APP_UID=$(id -u) APP_GID=$(id -g) docker compose build`.

### 3.2 `.env` anlegen

```bash
cp ~/dev/rvkompass/.env /srv/rvkompass/.env   # als Ausgangspunkt
chmod 600 /srv/rvkompass/.env
nano /srv/rvkompass/.env
```

Welche Namen dort stehen können, zeigt Abschnitt 10. Für den Betrieb zusätzlich nötig: `SECRET_KEY`
(langer Zufallswert), `SYNC_TOKEN` (für den manuellen Abgleich) und die Mail-Einstellungen
`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`.
Einen Zufallswert erzeugt `openssl rand -hex 32`.

### 3.3 Daten übernehmen

Am schnellsten: die fertigen Daten aus der Werkstatt kopieren (das Berechnen der Embeddings dauert
sonst Stunden). Vorher in der Werkstatt nichts laufen lassen, was die Datenbank ändert.

```bash
cd ~/dev/rvkompass/data
cp rvk.sqlite3 embeddings.npy embeddings_meta.json \
   register_term_embeddings.npy register_term_meta.json gnd.sqlite3 /srv/rvkompass/data/
```

Alternativ alles im Container neu aufbauen (dauert lange, braucht Netz):

```bash
cd /srv/rvkompass/app
docker compose build
docker compose run --rm --entrypoint "" --user rvkompass rvkompass python scripts/fetch_marcxml_dump.py
docker compose run --rm --entrypoint "" --user rvkompass rvkompass python scripts/sync_rvk.py
docker compose run --rm --entrypoint "" --user rvkompass rvkompass python scripts/build_embeddings.py
docker compose run --rm --entrypoint "" --user rvkompass rvkompass python scripts/sync_gnd.py
```

Das Modell für die Vektorsuche wird beim ersten Start einmal heruntergeladen und unter
`/srv/rvkompass/data/hf-cache` gespeichert.

### 3.4 Image bauen

```bash
cd /srv/rvkompass/app
docker compose build
```

Noch **nicht** `docker compose up` ausführen – das ist Teil der Live-Schaltung.

## 4. Live-Schaltung – erst nach Günthers Freigabe

1. Container starten und prüfen:

   ```bash
   cd /srv/rvkompass/app
   docker compose up -d --build
   docker compose ps                       # Spalte STATUS: "healthy" nach 1–3 Minuten
   curl -s http://127.0.0.1:8000/health    # "status": "ok", RVK-Stand, Anzahl Notationen
   ```

2. Caddy umstellen (die Vorlage leitet „/“ an die App weiter, `/status/` bleibt geschützt,
   `/internal/*` ist von außen gesperrt):

   ```bash
   sudo cp /etc/caddy/sites/rvkompass.caddy /etc/caddy/sites/rvkompass.caddy.vorher
   sudo cp /srv/rvkompass/app/deploy/caddy/rvkompass.caddy.live /etc/caddy/sites/rvkompass.caddy
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

3. Im Browser `https://rvkompass.ghochhauser.at/` öffnen und eine Suche ausprobieren.
4. Zurück auf „in Vorbereitung“: die Datei `rvkompass.caddy.vorher` zurückkopieren und Caddy neu
   laden; den Container mit `docker compose down` anhalten.

Vor der Live-Schaltung: Impressum und Datenschutzerklärung (`/impressum`, `/datenschutz`)
ausfüllen und rechtlich prüfen.

## 5. Monatlicher RVK-Abgleich

**Wann:** am 2. jedes Monats um 03:00 Uhr (UTC) per Cron im Container
(`docker/crontab`). Protokoll: `/srv/rvkompass/logs/sync.log`.

**Ablauf** (`scripts/monthly_sync.py`, Einzelheiten in `app/services/rvk_sync/pipeline.py`):

1. Die RVK-Download-Seite wird gelesen. Gibt es keinen neuen Versionsnamen, endet der Lauf – ohne
   Mail, nur mit einem Eintrag im Protokoll.
2. Die Live-Datenbank wird nach `data/staging/` kopiert; dort wird der neue Gesamtabzug
   eingelesen. Die Live-Daten bleiben unberührt.
3. Vergleich alt/neu: neu, geändert, gelöscht (Tabelle `notation_change`, Grundlage für das
   Etikett „geändert in Version …“ in der Oberfläche).
4. Prüfungen:
   - Die Zahl der Notationen weicht um weniger als 5 % ab (`SYNC_MAX_COUNT_DEVIATION`).
   - Alle Hauptgruppen des alten Stands und die Notationen aus `SYNC_SAMPLE_NOTATIONS` gibt es noch.
   - Goldstandard: Notationen, die im neuen Stand gelöscht sind, werden als „gelöscht in …“
     markiert und zählen vorher wie nachher nicht mit. Notationen, die schon vorher fehlten,
     werden als „fehlte schon vorher“ ausgewiesen und bleiben in der Wertung.
   - Erst wenn die ersten beiden Prüfungen bestanden sind, werden die Embeddings neu berechnet
     (dauert Stunden) und die Evaluation läuft: Hit@10 darf um höchstens 0,05 sinken
     (`SYNC_MAX_HIT10_DROP`).
5. **Bestanden:** Die Live-Dateien werden nach `data/backups/rvk-<alte Version>-<Zeit>/` gesichert
   und in einem Schritt je Datei durch die neuen ersetzt. Es bleiben die letzten 2 Backups
   (`SYNC_BACKUP_KEEP`). Die Zwischenspeicher der App (GND-, Sprachmodell-Cache, Tageszähler der
   Sprachmodell-Aufrufe) werden übernommen. gunicorn startet die Worker sanft neu.
   **Nicht bestanden:** Die alte Datenbank bleibt live, die Arbeitskopie wird gelöscht.
6. **Info-Mail** bei jedem neuen Stand (bestanden oder nicht) und bei Fehlern, mit allen Zahlen
   direkt im Mailtext. Ist kein SMTP eingestellt oder klappt der Versand nicht, liegt die Mail als
   `logs/sync-mail-<Zeit>.html` vor.

**Manuell anstoßen** (auf dem VPS):

```bash
cd /srv/rvkompass/app
docker compose exec --user rvkompass rvkompass python scripts/monthly_sync.py --dry-run   # nur prüfen
docker compose exec --user rvkompass rvkompass python scripts/monthly_sync.py             # echt
docker compose exec --user rvkompass rvkompass python scripts/monthly_sync.py --force     # auch ohne neuen Stand
```

Oder über die App (nur lokal auf dem Server, von außen gesperrt); das Token steht in `.env`:

```bash
read -rs TOKEN   # Token eingeben, wird nicht angezeigt
curl -s -X POST -H "X-Sync-Token: $TOKEN" http://127.0.0.1:8000/internal/sync
```

Antwort `{"status":"started"}` (läuft im Hintergrund) oder `{"status":"running"}` (es läuft schon
einer). Zwei Abgleiche gleichzeitig sind ausgeschlossen (Sperrdatei `data/sync.lock`).

**Von Hand auf ein Backup zurück:**

```bash
cd /srv/rvkompass/app
docker compose stop
ls /srv/rvkompass/data/backups/                       # gewünschten Ordner wählen
cp /srv/rvkompass/data/backups/rvk-2026_2-<Zeit>/* /srv/rvkompass/data/
docker compose start
curl -s http://127.0.0.1:8000/health                  # rvk_version prüfen
```

## 6. GND-Abzug

RVKompass nutzt die Sachschlagwörter der GND als lokalen Abzug der Deutschen Nationalbibliothek
(CC0, `https://data.dnb.de/opendata/`, Datei `authorities-gnd-sachbegriff_lds_<Datum>.jsonld.gz`).
lobid-gnd wird nur noch gefragt, wenn `data/gnd.sqlite3` fehlt oder unlesbar ist.

- Aktualisierung am 3. jedes Monats um 04:00 Uhr (UTC), Protokoll `logs/gnd.log`.
- Gleiche Version wie bisher → nichts zu tun. Neue Version → Aufbau in `data/staging/`, Prüfung
  (Anzahl weicht um weniger als 5 % ab), dann Tausch. Bei Fehlern bleibt der alte Stand und es
  kommt eine Mail.
- Von Hand: `docker compose exec --user rvkompass rvkompass python scripts/sync_gnd.py`
- Zurück auf nur lobid: in `.env` `GND_SOURCE=lobid` und `docker compose restart`.

## 7. `/health` und Nutzungsstatistik

```bash
curl -s http://127.0.0.1:8000/health
```

Felder: `status` (`ok`/`degraded`), `db_version` (letzter erfolgreicher Abgleich), `rvk_version`,
`notation_count`, `gnd_version`, `cache_hits_today`, `requests_today`, `zero_hit_rate_today`
(Anteil der Anfragen ohne Treffer als Dezimalzahl 0–1). Fehlt die Datenbank, antwortet `/health`
mit Status 503; Docker zeigt den Container dann als „unhealthy“.

Die Nutzungsstatistik liegt in `data/usage.sqlite3`: nur Tageszähler, keine IP-Adressen, keine
Suchtexte. Weder gunicorn noch Caddy schreiben ein Zugriffsprotokoll.

### 7.1 Monatsstatistik per Mail und Statistikseite

**Mail:** am 1. jedes Monats um 07:00 Uhr (Ortszeit des Servers) per systemd-Timer
`rvkompass-monthly-report.timer`. Der Timer führt `scripts/monthly_report.py` im laufenden
Container aus. Inhalt der Mail:
- Vormonat im Vergleich zum Monat davor, mit Tagesverlauf
- Überblick über 12 Monate
- Link „Alle Daten“

Die Datenbanken werden nur lesend geöffnet. Ohne SMTP landet die Mail als
`logs/usage-mail-<zeit>.html`.

**Seite:** `https://rvkompass.ghochhauser.at/status/statistik` zeigt alle Monate, je Monat
aufklappbar mit Tageswerten. CSV-Download unter `/status/statistik.csv?ebene=monat` bzw.
`?ebene=tag`. Den Passwortschutz übernimmt Caddy mit derselben Datei wie für `/status/`
(Block `handle /status/statistik*` in `deploy/caddy/rvkompass.caddy.live`).

Einrichten (einmalig; vorher das Image neu bauen, damit das Skript im Container liegt):

```bash
cd /srv/rvkompass/app && git pull && docker compose up -d --build
sudo cp deploy/systemd/rvkompass-monthly-report.service deploy/systemd/rvkompass-monthly-report.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rvkompass-monthly-report.timer
systemctl list-timers rvkompass-monthly-report.timer      # nächster Lauf
sudo cp /etc/caddy/sites/rvkompass.caddy /etc/caddy/sites/rvkompass.caddy.vorher
sudo cp deploy/caddy/rvkompass.caddy.live /etc/caddy/sites/rvkompass.caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Probelauf ohne Mail (schreibt `reports/monatsstatistik-<JJJJ-MM>.html` im Container):

```bash
docker compose exec -T --user rvkompass rvkompass python /app/scripts/monthly_report.py --month 2026-11 --dry-run
```

Mail sofort auslösen: `sudo systemctl start rvkompass-monthly-report.service`; Protokoll:
`journalctl -u rvkompass-monthly-report.service`.

**Kennzahlen:**
- Die Statistik beginnt mit dem ersten Tag mit Suchen. Monate davor zeigen „–“.
- Ø pro Tag: über die Kalendertage, im Startmonat ab dem ersten Tag mit Suchen, im laufenden
  Monat bis heute.
- Rückgriffe auf lobid: Zahl der Abfragen an lobid-gnd (Rückfallebene, wenn der lokale
  GND-Abzug fehlt oder unlesbar ist). Gezählt wird erst seit der Monatsstatistik, ältere Tage
  zählen als 0.
- Cache-Quote Sprachmodell: Anteil der Abfragen aus dem Zwischenspeicher. Ältere Tage werden
  aus Cache-Treffern und Claude-Aufrufen berechnet.
- Claude-API-Aufrufe: nur aus echten Suchen (Spalte `claude_calls` in `daily_usage`).
  Evaluationsläufe (`scripts/evaluate.py`, `scripts/tune_scoring.py`, Prüfung im monatlichen
  Abgleich) zählen in `claude_calls_eval`. Das Tageslimit `CLAUDE_DAILY_LIMIT` gilt für die Summe.

**Einmalige Korrektur beim Ausrollen:** Die 391 Aufrufe vom 11.11.2026 stammen aus einem
Evaluationslauf. Sie stehen noch in `claude_calls` und gehören nach `claude_calls_eval`.
Die SQL-Anweisungen:

```sql
-- nur falls die Spalte noch fehlt
ALTER TABLE daily_usage ADD COLUMN claude_calls_eval INTEGER NOT NULL DEFAULT 0;
-- verschieben; durch die Bedingung claude_calls = 391 ändert ein zweiter Lauf nichts
UPDATE daily_usage
   SET claude_calls_eval = claude_calls_eval + claude_calls, claude_calls = 0
 WHERE day = '2026-11-11' AND claude_calls = 391;
```

Ausführen im Container (auf dem VPS ist kein `sqlite3`-Programm nötig), nach dem Neubau des
Images:

```bash
cd /srv/rvkompass/app
docker compose exec -T --user rvkompass rvkompass python - <<'PY'
import sqlite3
conn = sqlite3.connect("/app/data/rvk.sqlite3")
columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_usage)")}
if "claude_calls_eval" not in columns:
    conn.execute("ALTER TABLE daily_usage ADD COLUMN claude_calls_eval INTEGER NOT NULL DEFAULT 0")
conn.execute(
    "UPDATE daily_usage SET claude_calls_eval = claude_calls_eval + claude_calls, claude_calls = 0 "
    "WHERE day = '2026-11-11' AND claude_calls = 391"
)
conn.commit()
print(conn.execute("SELECT day, claude_calls, claude_calls_eval FROM daily_usage WHERE day = '2026-11-11'").fetchone())
conn.close()
PY
```

Erwartete Ausgabe: `('2026-11-11', 0, 391)`.

## 8. Routine „Update und Backup“

Empfehlung: einmal im Monat, nach dem RVK-Abgleich (z. B. am 5.), dazu Sicherheitsupdates
automatisch.

### 8.1 Systemupdates

```bash
sudo apt update && sudo apt upgrade
[ -f /var/run/reboot-required ] && echo "Neustart nötig"   # dann: sudo reboot
```

Sicherheitsupdates automatisch einspielen (einmalig einrichten):

```bash
sudo apt install unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

Nach einem Neustart startet der Container von selbst; prüfen mit `docker compose ps` und
`curl -s http://127.0.0.1:8000/health`.

### 8.2 Snapshot beim VPS-Anbieter

Vor größeren Updates (neue Ubuntu-Version, Docker-Update, App-Update mit neuer Sperrdatei) im
Kundenbereich des VPS-Anbieters einen Snapshot anlegen und ihn nach einigen Tagen ohne Probleme
wieder löschen.

### 8.3 Datenbank-Backup

Online-Backup im laufenden Betrieb (konsistent, auch während Anfragen kommen):

```bash
cd /srv/rvkompass/app
docker compose exec -T --user rvkompass rvkompass \
    python scripts/backup_db.py --target /app/data/db-backups --keep 2
sudo mkdir -p /var/backups/rvkompass
sudo rsync -a --delete /srv/rvkompass/data/db-backups/ /var/backups/rvkompass/db/
sudo cp /srv/rvkompass/.env /var/backups/rvkompass/env.backup && sudo chmod 600 /var/backups/rvkompass/env.backup
sudo cp -a /etc/caddy /var/backups/rvkompass/caddy
```

Gesichert werden `rvk.sqlite3`, `gnd.sqlite3` und `usage.sqlite3` (Embeddings lassen sich neu
berechnen und liegen zusätzlich in den Abgleich-Backups unter `data/backups/`).
`/var/backups/rvkompass` liegt außerhalb von `/srv/rvkompass/data`; am besten zusätzlich vom
Server herunterladen oder in einen Speicher außerhalb des VPS kopieren.

Als wöchentlicher Cron-Eintrag auf dem Server (`sudo crontab -e`), sonntags 02:30 Uhr:

```
30 2 * * 0 cd /srv/rvkompass/app && docker compose exec -T --user rvkompass rvkompass python scripts/backup_db.py --target /app/data/db-backups --keep 2 && rsync -a --delete /srv/rvkompass/data/db-backups/ /var/backups/rvkompass/db/
```

**Zurückspielen:**

```bash
cd /srv/rvkompass/app
docker compose stop
cp /var/backups/rvkompass/db/<Zeit>/rvk.sqlite3 /srv/rvkompass/data/rvk.sqlite3
docker compose start
```

Passen Embeddings und Datenbank nicht zusammen (z. B. Datenbank älter als die Embeddings), das
vollständige Abgleich-Backup aus `data/backups/` nehmen (Abschnitt 5).

### 8.4 App-Update

```bash
cd /srv/rvkompass/app
git pull
docker compose build
docker compose up -d
docker compose ps && curl -s http://127.0.0.1:8000/health
```

### 8.5 Abhängigkeiten aktualisieren

Nur bewusst und mit Messung: in der Werkstatt Pakete ändern, `python scripts/build_lock.py`
schreibt beide Sperrdateien neu, danach `pytest -q` und
`python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/` – Hit@10 darf nicht
sinken. Erst dann committen und wie in 8.4 ausrollen.

## 9. Abgleich der Werkstatt-Umgebung (`.venv`) mit der Sperrdatei

Vorher anzeigen, was sich ändern würde:

```bash
cd ~/dev/rvkompass
.venv/bin/pip freeze | diff - <(grep -v '^[-#]' requirements-dev.lock)
```

Abgleichen und prüfen:

```bash
.venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-dev.lock && .venv/bin/pip check
```

**Stand 12.11.2026:** gunicorn 27.0.0 ist in der Werkstatt installiert und steht in beiden
Sperrdateien. Kommt künftig ein neues Paket dazu: in der `.venv` installieren, dann
`.venv/bin/python scripts/build_lock.py` (schreibt die Sperrdateien neu), `.venv/bin/pip check`
und die Evaluation laufen lassen.

## 10. Einstellungen in `.env`

Nur Namen; Werte gehören nie ins Repo. Fehlt ein Eintrag, gilt der Standard.

| Name | Standard | Bedeutung |
|---|---|---|
| `SECRET_KEY` | – | Zufallswert für Flask |
| `SYNC_TOKEN` | leer | Token für `/internal/sync`; leer = Endpunkt gesperrt |
| `CLAUDE_API_KEY`, `ENABLE_QUERY_EXPANSION`, `CLAUDE_DAILY_LIMIT`, `CLAUDE_MODEL` | | Begriffserweiterung per Sprachmodell (Standard aus) |
| `ENABLE_GND_EXPANSION` | `true` | GND-Erweiterung |
| `GND_SOURCE` | `local` | `local` (Abzug, lobid als Rückfall) oder `lobid` |
| `GND_DATABASE_PATH`, `USAGE_DATABASE_PATH` | `data/gnd.sqlite3`, `data/usage.sqlite3` | |
| `SYNC_MAX_COUNT_DEVIATION` | `0.05` | erlaubte Abweichung der Notationsanzahl |
| `SYNC_MAX_HIT10_DROP` | `0.05` | erlaubter Rückgang von Hit@10 |
| `SYNC_BACKUP_KEEP` | `2` | Zahl der Abgleich-Backups |
| `SYNC_SAMPLE_NOTATIONS` | leer | zusätzliche Stichprobe, kommagetrennt, z. B. `ZL 4620,ST 230` |
| `SYNC_WORK_DIR`, `SYNC_BACKUP_DIR`, `SYNC_LOCK_PATH`, `SYNC_LOG_PATH`, `SYNC_RAW_DIR`, `SYNC_GOLD_PATH` | unter `data/`, `logs/`, `eval/` | Pfade des Abgleichs |
| `SMTP_HOST`, `SMTP_PORT` (`587`), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS` (`true`), `SMTP_TIMEOUT_SECONDS` (`30`) | | Mailversand |
| `SMTP_SECURITY` | leer | `ssl` (direkt verschlüsselt) oder `starttls`, wie beim Steuerprogramm; leer: Port 465 → `ssl`, sonst `starttls` |
| `MAIL_FROM`, `MAIL_TO` | leer | Absender und Empfänger der Info-Mail |
| `MAIL_FALLBACK_DIR` | `logs/` | Ablage der Mail, wenn Versand nicht möglich |
| `USAGE_STATS_URL` | `https://rvkompass.ghochhauser.at/status/statistik` | Link „Alle Daten“ in der Monatsmail |
| `GUNICORN_PID_PATH` | `/tmp/gunicorn.pid` | für den Neustart der Worker |
| `PROXY_FIX_X_FOR` | `0` (im Container `1`) | Zahl der Reverse-Proxys vor der App |
| `DEFAULT_MAIN_GROUPS`, `SCORING_FORMULA`, `EMBEDDING_MODEL_NAME` | | wie bisher |

## 11. Fehlerbilder

| Beobachtung | Ursache und Abhilfe |
|---|---|
| `docker compose ps` zeigt „unhealthy“ | `docker compose logs --tail 100`; meist fehlt eine Datei in `data/` oder sie gehört einem anderen Nutzer (`ls -l /srv/rvkompass/data`). |
| Seite zeigt „RVKompass ist in Vorbereitung“ | Caddy nutzt noch die Datei mit 503 (Abschnitt 4, Schritt 2). |
| Caddy meldet 502 | Container läuft nicht: `docker compose up -d`. |
| Keine Info-Mail | `logs/sync.log` lesen; liegt eine `logs/sync-mail-*.html` vor, stimmen die SMTP-Werte in `.env` nicht. |
| Abgleich „läuft bereits“ | Ein anderer Abgleich ist noch aktiv; Ende abwarten (`logs/sync.log`). |
| Platte voll | `du -sh /srv/rvkompass/data/*`; alte Rohdateien in `data/raw/` und `data/db-backups/` prüfen. |
| `/health` zeigt `gnd_version: null` | `data/gnd.sqlite3` fehlt; die App fragt dann lobid. `python scripts/sync_gnd.py` im Container ausführen. |
| Nach dem Abgleich alte Ergebnisse | Worker nicht neu gestartet: `docker compose restart`. |
