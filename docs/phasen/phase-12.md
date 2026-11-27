# Phase P12 – Goldstandard erweitert, neue Basismessung

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Der Goldstandard ist unsere Prüfsammlung: Suchbegriffe, zu denen du die richtigen RVK-Stellen festgelegt hast. Er wuchs von 97 auf 501 Anfragen, weil du 404 neue ausgearbeitet hast. Auf dieser größeren Basis gibt es jetzt eine neue Messung ohne Sprachmodell. Außerdem sollte sicher sein, dass die alten 97 Anfragen genau wie vorher funktionieren.

## Was sich geändert hat

1. **Neue Anfragen übernommen.**
   - Was: 403 der 404 Zeilen wurden Byte für Byte angehängt. Eine Zeile („Frauenwahlrecht in Österreich“) wurde angepasst: Die Notation „F 4320“ gibt es im RVK-Stand 2026/2 nicht. Ich habe sie aus der Zeile genommen und in der Spalte „anmerkung“ vermerkt. Zwei gültige Notationen bleiben.
   - Warum: Alles, was du festgelegt hast, bleibt unverändert. Nur Unmögliches wird herausgenommen, und zwar sichtbar.
   - Wo: `eval/goldstandard.csv`, Werkzeug `scripts/merge_gold_extension.py`, Logik in `app/services/gold_merge.py`. Es gab keine doppelten Anfragen. Die Datei `goldstandard_offen.csv` wurde nicht gebraucht.

2. **Prüfliste für dich.**
   - Was: Eine Liste mit der fehlenden Notation und 32 Zeilen, bei denen keine Notation in einer Hauptgruppe liegt, die zum Fachgebiet passt (z. B. „Maus“ → AP 99492). Welche Hauptgruppe zu welchem Fachgebiet passt, ist eine Annahme von mir. Sie ändert nichts am Goldstandard.
   - Warum: Du sollst gegenlesen können, ob etwas ein Tippfehler ist.
   - Wo: `docs/goldstandard/pruefliste-2026-11.md`.

3. **Messung nach Teilmengen und Schutz für die alten Anfragen.**
   - Was: Die Auswertung zeigt die Werte jetzt getrennt für „alt“ (97), „neu Technik“ (248) und „neu andere Fachgebiete“ (156). Dazu kommt ein Regressionsschutz: Er prüft, ob die alten 97 Anfragen dieselben Werte wie am Ende von P11 liefern.
   - Warum: Sonst wüssten wir nicht, ob ein schlechterer Gesamtwert an der Suche oder an den schwereren neuen Anfragen liegt.
   - Wo: `app/services/evaluation.py`, `scripts/evaluate.py`, Teilmengen-Größen in `app/config.py`.

4. **Gewichte geprüft, nicht geändert.**
   - Was: Die Gewichte der Formel `max_plus` wurden auf der Entwicklungshälfte neu gesucht. Auf der Testhälfte war das Ergebnis nur bei MRR um 0.0052 besser. Die vorab festgelegte Regel verlangt mindestens 0.01. Deshalb bleiben die alten Gewichte.
   - Warum: Ein so kleiner Gewinn ist zu unsicher. Bei Technik wurde es sogar leicht schlechter.
   - Wo: Regel `accept_p12` in `app/services/scoring_tuning.py`, Option `--retune` in `scripts/tune_scoring.py`.

5. **Relevanzstufen neu gesetzt.**
   - Was: Die Grenzen der fünf Stufen („sehr hoch“ bis „gering“) wurden auf der größeren Basis neu berechnet. Das Verfahren ist dasselbe wie in P11 (fünf etwa gleich große Bereiche). Die Grenzen liegen jetzt tiefer. Auf der Testhälfte sinkt der Anteil passender Treffer von Stufe zu Stufe. Das war in P11 nicht so.
   - Warum: Die alten Grenzen stammten aus nur 97 Anfragen.
   - Wo: `app/config.py` (`SCORE_PROFILES["max_plus"]["tiers"]`). Ein Oberflächentest (`tests/test_search_route.py`) wurde auf die neuen Grenzen angepasst. Die Hilfeseite bleibt richtig.

6. **GND-Sachgruppen: geprüft, nicht übernommen.**
   - Was: Zwei Zuordnungen von Sachgruppe zu Hauptgruppe erfüllten die Schwellen (31.9a → ZN/ZP, 31.9b → ZN). Beide Codes stehen im DNB-Verzeichnis. Auf der Testhälfte änderten sie nichts, also übernehme ich sie nicht.
   - Warum: Ohne messbaren Nutzen bleibt die Zuordnung, wie sie ist. Die Messung beruht auf meiner Annahme zu Fachgebiet und Hauptgruppen.
   - Wo: `scripts/derive_subject_mapping.py`, `app/services/subject_mapping.py`.

7. **Prüfauftrag „Hit@5 = Hit@10“ geklärt.**
   - Was: Auf 501 Anfragen sind die beiden Werte verschieden. 17 Anfragen haben die richtige Stelle auf Platz 6 bis 10. In P11 waren sie nur wegen der 97 alten Anfragen gleich. Ein Rechenfehler war es nicht.
   - Wo: `reports/scoring-diagnose-2026-11-27-fd6f0d3.md`.

8. **Schwachstellen-Analyse.**
   - Was: 266 Anfragen haben keinen Treffer unter den ersten 10. Bei 183 davon taucht die richtige Stelle gar nicht unter den Kandidaten auf.
   - Wo: `scripts/analyze_weak.py`, `app/services/gold_analysis.py`, Bericht `reports/schwachstellen-2026-11-27-fd6f0d3.md`.

9. **Folgen für den monatlichen RVK-Abgleich.**
   - Was: Er liest jetzt alle 501 Anfragen. Ein Test bestätigt, dass er sie richtig liest und markiert. Seine Auswertung dauert etwa 4 Minuten statt etwa 41 Sekunden. Einen echten Probelauf gab es nicht, denn er würde einen RVK-Abzug herunterladen und unter `data/` neu bauen.
   - Wo: `tests/test_rvk_checks.py`.

## Kennzahlen vorher/nachher
Die Werte sind Relevanzmaße, keine Wahrscheinlichkeiten. Hit@k heißt: Der Anteil der Anfragen, bei denen eine richtige Stelle unter den ersten k Vorschlägen steht. MRR ist ein Mittelwert, der frühe richtige Plätze stärker belohnt. Beide liegen zwischen 0 und 1.

Für 501 Anfragen gab es vorher keine Messung. Das ist eine neue Basis. Ein Vergleich mit dem alten Stand wäre nicht fair.

| Kennzahl | vorher | nachher (501 Anfragen) |
|---|---|---|
| Hit@1 | – | 0.266 |
| Hit@5 | – | 0.435 |
| Hit@10 | – | 0.469 |
| MRR | – | 0.338 |

Zum Vergleich die alten 97 Anfragen allein (Regressionsschutz, Toleranz 0.005):

| Kennzahl | Ende P11 | jetzt | Differenz |
|---|---|---|---|
| Hit@1 | 0.3608 | 0.3608 | 0 |
| Hit@5 | 0.6082 | 0.6082 | 0 |
| Hit@10 | 0.6082 | 0.6082 | 0 |
| MRR | 0.4536 | 0.4536 | 0 |

Hit@10 je Teilmenge: alt 0.6082, neu Technik 0.5363, neu andere Fachgebiete 0.2756.

Alle Prüfungen (Gates) sind grün:
- ruff: keine Fehler.
- pytest: 872 Tests bestanden.
- Regressionsschutz: bestanden. Im Schlusslauf steht „gleiche Einstellung: nein“. Das ist gewollt, denn die Stufengrenzen gehören zur Einstellung. Die Rangfolge ändert sich dadurch nicht.
- Hit@10-Vergleich: 0.469 ist eine neue Basismessung, kein Vergleich mit dem alten Stand.

## Was das für die Nutzung bedeutet
- **Die Suche selbst ist unverändert.** Gewichte und Zuordnungen sind dieselben wie am Ende von P11. Die alten Anfragen liefern exakt dieselben Ränge.
- **Die Stufen in der Oberfläche sehen anders aus.** Weil alle Grenzen tiefer liegen, zeigen viele Vorschläge eine höhere Stufe als vorher. Ein Relevanzwert von 0.50 ist jetzt „mittel“, vorher war er „schwach“. Die Stufen bleiben eine Rangordnung. Sie sagen nicht, wie oft eine Stelle passt.
- **Die Treffer sind in Technik besser als in anderen Fächern.** In den neuen, nicht-technischen Fachgebieten steht nur bei gut jeder vierten Anfrage eine richtige Stelle unter den ersten 10. Das ist ein ehrlicher Befund. Er hilft beim Erwartungsmanagement: Die Vorschläge sind ein Einstieg, keine Entscheidung.
- **Der monatliche Abgleich läuft länger.** Rund 4 Minuten für die Auswertung, dazu das Laden des Modells.

## Offene Punkte
- **Erweiterungsdatei löschen.** `eval/goldstandard_erweiterung_2026-11.csv` ließ sich nicht löschen, der Befehl war gesperrt. Der Inhalt steckt vollständig in `eval/goldstandard.csv` (das Skript hat es geprüft). Bitte von Hand löschen.
- **Prüfliste gegenlesen.** Welche Notation ist bei „F 4320“ gemeint? Dazu die 32 Hauptgruppen-Ausreißer ansehen (z. B. „Maus“ → AP 99492, „Scrum“ → QP 360). Aufgefallen ist auch „Zerspanung → ZM 5100“ (Kunststoffeigenschaften, Kunststoffanalyse). Korrekturen am Goldstandard nimmst nur du vor.
- **Annahme zu Fachgebieten.** Die Zuordnung von Fachgebiet zu Hauptgruppen stammt von mir. Sie wirkt nur auf die Prüfliste und auf die Messung der Sachgruppen.
- **Kein echter Probelauf des Abgleichs.** Der Ablauf ist nur über Tests abgesichert.
- **Gewichte sind auf Technik abgestimmt.** Die neuen Fachgebiete liegen deutlich darunter.

## Empfehlung für die nächste Phase
P13 sollte an der größten Schwachstelle ansetzen: Bei 183 Anfragen fehlt die richtige Stelle schon bei den Kandidaten. Reihenfolge nach Wirkung:
1. **Mehr Wege zu Kandidaten (183 Anfragen).** GND-Begriffe auch gegen RVK-Benennungen und Hierarchiepfade prüfen, nicht nur gegen das Register. Ähnliche Stellen über die inhaltliche Ähnlichkeit (Vektor) tiefer holen.
2. **Fehlende GND-Bedeutung (33 Anfragen).** Umschreibungen und zusammengesetzte Wörter finden keinen Normbegriff. Ansatz: Wörter in Teile zerlegen und die Teile suchen.
3. **Bedeutung führt woanders hin (19 Anfragen).** Den Fachfilter stärker nutzen. C3 zeigt, dass die Zuordnung allein nicht reicht.
4. **Richtig gefunden, aber zu weit hinten (30 Anfragen).** Gewichte getrennt für Technik und andere Fachgebiete prüfen, denn C2 wirkte gegenläufig.

Wie immer gilt: erst Vorher/Nachher-Messung mit `scripts/evaluate.py`, dann entscheiden.
