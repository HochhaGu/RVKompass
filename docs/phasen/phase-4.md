# Phase P4 – LLM-Erweiterung, verifiziert

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Bisher konnte das Sprachmodell (die KI, hier Claude Haiku 5.5) Suchbegriffe ungeprüft in die Suche einbringen. Ab P4 darf es nur noch **vorschlagen**. Gesucht wird ein Vorschlag nur, wenn er im RVK-Register oder in der GND (der Normdatei für Schlagwörter) steht. Alle anderen Vorschläge werden grau angezeigt und nicht gesucht. So bleibt jeder Treffer nachvollziehbar.

## Was sich geändert hat

- **Bibliothek und Modell**
  - Was: Die Anthropic-Bibliothek (das Programmstück, das mit der KI spricht) ist von 0.34.2 auf 2.8.0 angehoben. Die alte Version war die Ursache der Fehlermeldung „proxies“ aus P3. Als Modell dient jetzt `claude-haiku-5-5`, laut Anthropic-Modellübersicht das aktuelle Haiku (geprüft am 11.11.2026).
  - Warum: Ohne neue Bibliothek lief kein Echttest.
  - Wo: `requirements.txt`, `app/config.py`. Dort stehen auch Zeitlimit (10 Sekunden), 1 Wiederholung, Denktiefe „low“ und höchstens 8 Vorschläge.

- **Neue Anfrage an das Sprachmodell**
  - Was: Der Prompt (die Anweisung an die KI) nennt den Kontext „Bibliothek mit technischem Schwerpunkt, Nutzer ist fachfremd.“ Die KI liefert 3 bis 8 Begriffe in GND-üblicher Form. Zu jedem gehören eine Beziehung (Synonym, Oberbegriff, Unterbegriff, verwandt, Fachkontext) und eine Begründung mit höchstens 12 Wörtern.
  - Die Antwortform wird über ein festes Schema erzwungen. Ein Leser im Code fängt Abweichungen trotzdem ab.
  - Zwischenspeicher und Tageslimit bleiben. Jeder Fehler heißt: keine Erweiterung. Im Protokoll steht nur die Fehlerart, nie der Schlüssel.
  - Warum: Begründungen machen die Vorschläge für Fachfremde lesbar.
  - Wo: `app/services/query_expansion.py`.

- **Prüfung gegen das Normvokabular**
  - Was: Jeder Vorschlag wird zuerst im RVK-Register gesucht, genau und in üblichen Schreibvarianten. Steht er dort nicht, wird lobid-gnd (der Online-Zugang zur GND) gefragt. Dort zählt er nur, wenn Vorzugsname oder Namensvariante genau passt. Fällt lobid aus, gilt der Vorschlag als nicht geprüft. Die Suche läuft weiter.
  - Warum: Leitprinzip „Kontrolliertes Vokabular zuerst“.
  - Wo: `app/services/expansion/llm.py` (neu), `app/services/rvk_repository.py`, `app/services/expansion/gnd.py`.

- **Datenmodell**
  - Was: Der vorläufige Wert `llm_unverified` aus P2 ist entfernt. In der Suche gibt es LLM-Begriffe nur noch als „llm_verified“. Neu ist der Vorschlag mit Begründung und Prüfergebnis (`LlmSuggestion`).
  - Warum: Ungeprüfte Begriffe dürfen nie in die Suche.
  - Wo: `app/models.py`.

- **Suche**
  - Was: Geprüfte LLM-Begriffe werden unter ihrer Schreibweise im Normvokabular gesucht, per Vektorsuche, über den gleichen Registerbegriff und über die GND-Nummer. Einen Zuschlag auf den Relevanzwert gibt es nicht (`llm_bonus` = 0). Ohne LLM-Begriffe ist das Ergebnis wie in P3.
  - Warum: Ein Zuschlag darf erst nach einer Messung kommen.
  - Wo: `app/services/ranking.py`.

- **Suchseite**
  - Was: Die Zeile „Erweitert um: …“ ist ersetzt durch den Block „Vorschläge des Sprachmodells“. Geprüfte Begriffe stehen mit Beziehung, Begründung und Fundort. Nicht geprüfte stehen grau mit „nicht im Normvokabular gefunden – nicht gesucht“. Die Spalte „Konfidenz“ bleibt bis P5.
  - Wo: `app/routes/search.py`, `app/templates/_results_table.html`, `app/static/style.css`.

- **Evaluation (Messlauf)**
  - Was: Gemessen werden vier Varianten: ohne Erweiterung, nur GND, nur LLM, GND+LLM. Der Bericht zeigt jetzt auch Hit@10 je Anfragetyp und Variante, dazu einen Abschnitt zum Echttest.
  - Warum: Nur so lässt sich messen, was das Sprachmodell bringt.
  - Wo: `scripts/evaluate.py`, `app/services/evaluation.py`.

- **Standard „Sprachmodell aus“**
  - Was: `ENABLE_QUERY_EXPANSION` steht jetzt standardmäßig auf `false`. Einschalten geht über `.env`. Code und Tests bleiben vollständig erhalten.
  - Warum: Günthers Regel verlangte beim Anfragetyp „Fachfremd umschrieben“ (21 Anfragen) mindestens +0.05 bei Hit@10 gegenüber „nur GND“. Gemessen wurden 0.3333 gegen 0.3333, also 0.00. Die Bedingung ist nicht erfüllt. Die Zahl für „nur GND“ stammt aus dem Bericht `c784d47` (gleicher Programmstand, ohne Schlüssel). Der Abschlusslauf bestätigt sie.
  - Wo: `app/config.py`, `tests/test_config.py`.

- **Tests**
  - Was: Neue Tests, insgesamt 332. Alle laufen ohne Netz. KI und lobid sind Attrappen.
  - Wo: `tests/test_query_expansion.py`, `tests/test_llm_verification.py`, `tests/test_config.py` (neu) und Ergänzungen in weiteren Testdateien.

## Kennzahlen vorher/nachher
Die Kennzahlen sind Relevanzmaße, keine Wahrscheinlichkeiten. Hit@N heißt: Die erwartete Systemstelle steht unter den ersten N Treffern. MRR ist der Durchschnitt von 1 geteilt durch den Rang des ersten richtigen Treffers.

| Kennzahl | vorher (Ende P3) | nachher: Echttest GND+LLM | Abschlusslauf, Sprachmodell aus |
|---|---|---|---|
| Hit@1 | 0.258 | 0.258 | 0.258 |
| Hit@5 | 0.464 | 0.443 | 0.464 |
| Hit@10 | 0.557 | 0.546 | 0.557 |
| MRR | 0.343 | 0.340 | 0.343 |
| Hauptgruppen-Treffer | – | – | – |

- **Echttest:** Modell `claude-haiku-5-5`, Bibliothek anthropic 2.8.0, Schlüssel vorhanden, 0 Fehler. Das Modell schlug 641 Begriffe vor, 538 davon standen im Normvokabular. Die übrigen wurden grau angezeigt.
- **Ergebnis:** Das Sprachmodell hat die Suche nicht besser gemacht. Hit@10 sinkt um 0.010 (eine Anfrage von 97). Das liegt innerhalb der erlaubten 0.02, ist aber ein echter Rückgang.
- **Abschlusslauf:** Mit dem neuen Standard „aus“ liegen die Werte wieder genau auf dem Stand von P3.
- Alle Berichte liegen in `reports/` (`eval-2026-11-11-c784d47*.json` und `.md`).

## Was das für die Nutzung bedeutet
- Im Normalbetrieb sucht RVKompass wie am Ende von P3: direkt und über die GND. Die Antwortzeit bleibt niedrig. Mit Sprachmodell wäre sie etwa fünfmal so hoch.
- Wird das Sprachmodell eingeschaltet, kann es keine Fantasiebegriffe in die Suche bringen. Jeder Treffer über einen LLM-Begriff führt auf einen Eintrag im Register oder in der GND. Graue Begriffe sind Denkanstöße, keine Suchbegriffe.

## Offene Punkte
- **Erneut messen in P5:** Nach dem neuen Scoring die LLM-Variante einmal mitmessen (`ENABLE_QUERY_EXPANSION=true` und Schlüssel setzen, dann `python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/`). Die Vorschläge liegen im Zwischenspeicher, das kostet kaum neue Aufrufe.
- **Vektorsuche für LLM-Begriffe:** Bei „Ermüdung“ und „Machine Learning“ scheinen breite Begriffe wie „Informatik“ Rauschen zu bringen. Ein Versuch nur mit exakten Register- und GND-Wegen ist sinnvoll.
- **Zuschlag für LLM-Treffer:** steht auf 0. Ein Wert nur mit Messung.
- **Altlast:** Die Anzeige „Konfidenz“ in Prozent bleibt bis P5.

## Empfehlung für die nächste Phase
P5 baut das neue Scoring und die neue Oberfläche ohne Sprachmodell. Danach einmal mit eingeschaltetem Sprachmodell messen und nach Günthers Regel neu entscheiden. Bringt es bei den umschriebenen Anfragen mindestens +0.05 bei Hit@10, wird es wieder eingeschaltet. Sonst bleibt es aus.
