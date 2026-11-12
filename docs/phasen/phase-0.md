# Phase P0 – Onboarding und Audit

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
In dieser Phase wurde der heutige Stand von RVKompass ehrlich geprüft. Danach wurde das Projekt für die Veröffentlichung vorbereitet (Lizenz, README, Stilprüfung). Am Verhalten des Programms hat sich nichts geändert.

## Was sich geändert hat

- **Prüfbericht (Audit) geschrieben.**
  - Was: Ein Bericht über Aufbau, Schwachstellen und Aufgaben für die Phasen P1 bis P6. Er enthält auch die Prüfung der Git-Historie (Git ist die Versionsverwaltung des Projekts) auf versehentlich abgelegte Schlüssel.
  - Warum: Wir brauchen eine gemeinsame, ehrliche Ausgangslage, bevor wir etwas verbessern.
  - Wo: `docs/audit-2026-11.md`
  - Wichtigste Ergebnisse:
    - Suchbegriff und Zusatzbegriffe werden zu einem einzigen Vektor vermischt. (Ein Vektor ist hier eine Zahlenreihe, die den Sinn eines Textes darstellt.) Deshalb lässt sich kein Treffer einem bestimmten Begriff zuordnen.
    - Die Prozentanzeige „Konfidenz“ ist fachlich nicht haltbar. Sie ist keine Wahrscheinlichkeit. Auch ein sinnloser Treffer kann schon recht hoch aussehen.
    - In der Git-Historie (14 Commits) steckt kein echter Schlüssel. Gefunden wurden nur absichtliche Testattrappen und ein Platzhalter in einem Kommentar. Die Historie muss nicht umgeschrieben werden.

- **Lizenzdatei angelegt.**
  - Was: Der offizielle englische Text der EUPL 1.2 (die Lizenz der Europäischen Union für freie Software). Die Datei wurde unverändert übernommen und auf Vollständigkeit geprüft.
  - Warum: Das Repo soll öffentlich werden. Dafür braucht es eine Lizenz.
  - Wo: `LICENSE`

- **README ergänzt.**
  - Was: Der Satz „Privates Projekt von Günther Hochhauser.“, ein neuer Abschnitt „Lizenz“ mit der Kennung `EUPL-1.2` und dem Link zur deutschen Fassung, und ein Hinweis „Veraltet“ über dem Deployment-Abschnitt.
  - Warum: Besucher sollen sofort sehen, unter welchen Bedingungen sie den Code nutzen dürfen. Der alte Betriebsweg (Netcup-Webhosting) gilt nicht mehr.
  - Wo: `README.md`

- **Kleine Korrekturen am Code, damit die Stilprüfung durchläuft.**
  - Was: Ungenutzte Importe wurden entfernt. Bei Importen, deren Reihenfolge Absicht ist, steht jetzt eine Markierung, die `ruff` (das Prüfwerkzeug für sauberen Stil) sagt: „Das ist so gewollt.“
  - Warum: Das Prüftor `ruff check .` muss grün sein.
  - Wo: `app/__init__.py`, `app/services/embedding_service.py`, `app/services/query_expansion.py`, `app/services/rvk_repository.py`, `scripts/build_embeddings.py`, `scripts/sync_rvk.py`
  - Am Verhalten ändert das nichts. Die genauen Einzelheiten stehen im Abschnitt zur Veröffentlichungsvorbereitung im Prüfbericht.

- **Ein Test schreibt nicht mehr in die echte Datenbank.**
  - Was: Der Test für den Zwischenspeicher der Begriffserweiterung nutzt jetzt eine leere Datenbank, die nur im Arbeitsspeicher existiert.
  - Warum: Vorher legte er bei jedem Lauf einen Eintrag in deiner echten Datenbank an.
  - Wo: `tests/test_repository.py`

- **Geprüft, aber nicht geändert:** `.gitignore` und `requirements-dev.txt` waren schon richtig.

## Kennzahlen vorher/nachher

Die Evaluation (Messung der Suchqualität) läuft erst ab Phase P1. Deshalb gibt es hier keine Relevanzzahlen. Die Kennzahlen sind Relevanzmaße, keine Wahrscheinlichkeiten.

| Kennzahl | Vorher | Nachher |
|---|---|---|
| Hit@1 | – | – |
| Hit@5 | – | – |
| Hit@10 | – | – |
| MRR | – | – |
| Hauptgruppen-Treffer | – | – |

Zur Einordnung die technischen Prüfungen:

| Prüfung | Vorher | Nachher |
|---|---|---|
| `ruff check .` | 12 Funde | keine Funde (grün) |
| `pytest -q` | 104 bestanden | 104 bestanden (grün) |

## Was das für die Nutzung bedeutet
- Für Nutzer:innen der Suche ändert sich nichts. Die Treffer und die Anzeige sind dieselben wie vorher.
- Die Prozentanzeige „Konfidenz“ bleibt bis Phase P5 sichtbar. Sie sollte bis dahin nicht als Sicherheit verstanden werden. Sie ist nur ein Relevanzwert.
- Das Projekt kann jetzt rechtlich sauber veröffentlicht werden: Die Lizenz liegt im Repo, die Historie ist frei von echten Schlüsseln.

## Offene Punkte
1. **`.env.example` bitte selbst ansehen.** Diese Datei fehlt laut README-Verweis oder durfte nicht geöffnet werden. Eine Musterprüfung deutet stark auf reine Platzhalter hin. Vor der Veröffentlichung bitte einmal selbst prüfen.
2. Die Prozentanzeige „Konfidenz“ bleibt bis P5 unverändert.
3. Der Deployment-Abschnitt der README wird erst in P8 ersetzt.
4. In der echten Datenbank steht womöglich noch ein alter Testeintrag „testbegriff“ im Zwischenspeicher der Begriffserweiterung. Er ist harmlos. Er wurde nicht entfernt, weil `data/` nicht von Hand geändert wird.
5. Es gibt keine eigene `ruff.toml`. Ohne sie können sich die Prüfregeln mit einer neuen `ruff`-Version still ändern.
6. Impressum und Datenschutzerklärung sind nur Platzhalter. Sie müssen vor dem öffentlichen Betrieb vervollständigt werden.

## Empfehlung für die nächste Phase
Weiter mit **Phase P1**: Dort entsteht das Messwerkzeug `scripts/evaluate.py` mit dem Goldstandard (Liste von Suchbegriffen mit bekannter richtiger Systemstelle). Erst damit lassen sich spätere Änderungen an Suche und Bewertung mit Vorher/Nachher-Zahlen belegen. Sinnvoll ist, in P1 gleich die Test-Isolation weiterzuziehen und eine `ruff.toml` anzulegen.
