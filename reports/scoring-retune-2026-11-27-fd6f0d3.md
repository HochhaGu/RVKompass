# RVKompass – Gewichte neu abgestimmt (P12)

## Übersicht

- Zeitpunkt: 2026-11-27T09:25:15+00:00
- Git-Stand: fd6f0d3 (mit ungespeicherten Änderungen)
- Goldstandard: eval/goldstandard.csv (501 Anfragen)
- Aufteilung: Entwicklung 251, Test 250 (je Anfragetyp abwechselnd, wie in P5)
- Suchwege: GND ja, Sprachmodell nein
- Formel: max_plus, Bewertungen im Grid: 17496

> Relevanzmaße, keine Wahrscheinlichkeiten. Gewichte nur auf der Entwicklungshälfte abgestimmt (Ziel MRR, dann Hit@10, dann Nähe zu den aktuellen Gewichten); entschieden wird auf der Testhälfte.

## Gewichte

- aktuell (Profil in app/config.py): register_exact 1, gnd_synonym 0.9, gnd_narrower 0.75, gnd_broader 0.55, gnd_related 0.6, llm_verified 0.5, label_fts 0.4, register_similar 0.4, vector 0.2; Mehrfach-Fund 0.1 je weiterem Pfad, Deckel 0.15
- bestes Grid-Ergebnis: register_exact 1, gnd_synonym 0.9, gnd_narrower 0.6, gnd_broader 0.55, gnd_related 0.75, llm_verified 0.5, label_fts 0.4, register_similar 0.4, vector 0.4; Mehrfach-Fund 0 je weiterem Pfad, Deckel 0.15

## Kennzahlen

| Teil | Variante | Hit@1 | Hit@5 | Hit@10 | MRR | Hauptgruppen-Hit@5 |
|---|---|---|---|---|---|---|
| Entwicklungshälfte | aktuell | 0.2629 | 0.4263 | 0.4582 | 0.3316 | 0.7291 |
| Entwicklungshälfte | Grid | 0.2789 | 0.4343 | 0.4701 | 0.3427 | 0.7331 |
| Testhälfte | aktuell | 0.268 | 0.444 | 0.48 | 0.3438 | 0.704 |
| Testhälfte | Grid | 0.276 | 0.44 | 0.48 | 0.349 | 0.692 |
| Voller Bestand | aktuell | 0.2655 | 0.4351 | 0.4691 | 0.3377 | 0.7166 |
| Voller Bestand | Grid | 0.2774 | 0.4371 | 0.475 | 0.3458 | 0.7126 |
| Teilmenge alt (bis P11) | aktuell | 0.3608 | 0.6082 | 0.6082 | 0.4536 | 0.8454 |
| Teilmenge alt (bis P11) | Grid | 0.3608 | 0.6082 | 0.6186 | 0.4603 | 0.8557 |
| Teilmenge neu Technik | aktuell | 0.3065 | 0.4879 | 0.5363 | 0.3873 | 0.7903 |
| Teilmenge neu Technik | Grid | 0.3306 | 0.4919 | 0.5282 | 0.3981 | 0.7823 |
| Teilmenge neu andere Fachgebiete | aktuell | 0.141 | 0.2436 | 0.2756 | 0.1867 | 0.5192 |
| Teilmenge neu andere Fachgebiete | Grid | 0.141 | 0.2436 | 0.3013 | 0.1916 | 0.5128 |

## Entscheidung

Regel (vorab festgelegt): Neue Gewichte werden nur Standard, wenn auf der Testhälfte Hit@10 und MRR jeweils nicht schlechter sind und mindestens eine der beiden um ≥ 0.01 besser ist (4 Stellen gerundet).

| Bedingung | erfüllt |
|---|---|
| hit@10_not_worse | ja |
| mrr_not_worse | ja |
| gain_ge_min | nein |

**Nicht übernommen: keine Kennzahl um ≥ 0.01 besser (hit@10 0.48 → 0.48 (+0.0), mrr 0.3438 → 0.349 (+0.0052)).**
