# RVKompass – Projektkontext für Claude Code

## Übersicht
1. Zweck und Zielgruppe
2. Leitprinzipien (nicht verhandelbar)
3. Architektur und Datenfluss
4. Befehle
5. Konventionen
6. Grenzen – was Claude nicht tut
7. Arbeitsweise pro Phase
8. Projektentscheidungen (Stand 10.11.2026)

## 1. Zweck und Zielgruppe
RVKompass schlägt zu einem Freitext-Suchbegriff passende Systemstellen der Regensburger
Verbundklassifikation (RVK) vor. Zielgruppe sind Bibliothekar:innen, die **fachfremd** klassifizieren
(Beispiel: Nicht-Techniker in einer technischen Spezialbibliothek). Das Werkzeug ersetzt keine
intellektuelle Entscheidung, es macht den Suchraum sichtbar.

## 2. Leitprinzipien (nicht verhandelbar)
- **Transparenz vor Raffinesse.** Jeder Treffer trägt ein Evidence-Objekt: über welchen Pfad
  (Direkt, GND-Synonym, GND-Oberbegriff, GND-Unterbegriff, GND-verwandt, LLM-verifiziert, Vektor)
  und über welchen Begriff er gefunden wurde. Kein Treffer ohne nachvollziehbare Herkunft.
- **Kontrolliertes Vokabular zuerst.** Erweiterung primär über GND (lobid-gnd) und das RVK-Register
  (GND-IDs). LLM-Vorschläge werden nur übernommen, wenn sie in GND oder RVK-Register verifiziert sind.
- **Score ist Relevanz, nicht Wahrscheinlichkeit.** In der UI nie „Konfidenz“ oder Prozent
  suggerieren, solange keine Kalibrierung gegen den Goldstandard vorliegt.
- **Messen statt meinen.** Änderungen an Erweiterung, Scoring oder Modell nur mit Vorher/Nachher-Lauf
  von `scripts/evaluate.py` (Hit@1, Hit@5, Hit@10, MRR, Hauptgruppen-Treffer).
- **Deutsch in UI, Kommentaren und Commit-Messages.** Code-Bezeichner Englisch.

## 3. Architektur und Datenfluss
- Flask-App (`app/`), HTMX-Frontend, SQLite mit FTS5 (`data/rvk.sqlite3`).
- Ingestion: `scripts/fetch_marcxml_dump.py` → `scripts/sync_rvk.py` → `scripts/build_embeddings.py`.
- Suche: `app/routes/search.py` → `app/services/expansion/*` → `app/services/ranking.py`.
- Externe Quellen: RVK-API v3 (`https://rvk.uni-regensburg.de/api3/json/`, CC0),
  lobid-gnd (`https://lobid.org/gnd/search`), Anthropic API (nur Begriffserweiterung, gecacht).

## 4. Befehle
- Tests: `pytest -q`
- Formatierung/Lint: `ruff format . && ruff check .`
- Evaluation: `python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/`
- Dev-Server: `flask --app app:create_app run --debug`

## 5. Konventionen
- Neue Logik als Service unter `app/services/`, reine Funktionen wo möglich, testbar ohne Netz.
- Externe HTTP-Aufrufe immer mit Timeout, Cache (SQLite-Tabelle) und Fehler-Fallback auf „keine Erweiterung“.
- Jede neue Funktion bekommt einen Test; Netzaufrufe in Tests werden gemockt.
- Konfiguration ausschließlich über `app/config.py` und `.env`.

## 6. Grenzen – was Claude nicht tut
- Keine Inhalte aus `.env` lesen oder ausgeben, keine Schlüssel in Code oder Logs.
- `data/` nie committen, nie löschen. Rebuilds nur über die Skripte.
- Claude-Sitzungen führen nie `git push` aus, kein Force-Push, keine Änderungen an `main` direkt –
  Feature-Branch pro Phase. Nur das Steuerprogramm (`orchestrator/`) pusht Phasen-Branches und
  merged sie per Pull Request nach `develop`. Nach `main` pusht oder merged niemand automatisch.
- Keine neuen schweren Abhängigkeiten (z. B. weitere Torch-Modelle) ohne Rückfrage.
- Keine Institution, keinen Arbeitgeber und keine institutionellen Datenzugänge nennen oder nutzen.
  RVKompass ist ein privates Projekt; nur offene, frei lizenzierte Datenquellen.

## 7. Arbeitsweise pro Phase
1. Plan Mode: Plan vorlegen, auf Freigabe warten.
2. Umsetzen in kleinen, getesteten Schritten.
3. `pytest -q` grün, Evaluation gelaufen (ab Phase 1), Ergebnis in `reports/` abgelegt.
4. Kurzer Abschlussbericht: Was geändert, Kennzahlen vorher/nachher, offene Punkte.

## 8. Projektentscheidungen (Stand 10.11.2026)
| Thema | Entscheidung |
|---|---|
| Hosting | VPS mit Docker (gunicorn, Reverse Proxy mit HTTPS). Kein Shared Webhosting. |
| Lizenz | EUPL 1.2, Repo wird öffentlich. Privates Projekt von Günther Hochhauser. |
| GND-Quelle | P3: lobid-gnd live mit 30-Tage-Cache. P8: lokaler GND-Abzug, lobid als Rückfallebene. |
| Homonyme | Nicht still auflösen. Ergebnisblöcke je Bedeutung, Vorrang für die Bedeutung, deren GND-Sachgruppe zum Fachfilter passt. |
| RVK-Versionen | Monatlicher Check; neuer Stand geht nach bestandenen Prüfungen automatisch live; Info-Mail mit Kennzahlen (HTML im Mailtext) bei jedem Wechsel und bei Fehlern. |
| Erwartungsmanagement | In die Oberfläche eingebaut: „Vorschläge“, RVK-Bemerkung und Hierarchiepfad am Treffer, Warnhinweis bei reiner Vektor-Evidenz, Prüflink zu RVK-Online, Hilfeseite. Kein erzwungener Prüfschritt. |
