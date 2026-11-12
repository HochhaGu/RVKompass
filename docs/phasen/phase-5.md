# Phase P5 – Scoring neu

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Bisher sortierte die Suche nach der Vektor-Ähnlichkeit (ein Maß dafür, wie nah zwei Texte inhaltlich beieinander liegen) plus kleiner fester Zuschläge. Die Oberfläche zeigte daraus eine „Konfidenz“ in Prozent. Das war irreführend, denn der Wert ist eine Relevanz und keine Wahrscheinlichkeit.

Ziel von P5 war ein Score, den man zerlegen und erklären kann, und eine Anzeige ohne Prozent. Neue Formeln sollten nur übernommen werden, wenn sie den Stand von P4 halten. Sonst bleibt die alte Rangfolge, und nur die Anzeige ist neu (Option b).

## Was sich geändert hat

- **Score aus benannten Bestandteilen**
  - Was: Jeder Fundweg eines Treffers liefert einen eigenen Beitrag: Direkt (Register), GND-Synonym, GND-Oberbegriff, GND-Unterbegriff, GND-verwandt, LLM-verifiziert (vom Sprachmodell vorgeschlagen und im Normvokabular bestätigt), Vektor, Volltext und „Registerbegriff ähnlich“. Der Beitrag ist Gewicht mal Stärke des Funds.
  - Warum: Jeder Score soll sich in seine Teile zerlegen lassen.
  - Zwei Fundwege sind neu benannt, weil es sie schon gab: „Volltext“ und „Registerbegriff ähnlich“. Letzterer lief bisher fälschlich als „Direkt“.
  - Wo: `app/services/scoring.py` (neu).

- **Drei Formeln und die alte Formel als Bezug**
  - Was: „sum“ (Summe), „max_plus“ (stärkster Fund plus Zuschlag für Mehrfach-Funde) und „sum_capped“ (Summe mit Deckel). Die alte Formel heißt „legacy“ und liefert Zahl für Zahl dasselbe wie in P4.
  - Abweichung vom Auftragstext, bewusst: Der Vektor-Beitrag wird in allen drei Formeln immer addiert und steht nicht im Maximum. So ist es in Günthers Rahmen vorgesehen.
  - Wo: `app/services/scoring.py`, `app/services/ranking.py`. Die Suche arbeitet nun in zwei Schritten: erst Funde sammeln, dann Score rechnen. Die Kandidatenmenge ist unverändert.

- **Einstellungen**
  - Was: Formel, Gewichte, Zuschläge und Stufenschwellen stehen in der Konfiguration. Die Formel lässt sich per `.env` umschalten, z. B. `SCORING_FORMULA=max_plus`. Standard ist `legacy`.
  - Wo: `app/config.py`.

- **Abstimmung der Gewichte**
  - Was: Der Goldstandard (die Liste von Suchbegriffen mit bekannten richtigen Stellen) wurde in zwei Hälften geteilt: 50 Anfragen zum Abstimmen, 47 zum Testen. Ein neues Skript probiert rund 35 000 Gewichtskombinationen durch (Grid-Search) und wertet nur an der Abstimmungshälfte aus. Berichtet wird an der Testhälfte.
  - Wo: `app/services/scoring_tuning.py`, `scripts/tune_scoring.py`. Bericht: `reports/scoring-2026-11-11-c877270-2.md` und `.json`. Der erste Lauf `reports/scoring-2026-11-11-c877270.*` hat dieselben Zahlen und nur weniger Hinweistext.

- **Oberfläche**
  - Was: Die Spalte „Konfidenz“ heißt jetzt „Relevanz“. Sie zeigt ein Stufenwort (hoch / mittel / niedrig) und einen Balken mit einem Segment je Fundweg. Mit der Maus sieht man Name und Wert. Unter der Tabelle steht: „Stufen aus der Evaluation abgeleitet; Balkenlänge relativ zum ersten Vorschlag; keine Wahrscheinlichkeit.“ Prozentzahlen und Ampelfarben gibt es nicht mehr.
  - Wo: `app/templates/_results_table.html`, `app/static/style.css`.

- **Evaluationsbericht**
  - Was: Neu sind der Abschnitt „Scoring“ und die Kennzahlen je Hälfte. Die Pflichtfelder für das Steuerprogramm (`metrics`, `timestamp`) sind unverändert.
  - Wo: `scripts/evaluate.py`, `app/services/evaluation.py`.

- **Tests**
  - Was: 393 Tests, alle grün, alle ohne Netz. Sie prüfen u. a., dass die Teile eines Scores genau dessen Summe ergeben und dass „legacy“ wie P4 rechnet.
  - Wo: `tests/test_scoring.py`, `tests/test_scoring_tuning.py`, `tests/test_tune_scoring_script.py` (neu) und Ergänzungen in fünf bestehenden Testdateien.

## Kennzahlen vorher/nachher
Alle Werte sind Relevanzmaße (Trefferquoten zwischen 0 und 1, bzw. mittlerer reziproker Rang), keine Wahrscheinlichkeiten. Voller Goldstandard, 97 Anfragen, Lauf mit Erweiterung (GND und Sprachmodell). Quelle: `reports/eval-2026-11-12-c877270.json`.

| Kennzahl | vorher (P4) | nachher (P5) |
|---|---|---|
| Hit@1 (richtige Stelle auf Platz 1) | 0.2577 | 0.2577 |
| Hit@5 (unter den ersten 5) | 0.4433 | 0.4433 |
| Hit@10 (unter den ersten 10) | 0.5464 | 0.5464 |
| MRR (je weiter vorn die richtige Stelle, desto höher) | 0.34 | 0.34 |

Die Zahlen sind gleich, wie es bei Option (b) sein muss: Die Rangfolge ist unverändert. Das Gate ist erfüllt (Hit@10 ändert sich um +0.000). Das Sprachmodell lief aus dem Zwischenspeicher: 0 neue Aufrufe, 0 Fehler, 641 Vorschläge, davon 538 im Normvokabular bestätigt. GND-Daten waren vollständig (46 Anfragen mit GND-Bedeutungen wie in P4).

**Die geprüften Formeln auf der Testhälfte (47 Anfragen), ohne Sprachmodell gerechnet**

| Variante | Hit@1 | Hit@10 | MRR | Hauptgruppen-Hit@5 |
|---|---|---|---|---|
| legacy (wie P4) | 0.2128 | 0.5532 | 0.3039 | 0.6809 |
| sum | 0.2766 | 0.5532 | 0.391 | 0.8085 |
| max_plus | 0.2979 | 0.5532 | 0.3979 | 0.8085 |
| sum_capped | 0.2979 | 0.5532 | 0.4017 | 0.8085 |

Auf dem vollen Goldstandard (ebenfalls ohne Sprachmodell) erreicht „legacy“ Hit@10 0.5567 und MRR 0.3432, „max_plus“ Hit@10 0.5979 und MRR 0.4649. Die Nachrechnung von „legacy“ trifft die P4-Werte genau.

## Was das für die Nutzung bedeutet
- Die Suche sortiert wie bisher. Neu ist nur die Anzeige.
- Statt Prozent steht „hoch“, „mittel“ oder „niedrig“. Das ist eine grobe Sortierhilfe und keine Zusage.
- Die Stufen konnten nicht nach der geplanten Regel (hoch ab 80 % passender Treffer, mittel ab 50 %) festgelegt werden, weil diese Anteile nirgends erreicht wurden. Es gilt der Rückfall: Die Scores sind in drei gleich große Gruppen geteilt. „hoch“ beginnt bei Relevanzwert 0.6551, „mittel“ bei 0.5754.
- Die Reihenfolge der Stufen stimmt auch auf der Testhälfte. Passende Hauptgruppe: „hoch“ 47 %, „mittel“ 36 %, „niedrig“ 23 % der Treffer.
- Der Balken zeigt, woher ein Treffer kommt (z. B. GND oder Vektor). So lässt sich prüfen, ob man dem Vorschlag traut.

## Offene Punkte
- **Entscheidung für Günther: Wie ist „Stand von P4“ gemeint?**
  - Keine neue Formel erreicht auf der Testhälfte Hit@10 0.5567. Diese Zahl gilt aber für den vollen Goldstandard. Auf derselben Testhälfte schafft auch das bisherige Verfahren nur 0.5532. Nach dem strengen Wortlaut gilt deshalb Option (b).
  - (a) Option (b) beibehalten, wie jetzt umgesetzt.
  - (b) Den P4-Wert auf derselben Testhälfte als Maßstab zulassen. Dann erfüllen alle drei Formeln die Regel, und `max_plus` würde übernommen. Es wäre eine kleine Folgeänderung: Formel und Gewichte in `app/config.py` eintragen, Stufenschwellen neu ableiten, Evaluation wiederholen.
  - Beim MRR sind alle neuen Formeln deutlich besser (Test: 0.39 bis 0.40 statt 0.30).
- **Sprachmodell nur teilweise gemessen:** Der Lauf mit Sprachmodell kam aus dem Zwischenspeicher von P4. Das Gewicht für „LLM-verifiziert“ bleibt fest bei 0.5 und wurde nicht abgestimmt. Die Formelvergleiche oben sind ohne Sprachmodell gerechnet.
- **Kleiner Goldstandard:** Eine Testanfrage entspricht etwa 0.02 bei Hit@10. Kleinere Unterschiede sind Zufall.
- **Stufen sind grob:** Bei einer neuen Formel oder einem größeren Goldstandard neu ableiten.
- **Doppelte Datei:** `reports/scoring-2026-11-11-c877270.md` stammt aus dem ersten Lauf und wurde nicht gelöscht.

## Empfehlung für die nächste Phase
- Zuerst die Entscheidung (a) oder (b) oben treffen. Bei (b) vorher die Folgeänderung für `max_plus` umsetzen und neu messen.
- Dann P6 wie geplant: Fachfilter-Auswahlfeld, Warnhinweis bei reiner Vektor-Evidenz und Hilfeseite.
- Danach `MIN_COSINE_SCORE` (Mindestähnlichkeit für Vektor-Treffer) aus der Evaluation ableiten.
- Den Goldstandard vergrößern, damit Abstimmung und Stufen belastbarer werden.
