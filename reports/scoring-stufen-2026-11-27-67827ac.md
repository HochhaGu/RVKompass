# RVKompass – Fünf Relevanzstufen (P11)

## Übersicht

- Zeitpunkt: 2026-11-27T08:25:25+00:00
- Git-Stand: 67827ac (mit ungespeicherten Änderungen)
- Goldstandard: eval/goldstandard.csv (97 Anfragen)
- Aufteilung: Entwicklung 50, Test 47 (je Anfragetyp abwechselnd, wie in P5)
- Suchwege: GND ja, Sprachmodell nein
- Gewichte: register_exact 1, gnd_synonym 0.9, gnd_narrower 0.75, gnd_broader 0.55, gnd_related 0.6, llm_verified 0.5, label_fts 0.4, register_similar 0.4, vector 0.2; Mehrfach-Fund 0.1 je weiterem Pfad, Deckel 0.15

> Relevanzwerte sind ein Maß für die Passung, keine Wahrscheinlichkeiten. Die Stufen sind nur auf der Entwicklungshälfte abgeleitet; maßgeblich für ihre Belastbarkeit ist die Testhälfte.

## Stufen

Formel max_plus. Grundlage: die ersten 10 Treffer je Anfrage der Entwicklungshälfte. Regel (gesetzte Annahme, nicht gemessen): Eine Stufe beginnt beim kleinsten Score, ab dem mindestens 80 % / 65 % / 50 % / 30 % der Treffer passen („sehr hoch / hoch / mittel / schwach“), mit mindestens 10 Treffern ab der Grenze; sonst Quintil der Scores.

| Stufe | ab Relevanzwert | Verfahren |
|---|---|---|
| sehr hoch | 0.8291 | quantile |
| hoch | 0.5848 | quantile |
| mittel | 0.5847 | share |
| schwach | 0.2671 | share |

„passend“ = Hauptgruppe einer erwarteten Notation, „exakt“ = erwartete Notation; Intervall = 95-%-Wilson-Intervall des Anteils passend. Nur im Bericht, nicht in der Oberfläche.

### Testhälfte

| Stufe | Treffer | passend | Anteil passend | 95-%-Intervall | exakt | Anteil exakt |
|---|---|---|---|---|---|---|
| sehr hoch | 91 | 40 | 0.4396 | 0.3421 – 0.5419 | 14 | 0.1538 |
| hoch | 78 | 42 | 0.5385 | 0.4286 – 0.6447 | 6 | 0.0769 |
| mittel | 4 | 1 | 0.25 | 0.0456 – 0.6994 | 0 | 0 |
| schwach | 297 | 101 | 0.3401 | 0.2885 – 0.3957 | 7 | 0.0236 |
| gering | 0 | 0 | – | – | 0 | – |

- Anteil passend fällt von Stufe zu Stufe: nein
- Klar getrennt (Intervalle überlappen nicht): keine
- Überlappende Nachbarstufen: gering / schwach, schwach / mittel, mittel / hoch, hoch / sehr hoch

### Entwicklungshälfte

| Stufe | Treffer | passend | Anteil passend | 95-%-Intervall | exakt | Anteil exakt |
|---|---|---|---|---|---|---|
| sehr hoch | 101 | 60 | 0.5941 | 0.4965 – 0.6847 | 20 | 0.198 |
| hoch | 98 | 39 | 0.398 | 0.3067 – 0.497 | 4 | 0.0408 |
| mittel | 1 | 1 | 1 | 0.2065 – 1 | 0 | 0 |
| schwach | 298 | 94 | 0.3154 | 0.2653 – 0.3703 | 8 | 0.0268 |
| gering | 0 | 0 | – | – | 0 | – |

- Anteil passend fällt von Stufe zu Stufe: nein
- Klar getrennt (Intervalle überlappen nicht): keine
- Überlappende Nachbarstufen: gering / schwach, schwach / mittel, mittel / hoch, hoch / sehr hoch
