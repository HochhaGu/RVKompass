# Phase P8 – Betrieb

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
RVKompass soll ohne dein Zutun laufen. Dafür wurde alles für den Betrieb auf dem VPS vorbereitet: ein Docker-Container (ein fertig verpacktes Programm samt Umgebung), ein monatlicher geprüfter Abgleich mit der RVK, ein lokaler GND-Abzug und eine Anleitung. **Nichts ist live.** Es läuft kein Container, und Caddy (der Webserver davor) zeigt weiter „in Vorbereitung“.

## Was sich geändert hat

**Betrieb im Container**
- Was: Container-Dateien mit gunicorn (ein richtiger Webserver für Python statt des Testservers), automatischem Neustart und Cron (Zeitplaner für den Monatslauf).
- Warum: Die App soll dauerhaft und ohne Handarbeit laufen. gunicorn und die Zeitplan-Jobs laufen ohne Root-Rechte.
- Wo: `Dockerfile`, `docker/entrypoint.sh`, `docker/gunicorn.conf.py`, `docker/crontab`, `docker-compose.yml`, `.dockerignore`.

**Caddy-Vorlage**
- Was: Eine Vorlage, die „/“ an die App weitergibt. `/status/` bleibt geschützt, `/internal/*` ist von außen gesperrt.
- Warum: Die Live-Schaltung soll später ein einziger, überschaubarer Schritt sein. Die Vorlage ist nicht aktiv.
- Wo: `deploy/caddy/rvkompass.caddy.live`.

**Feste Paketversionen**
- Was: Zwei Sperrdateien mit den genauen Versionen aller benötigten Pakete. gunicorn steht jetzt fest auf 27.0.0. torch ist die CPU-Fassung, es gibt keine CUDA-Pakete (die brauchen eine Grafikkarte). Ein Test prüft das.
- Warum: Im Container sollen exakt die Versionen laufen, die getestet wurden.
- Wo: `requirements.lock`, `requirements-dev.lock`, `requirements.txt`, `scripts/build_lock.py`.

**Monatlicher RVK-Abgleich**
- Was: Ein Programm prüft, ob es einen neuen RVK-Stand gibt. Wenn ja, baut es auf einer Kopie alles neu auf und vergleicht alt gegen neu: neu, geändert, gelöscht. Dann folgen vier Prüfungen: Anzahl der Notationen weicht weniger als 5 % ab, alle Hauptgruppen sind noch da, Goldstandard-Einträge werden geprüft, Hit@10 sinkt höchstens um 0,05. Nur wenn alles besteht, wird getauscht. Die letzten zwei Stände bleiben als Backup. Sonst bleibt die alte Datenbank live.
- Warum: Ein fehlerhafter neuer Stand soll nie unbemerkt live gehen.
- Wo: `scripts/monthly_sync.py`, `app/services/rvk_sync/` (`pipeline.py`, `diff.py`, `checks.py`, `swap.py`), `scripts/sync_rvk.py`, `scripts/fetch_marcxml_dump.py`, `scripts/build_embeddings.py`.
- Änderungen werden in der Tabelle `notation_change` festgehalten, über den Notationstext statt über die Nummer. Die Nummern entstehen bei jedem Neuaufbau neu. Dafür wurde die Tabelle angepasst (`app/models.py`, `app/services/rvk_repository.py`).
- Gelöschte Gold-Notationen werden als „gelöscht in <Version>“ markiert und zählen vorher wie nachher nicht mit (`app/services/evaluation.py`). Solche, die schon vorher fehlten, stehen getrennt in der Mail.
- Zwei Läufe gleichzeitig werden durch eine Sperre verhindert. Nach dem Tausch bekommt gunicorn ein Signal und lädt neu.
- Der Abgleich lässt sich auch über `/internal/sync` starten. Das geheime Token steht im Kopf der Anfrage (`X-Sync-Token`) und nie im Protokoll (`app/routes/internal.py`).

**Info-Mail**
- Was: Eine Mail bei jedem Wechsel des Stands und bei Fehlern. Das HTML steht direkt im Mailtext, ohne Anhang und ohne Link. Die Werte heißen „Relevanzmaß“. Ohne SMTP-Zugang (Mailserver-Zugang) wird die Mail als Datei in `logs/` abgelegt.
- Warum: Du siehst, was sich geändert hat, ohne nachsehen zu müssen.
- Wo: `app/services/mailer.py`, `app/templates/mail/sync_report.html`, `app/templates/mail/sync_report.txt`.

**Lokale GND**
- Was: Der GND-Abzug der DNB (frei lizenziert, CC0) wird heruntergeladen und in einer eigenen Datenbank abgelegt. lobid, die Online-Abfrage, springt nur noch ein, wenn diese Datei fehlt oder kaputt ist. Der Aufbau lief schon: Stand 13.10.2026, 208 435 Sachbegriffe, 483 Sachgruppen-Bezeichnungen.
- Warum: Schneller, unabhängig vom Netz und ohne Abfragen bei einem fremden Dienst.
- Wo: `scripts/fetch_gnd_dump.py`, `scripts/sync_gnd.py`, `app/services/gnd_local.py`, `app/services/expansion/gnd.py`.
- Vergleich mit lobid für alle 97 Gold-Anfragen: 87 gleich, 2 mit anderer Reihenfolge, 1 Anfrage nur lokal mit Bedeutungen, keine nur bei lobid. Lokal kommen manchmal zusätzliche Bedeutungen über Namensvarianten dazu, etwa bei „Bank“. Bericht: `reports/gnd-vergleich-2026-11-12.json`, Skript: `scripts/compare_gnd_sources.py`.

**Health-Seite und Statistik**
- Was: `/health` zeigt den Zustand der App. Dazu kommt eine Statistik mit reinen Tageszählern: Anfragen, Anfragen ohne Treffer, Cache-Treffer. Gespeichert werden keine IP-Adressen und keine Suchtexte.
- Warum: Docker kann so erkennen, ob die App gesund ist. Du siehst, ob sie genutzt wird.
- Wo: `app/routes/health.py`, `app/services/usage_stats.py`, `app/routes/search.py`, `app/templates/datenschutz.html`.
- Die Datenschutzseite hat sachliche Ergänzungen. Die Rechtstexte bleiben deine Aufgabe.

**Weiteres**
- `scripts/backup_db.py` (Datenbank-Backup), `scripts/data_report.py`, Anpassung von `scripts/evaluate.py` und `app/services/evaluation.py` für die Prüfung vor dem Tausch.
- Anleitung für Installation, Live-Schaltung, Abgleich, Rücksprung auf ein Backup und Fehlerbilder: `docs/deployment.md`. Die README beschreibt jetzt den Betrieb auf dem VPS.
- 98 neue Tests.

## Kennzahlen vorher/nachher
Alle Werte sind Relevanzmaße zwischen 0 und 1, keine Wahrscheinlichkeiten.

| Kennzahl | Vorher | Nachher |
|---|---|---|
| Hit@1 | 0.258 | 0.258 |
| Hit@5 | 0.443 | 0.443 |
| Hit@10 | 0.546 | 0.546 |
| MRR | 0.340 | 0.343 |
| Hauptgruppen-Treffer | – | – |

Hit@10 ist unverändert (Änderung +0.000). Der Wert liegt damit über der Schwelle. MRR steigt minimal.

Prüfungen: `ruff check` ohne Funde. `pytest`: 576 bestanden (vorher 478). Bei der Evaluation gab es 0 Aufrufe und 0 Fehler beim Sprachmodell (claude-haiku-5-5). Von 641 Vorschlägen waren 541 geprüft.

## Was das für die Nutzung bedeutet
- Die Suchergebnisse sind praktisch dieselben wie vorher. Es gibt keine neue Funktion in der Oberfläche.
- Die GND-Erweiterung kommt jetzt aus der lokalen Datei und ist dadurch schneller.
- Sobald du live schaltest, hält sich die App monatlich selbst aktuell. Du bekommst eine Mail, und eine fehlgeschlagene Prüfung lässt die alte Datenbank stehen.
- Das Etikett „neu/geändert“ an Notationen erscheint erst ab dem nächsten RVK-Stand.

## Offene Punkte
1. **`.venv` abgleichen**, nach dem Merge in `~/dev/rvkompass`. Der erste Befehl zeigt nur an, was sich ändern würde. Der zweite gleicht ab und prüft.
   ```bash
   .venv/bin/pip freeze | diff - <(grep -v '^[-#]' requirements-dev.lock)
   .venv/bin/pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-dev.lock && .venv/bin/pip check
   ```
2. **`passenger_wsgi.py`** ist ein Rest vom alten Webhosting und wird nicht mehr benutzt. Ich darf nicht löschen. Bitte mit `git rm passenger_wsgi.py` entfernen.
3. **`.env.example`** konnte ich nicht lesen und deshalb nicht ergänzen. Die neuen Einstellungsnamen stehen in `docs/deployment.md`, Abschnitt 10.
4. **Größe des Images und Arbeitsspeicher** sind nicht gemessen, weil das volle Image nicht gebaut wurde. Nach dem ersten Start mit `docker stats` nachsehen und dann `mem_limit` in `docker-compose.yml` setzen.
5. **Der monatliche Abgleich ist nur über Tests geprüft** und noch nicht echt gelaufen. `notation_change` bleibt leer bis zum nächsten RVK-Stand, denn heute liegt nur 2026/2 vor.
6. **Impressum und Datenschutz** brauchen vor der Live-Schaltung deine Angaben und eine rechtliche Prüfung.
7. **Live-Schaltung** machst du selbst nach deiner Freigabe: Container starten, Caddy-Vorlage tauschen, SMTP-Werte eintragen. Siehe `docs/deployment.md`, Abschnitte 3 und 4.

## Empfehlung für die nächste Phase
Zuerst die offenen Punkte 1 bis 3 erledigen, dann die Live-Schaltung vorbereiten (Punkte 4, 6 und 7). Den ersten echten Monatslauf mit `--dry-run` beobachten, bevor er etwas tauschen darf. Danach lohnt sich ein Blick auf die Nutzungsstatistik: Welche Anfragen bleiben ohne Treffer? Daraus lässt sich die nächste Verbesserung ableiten, gemessen mit `scripts/evaluate.py`.
