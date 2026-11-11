# Phase P1 – Evaluationsbasis

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Bisher ließ sich nicht messen, wie gut RVKompass Systemstellen vorschlägt. Phase P1 baut dafür die Grundlage. Ein Messskript lässt alle 97 Anfragen aus deinem Goldstandard (deine handgepflegte Liste: Suchbegriff und die dazu erwarteten RVK-Notationen) durch die Suche laufen und schreibt einen Bericht. So gibt es ab jetzt einen Ausgangswert, mit dem spätere Änderungen verglichen werden können.

## Was sich geändert hat
- **Messskript.** Es schickt jede Anfrage an die Suche, einmal mit und einmal ohne Begriffserweiterung (zusätzliche Suchwörter aus GND und Claude). Es misst, wie weit oben die erwartete Notation steht, und wie schnell die Antwort kam. Warum: Änderungen sollen nur nach Messung gelten (Leitprinzip „Messen statt meinen“). Wo: `scripts/evaluate.py`, Rechenlogik in `app/services/evaluation.py`.
- **Berichte.** Jeder Lauf erzeugt zwei Dateien in `reports/`: eine zum Lesen (Markdown) und eine für Programme (JSON). Sie zeigen auch die Werte je Anfragetyp und Fachgebiet, die 10 schlechtesten Anfragen und erwartete Notationen, die im RVK-Bestand fehlen (Hinweis auf Tippfehler). Bestehende Berichte werden nie überschrieben; ein zweiter Lauf am selben Tag bekommt die Endung `-2`. Mit `--compare` zeigt ein Bericht die Differenz zu einem älteren. Wo: `scripts/evaluate.py`, `reports/eval-2026-11-11-9cc9d2f.md` und `.json`.
- **Hilfsskript für neue Goldstandard-Zeilen.** Es holt zu Suchbegriffen Titel samt RVK-Notation aus dem offenen K10plus-Katalog (Schnittstelle SRU, Lizenz CC0, am 11.11.2026 geprüft, kein Konto nötig) und schreibt Kandidatenzeilen. Das sind nur Vorschläge. Sie müssen von Hand in RVK-Online geprüft werden. Das Skript weigert sich, `goldstandard.csv` zu überschreiben, und legt bei vorhandener Zieldatei eine neue mit Zeitstempel an. Bei Netzfehlern gibt es eine Warnung, dann geht es weiter. Wo: `scripts/build_gold_candidates.py`, `app/services/gold_candidates.py`.
- **Vorlage angepasst.** Die Vorlage hat jetzt wie dein Goldstandard die Spalte `typ`, mit den erlaubten Werten. Wo: `eval/goldstandard_vorlage.csv`.
- **Tests.** Drei neue Testdateien prüfen die neue Logik, ohne Datenbank und ohne Internet. Wo: `tests/test_evaluation.py`, `tests/test_evaluate_script.py`, `tests/test_gold_candidates.py`.
- **Unverändert:** dein Goldstandard `eval/goldstandard.csv` und `data/`.

## Kennzahlen vorher/nachher
Es sind Relevanzmaße, keine Wahrscheinlichkeiten. Hit@k heißt: Die erwartete Notation steht unter den ersten k Vorschlägen (Anteil der Anfragen, 0 bis 1). MRR ist der Mittelwert von 1 geteilt durch den Rang des ersten richtigen Treffers (1 wäre immer Platz 1).

| Kennzahl | Vorher | Nachher (Prüflauf) |
|---|---|---|
| Hit@1 | – | 0.196 |
| Hit@5 | – | 0.402 |
| Hit@10 | – | 0.484 |
| MRR | – | 0.285 |
| Hauptgruppen-Hit@5 | – | 0.691 (Kontroll-Lauf) |
| Anfragen ohne Treffer | – | 0 (Kontroll-Lauf) |

Es gibt kein „Vorher“, weil dies die erste Messung ist. Die beiden letzten Zeilen stammen aus meinem Kontroll-Lauf, nicht aus dem Bericht des Prüflaufs. Im Kontroll-Lauf lag Hit@10 bei 0.485, im Prüflauf bei 0.484. Der Unterschied ist minimal, ich habe ihn nicht weiter untersucht. Schwach sind laut Kontroll-Lauf die Werkstoffe (Hit@10 0.23) und fachfremd umschriebene Anfragen (0.33).

## Was das für die Nutzung bedeutet
- Für die Suche selbst ändert sich nichts. Die Phase baut nur Messwerkzeug.
- Ab jetzt lässt sich jede Änderung an Erweiterung, Ranking oder Modell mit Zahlen belegen: `python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/`.
- Etwa die Hälfte der Anfragen (Hit@10 0.484) hat die erwartete Stelle in den ersten zehn Vorschlägen. Das ist der Ausgangswert, an dem sich spätere Phasen messen lassen.
- Mit dem Hilfsskript kannst du den Goldstandard später erweitern, immer mit Prüfung von Hand.

## Offene Punkte
- **Fehlermeldung im Evaluationslauf.** Die Ausgabe des Prüflaufs enthält `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`. Das sieht nach einem Versionsproblem beim Aufruf der Anthropic-Schnittstelle aus. Die Prüfung wurde trotzdem als grün gewertet, der Bericht liegt vor. Es ist aber wahrscheinlich, dass die Begriffserweiterung durch Claude im Prüflauf nicht lief. Dann gilt der Wert „mit Erweiterung“ im Bericht eigentlich nur für die Suche ohne Claude-Begriffe. Ich habe die Ursache nicht untersucht. Im JSON-Bericht stehen unter `expansion` der Schlüssel-Status und die Zahl der Anfragen mit Erweiterungsbegriffen. Bitte dort nachsehen.
- **Kein Vergleich möglich.** Der Hit@10-Vergleich meldete „erster Bericht, kein Vergleich“. Das ist beim ersten Lauf normal.
- **Bericht im Pull Request.** Bitte prüfen, ob `reports/eval-*.json` und `.md` im Pull Request enthalten sind.
- **Kandidaten von Hand prüfen.** Der Probelauf des K10plus-Skripts war erreichbar: „Wärmepumpe“ ergab 3 Kandidaten, „Photovoltaik“ keinen. Nichts davon ist im Goldstandard.
- **Folgen eines Schlüssels.** Läuft die Messung mit API-Schlüssel, ruft sie Claude für bis zu 97 Anfragen auf. Die Antworten landen im Zwischenspeicher der Datenbank. Spätere Läufe sind dann besser vergleichbar.

## Empfehlung für die nächste Phase
Zuerst die `proxies`-Fehlermeldung klären und die Messung danach wiederholen. Nur so ist die Baseline „mit Erweiterung“ belastbar. Danach kann die nächste Phase auf den schwachen Gruppen aufbauen, also Werkstoffe und fachfremd umschriebene Anfragen. Jede Änderung wird mit `--compare` gegen den neuen Bericht gemessen.
