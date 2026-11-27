# RVKompass – Diagnose der Rangfolge (P11, Prüfauftrag „Hit@5 = Hit@10“)

## Übersicht

- Zeitpunkt: 2026-11-27T08:24:02+00:00
- Git-Stand: 67827ac (mit ungespeicherten Änderungen)
- Goldstandard: eval/goldstandard.csv (97 Anfragen)
- Suchwege: GND ja, Sprachmodell nein
- Formel: max_plus – register_exact 1, gnd_synonym 0.9, gnd_narrower 0.75, gnd_broader 0.55, gnd_related 0.6, llm_verified 0.5, label_fts 0.4, register_similar 0.4, vector 0.2; Mehrfach-Fund 0.1 je weiterem Pfad, Deckel 0.15

## Hit@5 und Hit@10 auf zwei Wegen

„Referenz“ = derselbe Weg wie scripts/evaluate.py (score_candidates, danach Abschneiden auf 20 Vorschläge). „FastScorer“ = Schnellbewertung des Grids (ungekürzte Rangberechnung).

| Hälfte | Hit@5 Referenz | Hit@10 Referenz | Hit@5 FastScorer | Hit@10 FastScorer | Ränge 6–10 | Ränge gleich |
|---|---|---|---|---|---|---|
| Voller Bestand | 0.6082 | 0.6082 | 0.6082 | 0.6082 | 0 | ja |
| Entwicklungshälfte | 0.64 | 0.64 | 0.64 | 0.64 | 0 | ja |
| Testhälfte | 0.5745 | 0.5745 | 0.5745 | 0.5745 | 0 | ja |

## Rang der ersten erwarteten Stelle (voller Bestand)

| Rang | Anfragen |
|---|---|
| 1 | 35 |
| 2 | 8 |
| 3 | 9 |
| 4 | 5 |
| 5 | 2 |
| 12 | 2 |
| 13 | 1 |
| 19 | 1 |
| 20 | 1 |
| nach Platz 20 (abgeschnitten) | 9 |
| nicht unter den Kandidaten | 24 |

- Gleichstand des Scores an der Grenze Platz 5/6: 0 Anfragen
- Erste erwartete Stelle unter den Kandidaten: 73 Anfragen; davon über einen Fundweg mit Normbegriff, Register oder Volltext 68, nur über Vektor-Ähnlichkeit 5
- Anfragen mit mindestens 10 Kandidaten mit Fundweg außer Vektor: 96

## Beispiele

Anfragen, deren erste erwartete Stelle nicht unter den ersten 5 liegt. „mit Fundweg“ = Kandidaten mit mindestens einem Fundweg außer Vektor.

| Anfrage | Rang | ungekürzt | Score erwartet | Fundwege erwartet | Score Platz 5 / 6 / 10 / 11 | Kandidaten (mit Fundweg) |
|---|---|---|---|---|---|---|
| Polymere | 12 | 12 | 0.9106 | gnd_related, label_fts, register_similar | 1.2384 / 1.2272 / 1.2111 / 0.9936 | 95 (89) |
| Mitarbeitende gut führen | 12 | 12 | 0.3557 | register_similar | 0.3956 / 0.3855 / 0.3637 / 0.3578 | 61 (30) |
| Wie Firmen ihre Abläufe verbessern | 13 | 13 | 0.3575 | register_similar | 0.4644 / 0.4614 / 0.3692 / 0.3603 | 86 (61) |
| Stahl | 19 | 19 | 0.4986 | label_fts | 0.6152 / 0.6049 / 0.5874 / 0.587 | 116 (94) |
| Bank | 20 | 20 | 0.6428 | label_fts | 1.2559 / 1.2428 / 0.995 / 0.8787 | 53 (49) |
| Automatisierungstechnik | – | 22 | 0.6534 | label_fts | 0.8007 / 0.7931 / 0.7128 / 0.7099 | 118 (103) |
| Zugversuch an Stahlproben | – | 25 | 0.3539 | register_similar | 0.5431 / 0.5424 / 0.4338 / 0.4168 | 58 (29) |
| Halbleiter | – | 26 | 0.4844 | label_fts | 1.189 / 0.9342 / 0.619 / 0.6051 | 119 (95) |
| Messunsicherheit bestimmen | – | 31 | 0.1048 | nur Vektor | 0.3609 / 0.3587 / 0.3508 / 0.3489 | 34 (30) |
| Spannung | – | 34 | 0.4311 | label_fts | 0.5888 / 0.5876 / 0.5698 / 0.5656 | 60 (53) |
| Verschleiß an Zahnradgetrieben | – | 34 | 0.1121 | nur Vektor | 0.5082 / 0.5013 / 0.4085 / 0.4067 | 54 (27) |
| Wie ein Motor im Auto funktioniert | – | 38 | 0.1074 | nur Vektor | 0.5291 / 0.5262 / 0.5114 / 0.4975 | 53 (25) |

## Selbstprüfung

- max_plus offline, voller Bestand: Hit@10 0.6082, MRR 0.4536 (P5: Hit@10 0.5979, MRR 0.4649; innerhalb ±0.005: nein)
- legacy offline, voller Bestand: Hit@10 0.5567, MRR 0.3461 (P5/P4: Hit@10 0.5567, MRR 0.3432; P10: Hit@10 0.5464, MRR 0.3431)
- legacy: Anfragen mit anderem Rang als im P4/P5-Bezugsbericht (reports/eval-2026-11-11-c784d47-4.json): 12

| Anfrage | Rang P4/P5 | Rang heute |
|---|---|---|
| Wie Kräfte in Maschinen übertragen werden | 11 | 15 |
| Mutter | 6 | 8 |
| Smart Grid | 16 | – |
| Strom aus Sonnenlicht | 3 | 4 |
| Ermüdung | – | 8 |
| Machine Learning | 9 | 5 |
| BIM | 14 | – |
| Messunsicherheit bestimmen | 5 | 2 |
| Verschleiß an Zahnradgetrieben | 12 | 11 |
| Fernwärmenetze in Städten | 8 | 7 |
| Keramik | 5 | 2 |
| Echtzeitbetriebssysteme für eingebettete Systeme | 6 | 5 |
