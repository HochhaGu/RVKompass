# Phase P7 – Embedding-Modellvergleich

> **Kurz vorweg:** Der Modellvergleich wurde nicht durchgeführt. Das war deine Entscheidung. Es gibt
> keine Messung von RAM-Bedarf, Indexgröße und Antwortzeit, keine ONNX-Prüfung und keine
> Entscheidungstabelle. Alles andere bleibt, wie es war.

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
P7 sollte das beste Embedding-Modell für deutsche Fachsprache finden. Ein Embedding-Modell wandelt
Texte in Zahlenreihen um. So findet RVKompass auch ähnliche Begriffe (Fundweg „Vektor“). Verglichen
werden sollten das heutige Modell `paraphrase-multilingual-MiniLM-L12-v2`, `intfloat/multilingual-e5-base`
und `BAAI/bge-m3`.

Du hast den Vergleich gestrichen, weil er zu viel Rechenzeit kostet. Du hast den Lauf abgebrochen und
die beiden Modelle e5-base und bge-m3 aus dem Cache gelöscht. Quelle ist die Antwortdatei zu P7
(`orchestrator/antworten/P7.md`, Stand 12.11.2026). Sie liegt außerhalb dieser Arbeitskopie. Ihr Inhalt
kam mit dem Auftrag für diesen Lauf. Das Modell MiniLM bleibt.

## Was sich geändert hat
- **Neu: dieser Bericht** (`docs/phasen/phase-7.md`). Warum: P7 braucht einen Abschluss, der die
  Entscheidung festhält.
- **Neu: zwei Evaluationsläufe** in `reports/`. Warum: Sie zeigen, dass die Suche unverändert arbeitet.
  - `eval-2026-11-12-c02a1c8.json` und `.md`: Lauf ohne API-Schlüssel, also ohne Sprachmodell.
  - `eval-2026-11-12-c02a1c8-2.json` und `.md`: Lauf mit Schlüssel. Er ist der maßgebliche Vergleichslauf.
- **Unverändert:** Modell, Index unter `data/`, `app/config.py`, `.env`, Code und Tests.
- **Nicht im Repo:** `scripts/compare_embeddings.py`. Dieses Vergleichsskript aus dem abgebrochenen Lauf
  ist in dieser Arbeitskopie nicht vorhanden. Es wurde nicht neu geschrieben. Sonst hätte P7 ungeplant
  neuen Code bekommen.
- Vom abgebrochenen Lauf lagen keine halbfertigen Ergebnisse vor. In `data/` wurde nichts angelegt,
  geändert oder gelöscht.

Prüfungen:
- `ruff check`: bestanden, keine Funde.
- `pytest`: 477 Tests bestanden (21,10 Sekunden).
- Evaluation: Das Sprachmodell `claude-haiku-5-5` war erreichbar, der Schlüssel war vorhanden. Es gab
  0 neue Aufrufe und 0 Fehler. Alle 194 Antworten kamen aus dem Zwischenspeicher. Es gab 641 Vorschläge,
  davon 538 im Normvokabular (RVK-Register oder GND) bestätigt.
- Hit@10-Vergleich: 0.546 → 0.546, Änderung +0.000. Die Grenze ist eingehalten.

## Kennzahlen vorher/nachher
Alle Werte sind Relevanzmaße für die Rangfolge. Sie sind keine Wahrscheinlichkeiten und keine
Konfidenz.

| Kennzahl | vorher | nachher | Änderung |
|---|---|---|---|
| Hit@1 (richtige Stelle auf Platz 1) | 0.258 | 0.258 | ±0 |
| Hit@5 (unter den ersten 5) | 0.443 | 0.443 | ±0 |
| Hit@10 (unter den ersten 10) | 0.546 | 0.546 | ±0 |
| MRR (Durchschnitt der Kehrwerte der Plätze) | 0.340 | 0.340 | ±0 |
| Hauptgruppen-Hit@5 | – | 0.711 | – |

Zur Lücke: Für „vorher“ liegt bei Hauptgruppen-Hit@5 in den Vorgaben dieses Auftrags kein Wert vor.
Der nachher-Wert stammt aus dem Bericht `eval-2026-11-12-c02a1c8-2.md`.

Die Zahlen sind gleich, weil sich nichts geändert hat. Das war so erwartet. Sie sagen nichts über
andere Embedding-Modelle, denn es wurde keines getestet.

Der Lauf ohne Schlüssel (`eval-2026-11-12-c02a1c8`) zeigt etwas höhere Werte: Hit@10 0.557, Hit@5
0.464, MRR 0.343. Dort arbeitet nur die GND-Erweiterung, ohne Sprachmodell. Das ist ein anderer
Aufbau. Man darf beide Läufe nicht gegeneinander als Verbesserung lesen.

## Was das für die Nutzung bedeutet
Für Nutzer:innen ändert sich nichts. Die Suche liefert dieselben Vorschläge wie nach Phase 6. Das
Modell MiniLM bleibt. Die Oberfläche bleibt gleich.

Ob ein anderes Modell den Fundweg „Vektor“ verbessern würde, wissen wir nicht. Dazu gibt es keine
Messung.

## Offene Punkte
- **Der Modellvergleich selbst.** Es fehlen Treffergüte, RAM-Bedarf (auch im Docker-Container),
  Indexgröße, Antwortzeit und die Entscheidungstabelle.
- **Vorsätze für e5.** Laut Modellbeschreibung erwartet e5-base vor Suchtexten den Vorsatz „query: “
  und vor Indextexten „passage: “. RVKompass setzt sie heute nicht. Ohne sie wäre ein Vergleich
  unfair. Es braucht eine kleine Codeänderung.
- **bge-m3 ist groß.** Laut Modellbeschreibung braucht es deutlich mehr RAM und Rechenzeit als die
  anderen beiden Modelle. Konkrete Zahlen gibt es nicht.
- **ONNX** (ein schlankes Austauschformat für Modelle, das schneller laufen kann) wurde nicht geprüft.
- **Vergleichsskript fehlt.** Für Probelauf, Teilmengen-Vergleich bei mehr als 8 Stunden Rechenzeit
  und Messungen bräuchte es ein Werkzeug wie `scripts/compare_embeddings.py`. Das wäre ein eigener
  Auftrag.
- **Lauf-Vorlage für später.** Man kann einen Index für ein anderes Modell in einem eigenen Ordner
  bauen. Dafür müssen in derselben Shell fünf Variablen gesetzt sein: `EMBEDDING_MODEL_NAME`,
  `EMBEDDINGS_PATH`, `EMBEDDINGS_META_PATH`, `REGISTER_TERM_EMBEDDINGS_PATH` und
  `REGISTER_TERM_META_PATH`. Danach laufen `python scripts/build_embeddings.py` und
  `python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/`. **Achtung:** Fehlt auch nur
  eine Variable, schreibt das Skript in die Standardpfade unter `data/`. Dann wird der laufende Index
  überschrieben. Vorher muss das Modell neu freigegeben werden (Download). Die Namen vorher mit
  `app/config.py` abgleichen. In P7 wurde nichts davon ausgeführt.

## Empfehlung für die nächste Phase
Weiter mit Phase 8: lokaler GND-Abzug, lobid als Rückfallebene, monatlicher RVK-Abgleich.

Den Modellvergleich nur wieder aufnehmen, wenn der Fundweg „Vektor“ in der Praxis zu schwach ist.
Dann zuerst das Vergleichswerkzeug mit Probelauf und Teilmenge bauen lassen. Danach erst Modelle
laden, jeweils nach deiner Freigabe.
