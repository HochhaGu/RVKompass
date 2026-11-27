# RVKompass – Diagnose der Rangfolge (P11, Prüfauftrag „Hit@5 = Hit@10“)

## Übersicht

- Zeitpunkt: 2026-11-27T09:42:06+00:00
- Git-Stand: fd6f0d3 (mit ungespeicherten Änderungen)
- Goldstandard: eval/goldstandard.csv (501 Anfragen)
- Suchwege: GND ja, Sprachmodell nein
- Formel: max_plus – register_exact 1, gnd_synonym 0.9, gnd_narrower 0.75, gnd_broader 0.55, gnd_related 0.6, llm_verified 0.5, label_fts 0.4, register_similar 0.4, vector 0.2; Mehrfach-Fund 0.1 je weiterem Pfad, Deckel 0.15

## Hit@5 und Hit@10 auf zwei Wegen

„Referenz“ = derselbe Weg wie scripts/evaluate.py (score_candidates, danach Abschneiden auf 20 Vorschläge). „FastScorer“ = Schnellbewertung des Grids (ungekürzte Rangberechnung).

| Hälfte | Hit@5 Referenz | Hit@10 Referenz | Hit@5 FastScorer | Hit@10 FastScorer | Ränge 6–10 | Ränge gleich |
|---|---|---|---|---|---|---|
| Voller Bestand | 0.4351 | 0.4691 | 0.4351 | 0.4691 | 17 | ja |
| Entwicklungshälfte | 0.4263 | 0.4582 | 0.4263 | 0.4582 | 8 | ja |
| Testhälfte | 0.444 | 0.48 | 0.444 | 0.48 | 9 | ja |
| Teilmenge alt (bis P11) | 0.6082 | 0.6082 | 0.6082 | 0.6082 | 0 | ja |
| Teilmenge neu Technik | 0.4879 | 0.5363 | 0.4879 | 0.5363 | 12 | ja |
| Teilmenge neu andere Fachgebiete | 0.2436 | 0.2756 | 0.2436 | 0.2756 | 5 | ja |

| Teilmenge | Gleichstand 5/6 | erste erwartete Stelle gefunden | davon nur Vektor | abgeschnitten nach Platz 20 | nicht unter den Kandidaten |
|---|---|---|---|---|---|
| alt (bis P11) | 0 | 73 | 5 | 9 | 24 |
| neu Technik | 0 | 168 | 20 | 26 | 80 |
| neu andere Fachgebiete | 0 | 77 | 10 | 24 | 79 |

## Rang der ersten erwarteten Stelle (voller Bestand)

| Rang | Anfragen |
|---|---|
| 1 | 133 |
| 2 | 36 |
| 3 | 27 |
| 4 | 17 |
| 5 | 5 |
| 6 | 6 |
| 7 | 2 |
| 8 | 3 |
| 9 | 3 |
| 10 | 3 |
| 12 | 6 |
| 13 | 3 |
| 14 | 4 |
| 15 | 1 |
| 16 | 1 |
| 17 | 1 |
| 18 | 2 |
| 19 | 4 |
| 20 | 2 |
| nach Platz 20 (abgeschnitten) | 59 |
| nicht unter den Kandidaten | 183 |

- Gleichstand des Scores an der Grenze Platz 5/6: 0 Anfragen
- Erste erwartete Stelle unter den Kandidaten: 318 Anfragen; davon über einen Fundweg mit Normbegriff, Register oder Volltext 283, nur über Vektor-Ähnlichkeit 35
- Anfragen mit mindestens 10 Kandidaten mit Fundweg außer Vektor: 493

## Beispiele

Anfragen, deren erste erwartete Stelle nicht unter den ersten 5 liegt. „mit Fundweg“ = Kandidaten mit mindestens einem Fundweg außer Vektor.

| Anfrage | Rang | ungekürzt | Score erwartet | Fundwege erwartet | Score Platz 5 / 6 / 10 / 11 | Kandidaten (mit Fundweg) |
|---|---|---|---|---|---|---|
| Kraftwerk | 6 | 6 | 0.8554 | gnd_narrower | 0.9421 / 0.8554 / 0.8305 / 0.5735 | 68 (47) |
| Wie Licht aus einer LED entsteht | 6 | 6 | 0.3622 | register_similar | 0.3647 / 0.3622 / 0.3547 / 0.3539 | 39 (36) |
| Warmwasser mit der Sonne machen | 6 | 6 | 0.3613 | register_similar | 0.3677 / 0.3613 / 0.3318 / 0.3274 | 38 (34) |
| Ein eigenes Unternehmen gründen | 6 | 6 | 0.427 | register_similar | 0.4668 / 0.427 / 0.3808 / 0.3808 | 68 (56) |
| Heat Pipe | 6 | 6 | 0.5035 | register_similar | 0.5085 / 0.5035 / 0.4095 / 0.4041 | 36 (27) |
| Kirchliche Jugendarbeit | 6 | 6 | 0.5024 | register_similar | 0.5174 / 0.5024 / 0.4225 / 0.4164 | 64 (37) |
| Prüfen, ob ein Bauteil innen Risse hat | 7 | 7 | 0.3746 | register_similar | 0.4356 / 0.4353 / 0.3346 / 0.3341 | 92 (55) |
| Gender Pay Gap | 7 | 7 | 0.3657 | register_similar | 0.4477 / 0.4393 / 0.3222 / 0.3217 | 50 (46) |
| Wie ein Kühlschrank kalt macht | 8 | 8 | 0.3397 | register_similar | 0.364 / 0.3516 / 0.325 / 0.3206 | 22 (22) |
| Wie Strom vom Kraftwerk ins Haus kommt | 8 | 8 | 0.5297 | register_similar | 0.5387 / 0.5361 / 0.5243 / 0.5217 | 61 (39) |
| Den Boden prüfen, bevor man baut | 8 | 8 | 0.3622 | register_similar | 0.4598 / 0.3717 / 0.3539 / 0.3537 | 124 (93) |
| Was ein Produkt in der Herstellung kostet | 9 | 9 | 0.3575 | register_similar | 0.3636 / 0.3628 / 0.3547 / 0.3545 | 35 (33) |

## Selbstprüfung

- max_plus offline, voller Bestand: Hit@10 0.4691, MRR 0.3377 (P5: Hit@10 0.5979, MRR 0.4649; innerhalb ±0.005: nein)
- legacy offline, voller Bestand: Hit@10 0.4112, MRR 0.2586 (P5/P4: Hit@10 0.5567, MRR 0.3432; P10: Hit@10 0.5464, MRR 0.3431)
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
