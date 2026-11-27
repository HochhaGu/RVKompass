# RVKompass – Fünf Relevanzstufen (P11)

## Übersicht

- Zeitpunkt: 2026-11-27T08:27:49+00:00
- Git-Stand: 67827ac (mit ungespeicherten Änderungen)
- Goldstandard: eval/goldstandard.csv (97 Anfragen)
- Aufteilung: Entwicklung 50, Test 47 (je Anfragetyp abwechselnd, wie in P5)
- Suchwege: GND ja, Sprachmodell nein
- Gewichte: register_exact 1, gnd_synonym 0.9, gnd_narrower 0.75, gnd_broader 0.55, gnd_related 0.6, llm_verified 0.5, label_fts 0.4, register_similar 0.4, vector 0.2; Mehrfach-Fund 0.1 je weiterem Pfad, Deckel 0.15

> Relevanzwerte sind ein Maß für die Passung, keine Wahrscheinlichkeiten. Die Stufen sind nur auf der Entwicklungshälfte abgeleitet; maßgeblich für ihre Belastbarkeit ist die Testhälfte.

## Stufen

Formel max_plus. Grundlage: die ersten 10 Treffer je Anfrage der Entwicklungshälfte. Regel (gesetzte Annahme, nicht gemessen): Eine Stufe beginnt beim kleinsten Score, ab dem mindestens 80 % / 65 % / 50 % / 30 % der Treffer passen („sehr hoch / hoch / mittel / schwach“), mit mindestens 10 Treffern je Stufe. Nur wenn alle vier Grenzen so erreichbar sind, gilt die Anteilsregel; sonst gelten für alle vier Grenzen die Quintile der Scores (reine Rangordnung, nicht kalibriert).

Verfahren: **quantile**

| Stufe | ab Relevanzwert | Anteilsregel hätte ergeben |
|---|---|---|
| sehr hoch | 0.8291 | nicht erreichbar |
| hoch | 0.5848 | nicht erreichbar |
| mittel | 0.5218 | 0.5847 |
| schwach | 0.4304 | 0.2671 |

„passend“ = Hauptgruppe einer erwarteten Notation, „exakt“ = erwartete Notation; Intervall = 95-%-Wilson-Intervall des Anteils passend. Nur im Bericht, nicht in der Oberfläche.

### Testhälfte

| Stufe | Treffer | passend | Anteil passend | 95-%-Intervall | exakt | Anteil exakt | 95-%-Intervall exakt |
|---|---|---|---|---|---|---|---|
| sehr hoch | 91 | 40 | 0.4396 | 0.3421 – 0.5419 | 14 | 0.1538 | 0.0939 – 0.2418 |
| hoch | 78 | 42 | 0.5385 | 0.4286 – 0.6447 | 6 | 0.0769 | 0.0357 – 0.1578 |
| mittel | 92 | 43 | 0.4674 | 0.3688 – 0.5686 | 4 | 0.0435 | 0.017 – 0.1065 |
| schwach | 106 | 34 | 0.3208 | 0.2395 – 0.4145 | 1 | 0.0094 | 0.0017 – 0.0515 |
| gering | 103 | 25 | 0.2427 | 0.1701 – 0.3338 | 2 | 0.0194 | 0.0053 – 0.0681 |

- Anteil passend fällt von Stufe zu Stufe: nein
- Klar getrennt (Intervalle überlappen nicht): keine
- Überlappende Nachbarstufen: gering / schwach, schwach / mittel, mittel / hoch, hoch / sehr hoch

### Entwicklungshälfte

| Stufe | Treffer | passend | Anteil passend | 95-%-Intervall | exakt | Anteil exakt | 95-%-Intervall exakt |
|---|---|---|---|---|---|---|---|
| sehr hoch | 101 | 60 | 0.5941 | 0.4965 – 0.6847 | 20 | 0.198 | 0.132 – 0.2862 |
| hoch | 98 | 39 | 0.398 | 0.3067 – 0.497 | 4 | 0.0408 | 0.016 – 0.1003 |
| mittel | 100 | 28 | 0.28 | 0.2014 – 0.3749 | 3 | 0.03 | 0.0103 – 0.0845 |
| schwach | 99 | 46 | 0.4646 | 0.3696 – 0.5624 | 4 | 0.0404 | 0.0158 – 0.0993 |
| gering | 100 | 21 | 0.21 | 0.1417 – 0.2998 | 1 | 0.01 | 0.0018 – 0.0545 |

- Anteil passend fällt von Stufe zu Stufe: nein
- Klar getrennt (Intervalle überlappen nicht): gering / schwach
- Überlappende Nachbarstufen: schwach / mittel, mittel / hoch, hoch / sehr hoch
