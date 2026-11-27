# Phase P11 – Scoring max_plus, LLM aus, Feinschliff Oberfläche

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Die Rangfolge der Vorschläge heißt jetzt „max_plus“. Das ist eine Rechenregel, bei der vor allem der stärkste Fundweg zählt. Sie war schon in Phase 5 besser als die alte Regel „legacy“, wurde aber nicht übernommen. Jetzt ist sie der Standard.

Außerdem ist die Erweiterung durch ein Sprachmodell (KI) ausgeschaltet, und die Texte versprechen sie nicht mehr. Dazu kamen drei kleine Verbesserungen an der Oberfläche: gegliederte Hauptgruppen, die Quelle an jedem Begriffs-Chip und stimmige Zahlen.

Gemessen wurde nur mit dem Goldstandard `eval/goldstandard.csv` (97 Anfragen). Der Goldstandard ist die Liste von Suchbegriffen mit den bekannten richtigen RVK-Stellen.

## Was sich geändert hat

1. **Rangfolge max_plus ist Standard.**
   - Was: Der stärkste Fundweg zählt fast voll. Jeder weitere Fundweg gibt einen kleinen Zuschlag. Die Gewichte sind genau die aus dem P5-Bericht. Ich habe nichts nachgestellt.
   - Warum: max_plus war in P5 auf allen Teilen des Goldstandards besser. Es hat sich jetzt auch auf frischen Daten bestätigt.
   - Zurück zur alten Regel: In der `.env` `SCORING_FORMULA=legacy` setzen.
   - Wo: `app/config.py`, `app/services/scoring.py`, `app/services/ranking.py`

2. **Prüfung des auffälligen Musters „Hit@5 = Hit@10“.**
   - Was: Bei max_plus waren beide Werte gleich. Das sah nach einem Rechenfehler aus. Ich habe beide Werte auf zwei getrennten Wegen berechnet. Sie stimmen für alle 97 Anfragen überein. An Platz 5 und 6 gibt es keine Gleichstände.
   - Ergebnis: Es ist kein Fehler. Die Formel sortiert in deutlichen Stufen. Eine richtige Stelle mit Normbegriff steht meist unter den ersten 5. Sonst steht sie weit hinter Platz 10.
   - Beispiele: „Polymere“ liegt auf Platz 12 (Score 0.91, die ersten zehn haben alle über 1.2). „Mutter“ rückt von Platz 8 auf Platz 3 vor.
   - Wo: `app/services/scoring_tuning.py`, `scripts/tune_scoring.py` (Option `--diagnose`), Bericht `reports/scoring-diagnose-2026-11-27-67827ac.md`

3. **Selbstprüfung gegen P5.**
   - Was: Die Werte aus P5 (Hit@10 0.598, MRR 0.465) kommen heute nicht ganz wieder. Heute sind es Hit@10 0.608 und MRR 0.454.
   - Ursache: Die Daten haben sich geändert (RVK-Abgleich, lokaler GND-Abzug seit P8). Die alte Regel legacy ist unverändert, liefert aber bei 12 Anfragen einen anderen Rang als in P5. Der Code für Kandidaten und Score ist seit P5 gleich.
   - Wo: `reports/scoring-diagnose-2026-11-27-67827ac.md`

4. **Sprachmodell aus, Texte stimmig.**
   - Was: Hilfe und README sprechen nicht mehr von KI-Vorschlägen. Dort steht jetzt: Stichwortsuche und semantische Suche (Suche nach inhaltlicher Ähnlichkeit über Text-Embeddings, das sind Zahlenvektoren für Texte). Die KI-Teile erscheinen in der Hilfe nur, wenn das Sprachmodell eingeschaltet ist.
   - Warum: Das Sprachmodell ist im Auslieferungszustand aus. Die Texte sollen das Wahre sagen.
   - Der Code der KI-Erweiterung ist unverändert und lässt sich per `.env` wieder einschalten.
   - Wo: `app/templates/hilfe.html`, `app/routes/help.py`, `README.md`

5. **Fünf Relevanzstufen gemessen.**
   - Was: Die Stufen „sehr hoch“ bis „gering“ werden jetzt aus den Daten abgeleitet. Das geht per Skript und lässt sich in P12 wiederholen.
   - Ergebnis: Mit 97 Anfragen trennen die Stufen nicht belastbar. Die gesetzten Wunschwerte (80, 65, 50, 30 Prozent passende Treffer) werden nicht erreicht. Deshalb sind es fünf etwa gleich große Bereiche. Das ist nur eine Rangordnung und nicht gegen die richtigen Antworten geprüft.
   - Auf der Testhälfte (Anfragen, die nicht zur Ableitung dienten) überlappen sich alle Nachbarstufen. „Hoch“ hat sogar mehr passende Treffer (54 von 100) als „sehr hoch“ (44 von 100). Nur grob gilt: „sehr hoch“ hat mehr exakte Treffer (15 von 100) als „schwach“ und „gering“ (1 bis 2 von 100).
   - Die Hilfe sagt das jetzt offen.
   - Wo: `app/config.py`, `app/services/scoring.py`, `app/services/scoring_tuning.py`, `app/services/presentation.py`, `scripts/tune_scoring.py` (Option `--tiers-only`), Berichte `reports/scoring-stufen-2026-11-27-67827ac-*.md`

6. **Hauptgruppen nach Fachgebieten gegliedert.**
   - Was: Die Hauptgruppen stehen unter Überschriften wie „S – Mathematik · Informatik“. Jede Gruppe lässt sich einklappen. Gruppen mit einer gewählten Hauptgruppe sind offen. Nach der ersten Suche steht die Zahl der Fundstellen in der Überschrift.
   - Warum: Die lange Liste war für Fachfremde schwer zu überblicken.
   - Es geht ohne JavaScript. JavaScript merkt sich nur, welche Gruppen Sie von Hand geöffnet haben.
   - Woher die Titel kommen: aus der Datenbank. Fehlt dort ein Titel, gilt eine Liste aus der RVK-API (abgerufen am 27.11.2026).
   - Wo: `app/services/presentation.py`, `app/services/rvk_repository.py`, `app/config.py`, `app/templates/_facet_list.html`, `app/static/app.js`, `app/static/style.css`

7. **Quelle an jedem Chip.**
   - Was: Ein Chip ist das kleine Feld mit einem Begriff, der zur Suche dazukam. Er zeigt die Quelle jetzt als Text, zum Beispiel „GND · Synonym“ oder „GND · Oberbegriff“. Das gilt auch für entfernte Begriffe.
   - Warum: Jeder Begriff soll seine Herkunft zeigen, nicht nur über die Farbe.
   - Wo: `app/services/presentation.py`, `app/templates/_chips.html`

8. **Zahlen stimmig („20+“).**
   - Was: Die Liste zeigt höchstens 20 Vorschläge. Größere Zahlen stehen jetzt als „20+“, zum Beispiel im Knopf zum Zurücksetzen der Hauptgruppen. Die Grenze 20 steht in der Konfiguration, nicht fest in den Seiten.
   - Warum: Vorher standen Zahlen da, die die Liste gar nicht zeigen konnte.
   - Wo: `app/config.py`, `app/services/ranking.py`, `app/services/presentation.py`, `app/routes/search.py`, `app/templates/_facet_list.html`, `app/templates/_results_table.html`

Dazu kamen neue Tests für alle Punkte (`tests/`).

## Kennzahlen vorher/nachher
Hit@1, Hit@5 und Hit@10 sagen, wie oft eine richtige Stelle auf Platz 1, unter den ersten 5 oder unter den ersten 10 steht. MRR ist der Mittelwert aus 1 geteilt durch den Rang der ersten richtigen Stelle. Das sind Relevanzmaße über 97 Testanfragen. Es sind keine Wahrscheinlichkeiten und keine Sicherheit für einzelne Vorschläge.

| Kennzahl | Vorher (legacy) | Nachher (max_plus) | Änderung |
|---|---|---|---|
| Hit@1 | 0.258 | 0.361 | +0.103 |
| Hit@5 | 0.443 | 0.608 | +0.165 |
| Hit@10 | 0.546 | 0.608 | +0.062 |
| MRR | 0.343 | 0.454 | +0.111 |
| Hauptgruppen-Treffer | – | – | – |

Hinweise:
- Die Prüfung verlangte, dass Hit@10 höchstens um 0.02 sinkt. Es ist gestiegen.
- Ein Kontrolllauf der alten Regel auf den heutigen Daten ergab Hit@10 0.557. Auch dagegen ist max_plus besser.
- Auf der Testhälfte, an der die Gewichte nie abgestimmt wurden, ist max_plus in allen Kennzahlen besser.
- Hit@5 und Hit@10 sind bei max_plus gleich. Das ist kein Fehler (siehe oben). Für Vergleiche sind Hit@1, Hit@5 und MRR aussagekräftiger.
- Die Evaluation lief ohne Sprachmodell: 0 Aufrufe, 0 Fehler.
- Berichte: `reports/eval-2026-11-27-67827ac.md` (legacy heute) und `reports/eval-2026-11-27-67827ac-2.md` (max_plus).

Alle Prüfungen sind grün: `ruff check` ohne Fehler, `pytest` mit 794 bestandenen Tests.

## Was das für die Nutzung bedeutet
- Die Vorschläge stehen besser sortiert. Die richtige Stelle liegt öfter ganz vorn.
- Wer fachfremd umschreibt, findet teils schlechter. Wird die richtige Stelle nur über inhaltliche Ähnlichkeit gefunden, rutscht sie hinter Stellen mit Normbegriff. Die Warnhinweise bei reiner Ähnlichkeit bleiben deshalb wichtig.
- Die Stufe „sehr hoch“ heißt nur: weit vorn in der Rangordnung. Sie heißt nicht, dass die Stelle wahrscheinlich stimmt.
- Die Hilfe verspricht keine KI mehr. Das entspricht dem Ist-Zustand.
- Die Hauptgruppen sind übersichtlicher und per Tastatur bedienbar.
- Jeder Chip zeigt, woher der Begriff stammt.

## Offene Punkte
- **Titel der Hauptgruppen prüfen.** Ich konnte die Datenbank nicht direkt abfragen. `sqlite3` fehlt, und eigene Abfragen waren gesperrt. Deshalb steht für alle Einträge die RVK-API-Liste als Rückfall in `app/config.py`. Im Betrieb haben die Datenbank-Titel Vorrang. Bitte einmal im laufenden Betrieb ansehen, ob die Überschriften stimmen.
- **Einige Anfragen sind schlechter geworden.** Dort wird die richtige Stelle nur über Ähnlichkeit gefunden:
  - „Strom aus Sonnenlicht“: Platz 4 → nicht mehr unter den ersten 20
  - „Messunsicherheit bestimmen“: Platz 2 → nicht mehr unter den ersten 20
  - „Bank“: Platz 3 → Platz 20
- **Relevanzstufen nicht geprüft.** Sie sind nur eine Rangordnung. Mit mehr Anfragen neu ableiten.
- **Satz in der Hilfe zu den Stufen.** Er gilt für die fünf gleich großen Bereiche. Ändert sich die Ableitung, muss der Satz angepasst werden.
- **Datenstand.** Die P5-Werte sind nicht exakt reproduzierbar, weil sich die Daten geändert haben.
- **Datei `docs/design.md`** ist nicht angepasst. Es gibt jetzt einklappbare Buchstabengruppen in der Facette und „Quelle · Typ“ am Chip.

## Empfehlung für die nächste Phase
In P12 mit 501 Anfragen sollte Folgendes neu abgeleitet werden:
1. Die Gewichte von max_plus per Grid (`python scripts/tune_scoring.py`).
2. Die fünf Stufen (`--tiers-only`). Danach die Werte in `app/config.py` eintragen und den Hilfe-Satz prüfen.
3. Die Diagnose (`--diagnose`). Sie zeigt, ob Hit@5 gleich Hit@10 bleibt und wie viele richtige Stellen nur über Ähnlichkeit gefunden werden.
4. Die Zuordnung von GND-Sachgruppen zu RVK-Hauptgruppen in der Konfiguration.
5. Die Entscheidung zwischen legacy und max_plus, falls sich das Bild dreht.

Außerdem: Prüfen, ob ein etwas höheres Gewicht für die Ähnlichkeitssuche die verschlechterten Anfragen verbessert. Das nur mit Messung. Danach `docs/design.md` anpassen.
