# RVKompass – Fünf Relevanzstufen (P11)

## Übersicht

- Zeitpunkt: 2026-11-27T09:33:31+00:00
- Git-Stand: fd6f0d3 (mit ungespeicherten Änderungen)
- Goldstandard: eval/goldstandard.csv (501 Anfragen)
- Aufteilung: Entwicklung 251, Test 250 (je Anfragetyp abwechselnd, wie in P5)
- Suchwege: GND ja, Sprachmodell nein
- Gewichte: register_exact 1, gnd_synonym 0.9, gnd_narrower 0.75, gnd_broader 0.55, gnd_related 0.6, llm_verified 0.5, label_fts 0.4, register_similar 0.4, vector 0.2; Mehrfach-Fund 0.1 je weiterem Pfad, Deckel 0.15

> Relevanzwerte sind ein Maß für die Passung, keine Wahrscheinlichkeiten. Die Stufen sind nur auf der Entwicklungshälfte abgeleitet; maßgeblich für ihre Belastbarkeit ist die Testhälfte.

## Stufen

Formel max_plus. Grundlage: die ersten 10 Treffer je Anfrage der Entwicklungshälfte. Regel (gesetzte Annahme, nicht gemessen): Eine Stufe beginnt beim kleinsten Score, ab dem mindestens 80 % / 65 % / 50 % / 30 % der Treffer passen („sehr hoch / hoch / mittel / schwach“), mit mindestens 10 Treffern je Stufe. Nur wenn alle vier Grenzen so erreichbar sind, gilt die Anteilsregel; sonst gelten für alle vier Grenzen die Quintile der Scores (reine Rangordnung, nicht kalibriert).

Verfahren: **quantile**

| Stufe | ab Relevanzwert | Anteilsregel hätte ergeben |
|---|---|---|
| sehr hoch | 0.6347 | nicht erreichbar |
| hoch | 0.5526 | 1.2794 |
| mittel | 0.4714 | 0.8171 |
| schwach | 0.3822 | 0.212 |

„passend“ = Hauptgruppe einer erwarteten Notation, „exakt“ = erwartete Notation; Intervall = 95-%-Wilson-Intervall des Anteils passend. Nur im Bericht, nicht in der Oberfläche.

### Testhälfte

| Stufe | Treffer | passend | Anteil passend | 95-%-Intervall | exakt | Anteil exakt | 95-%-Intervall exakt |
|---|---|---|---|---|---|---|---|
| sehr hoch | 544 | 254 | 0.4669 | 0.4254 – 0.5089 | 65 | 0.1195 | 0.0949 – 0.1494 |
| hoch | 380 | 136 | 0.3579 | 0.3113 – 0.4073 | 24 | 0.0632 | 0.0428 – 0.0923 |
| mittel | 532 | 190 | 0.3571 | 0.3176 – 0.3988 | 21 | 0.0395 | 0.026 – 0.0596 |
| schwach | 485 | 109 | 0.2247 | 0.1898 – 0.264 | 10 | 0.0206 | 0.0112 – 0.0375 |
| gering | 552 | 98 | 0.1775 | 0.1479 – 0.2116 | 9 | 0.0163 | 0.0086 – 0.0307 |

- Anteil passend fällt von Stufe zu Stufe: ja
- Klar getrennt (Intervalle überlappen nicht): schwach / mittel, hoch / sehr hoch
- Überlappende Nachbarstufen: gering / schwach, mittel / hoch

### Entwicklungshälfte

| Stufe | Treffer | passend | Anteil passend | 95-%-Intervall | exakt | Anteil exakt | 95-%-Intervall exakt |
|---|---|---|---|---|---|---|---|
| sehr hoch | 500 | 229 | 0.458 | 0.4148 – 0.5018 | 70 | 0.14 | 0.1123 – 0.1732 |
| hoch | 497 | 142 | 0.2857 | 0.2478 – 0.327 | 15 | 0.0302 | 0.0184 – 0.0492 |
| mittel | 500 | 220 | 0.44 | 0.3971 – 0.4838 | 17 | 0.034 | 0.0213 – 0.0538 |
| schwach | 497 | 114 | 0.2294 | 0.1946 – 0.2683 | 10 | 0.0201 | 0.011 – 0.0366 |
| gering | 499 | 119 | 0.2385 | 0.2032 – 0.2778 | 12 | 0.024 | 0.0138 – 0.0416 |

- Anteil passend fällt von Stufe zu Stufe: nein
- Klar getrennt (Intervalle überlappen nicht): schwach / mittel, hoch / sehr hoch
- Überlappende Nachbarstufen: gering / schwach, mittel / hoch
