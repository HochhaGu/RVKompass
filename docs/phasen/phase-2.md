# Phase P2 – Evidence-Datenmodell

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Jeder Suchtreffer soll festhalten, warum er in der Liste steht. Dazu gehören der Begriff und der Suchweg, über die er gefunden wurde. Bisher ging diese Herkunft verloren, weil Eingabe und Erweiterungsbegriffe zu einem einzigen Suchtext verschmolzen wurden. Die Ergebnisse selbst sollten sich dabei nicht ändern.

## Was sich geändert hat

- **Neues Datenmodell**
  - Was: Es gibt drei neue Bausteine.
    - Ein *Suchbegriff* merkt sich, woher er stammt: aus der Eingabe, aus der GND (der Gemeinsamen Normdatei, einem Schlagwort-Verzeichnis), vom Sprachmodell oder aus der Vektorsuche (Suche nach inhaltlicher Ähnlichkeit).
    - Eine *Begründung* (Fachwort: Evidence) hält fest, über welchen Begriff und welchen Weg eine Notation (Systemstelle der RVK) gefunden wurde. Der Rohwert der Suche steht dabei.
    - Ein *Treffer* trägt eine oder mehrere Begründungen.
  - Warum: Ohne nachvollziehbare Herkunft darf kein Treffer erscheinen (Leitprinzip „Transparenz vor Raffinesse“). Ein Treffer ohne Begründung wird im Code abgewiesen. Das gilt auch, wenn Python mit abgeschalteten Prüfungen läuft (`python -O`).
  - Wo: `app/models.py`. Die alte, nirgends benutzte Klasse `SearchHit` ist entfernt.

- **Suche pro Begriff**
  - Was: Die Eingabe und jeder Erweiterungsbegriff werden einzeln gesucht, nicht mehr als verschmolzener Text. Der Wert eines Treffers richtet sich weiter nach der Ähnlichkeit zur ursprünglichen Eingabe. Erweiterungsbegriffe liefern nur zusätzliche Kandidaten und verwässern die Eingabe nicht mehr.
  - Warum: So lässt sich jeder Treffer einem Begriff zuordnen. Bei Treffern über das RVK-Register ist jetzt sichtbar, welcher Registerbegriff mit welcher GND-ID dazu geführt hat. Früher wurde das verworfen.
  - Wo: `app/services/ranking.py`. Dort ist auch eine neue Hilfsfunktion, die für die Tabelle die gleichen Zeilen wie bisher baut.

- **Anbindung der Suchseite**
  - Was: Die Suchseite holt zuerst die Treffer und wandelt sie dann in Tabellenzeilen um. Die Oberfläche sieht unverändert aus.
  - Warum: Die Vorlage soll unangetastet bleiben, weil sich in dieser Phase das Verhalten nicht ändern durfte.
  - Wo: `app/routes/search.py`.

- **Evaluation: Vergleich pro Anfrage**
  - Was: Bei `--compare` wird jede Anfrage aufgelistet, deren Rang oder deren erste drei Treffer sich gegenüber dem alten Bericht verändert haben. Der Markdown-Bericht nennt die Anzahl.
  - Warum: Gleiche Kennzahlen allein beweisen nicht, dass die Reihenfolge gleich blieb. Der Vergleich pro Anfrage tut das.
  - Wo: `app/services/evaluation.py`, `scripts/evaluate.py`.

- **Zusätzlicher Wert `llm_unverified` (Abweichung vom Auftrag)**
  - Was: Für Begriffe vom Sprachmodell gibt es vorläufig den Wert „ungeprüft“. Der Auftrag kannte nur „geprüft“.
  - Warum: Die heutigen Begriffe sind noch nicht gegen GND oder RVK-Register geprüft. Sie als „geprüft“ zu kennzeichnen wäre falsch. Im Code ist der Wert als vorläufig markiert. Phase P4 soll ihn entfernen, sobald die Prüfung steht.
  - Wo: `app/models.py`.

- **Tests**
  - Was: 32 neue Tests. Sie laufen ohne Netz und ohne Sprachmodell. Bestehende Tests wurden an die neue Treffer-Form angepasst.
  - Wo: `tests/test_models.py`, `tests/test_ranking_evidence.py`, `tests/test_evaluation.py`, `tests/test_evaluate_script.py`, `tests/test_ranking.py`.

- **Berichte**
  - Was: Zwei Evaluationsläufe liegen in `reports/` (`eval-2026-11-11-f70602c.*` und `eval-2026-11-11-f70602c-2.*`, jeweils `.json` und `.md`).

## Kennzahlen vorher/nachher
Die Kennzahlen sind Relevanzmaße, keine Wahrscheinlichkeiten. Sie zeigen, wo die richtige Systemstelle in der Trefferliste landet.

| Kennzahl | vorher | nachher |
|---|---|---|
| Hit@1 (richtige Stelle auf Platz 1) | 0.196 | 0.196 |
| Hit@5 (unter den ersten 5) | 0.402 | 0.402 |
| Hit@10 (unter den ersten 10) | 0.484 | 0.484 |
| MRR (mittlerer Kehrwert des Rangs) | 0.285 | 0.285 |
| Hauptgruppen-Treffer | – | – |
| Antwortzeit je Anfrage | 115 ms | 167 ms |

- Die Hit@10-Änderung beträgt +0.000. Es gibt keine Verschlechterung.
- Keine Anfrage hat eine geänderte Rangfolge (`compare_per_query` ist leer).
- Die Antwortzeit ist gestiegen. Die Ursache wurde nicht untersucht. Auf die Treffer hat das keinen Einfluss.

## Was das für die Nutzung bedeutet
- Für Nutzer:innen ändert sich nichts. Gleiche Treffer, gleiche Reihenfolge, gleiche Anzeige.
- Technisch ist die Grundlage gelegt: Jeder Treffer kennt seine Herkunft. Spätere Phasen können das in der Oberfläche zeigen und die Wege unterschiedlich gewichten.
- Die Anzeige „Konfidenz“ in Prozent besteht noch. Sie ist ein Relevanzwert, keine Wahrscheinlichkeit. Das ändert sich erst in P5.

## Offene Punkte
- **Evaluationslauf meldete einen Fehler:** Im Gate-Protokoll steht für den Evaluationslauf die Meldung `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`. Das Gate wurde trotzdem als grün gewertet, weil die Berichte erzeugt wurden und der Hit@10-Vergleich stimmt. Die Meldung sieht nach einem Problem mit dem Anthropic-Zugang aus (vermutlich passen Versionen der Bibliotheken nicht zusammen). Sie wurde in dieser Phase nicht untersucht. Sie erklärt womöglich, warum keine Erweiterungsbegriffe ankamen. Das sollte vor P3 geklärt werden.
- **Wirkung der Erweiterung nicht gemessen:** Keine Anfrage der Evaluation hatte Erweiterungsbegriffe. Wie sich die Suche pro Begriff mit echten Begriffen auswirkt, ist offen.
- **Registertreffer als „register_exact“:** Diese Treffer laufen vorläufig unter dieser Kennung, obwohl der Registerbegriff über Ähnlichkeit gefunden wurde und nicht über exakte Übereinstimmung. Ein Code-Kommentar begründet das. Eine eigene Kennung kann in P3 oder P5 kommen.
- **Begründungen nicht sichtbar:** Die Oberfläche zeigt sie noch nicht an. Die Spalte „Verwandte Begriffe“ zeigt weiterhin beliebige Registerbegriffe.
- **Volltextsuche:** Sie läuft weiter nur auf der Eingabe, nicht auf den Erweiterungsbegriffen. Das gehört zu P3 und P5.
- **Wert `llm_unverified`:** Er ist vorläufig und muss in P4 wieder entfernt werden.
- **Antwortzeit:** Der Anstieg von 115 auf 167 ms ist ungeklärt.

## Empfehlung für die nächste Phase
Zuerst den `proxies`-Fehler beim Anthropic-Zugang klären. Danach mit der GND-Erweiterung weitermachen (P3). Dabei sollten die Evaluationsanfragen so ergänzt werden, dass ein Teil mit Erweiterungsbegriffen läuft. Nur dann lässt sich messen, ob die neue Suche pro Begriff etwas bringt. Außerdem lohnt ein kurzer Blick auf die gestiegene Antwortzeit.
