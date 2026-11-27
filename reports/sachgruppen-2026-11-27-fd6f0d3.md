# RVKompass – GND-Sachgruppen → RVK-Hauptgruppen (P12)

## Übersicht

- Zeitpunkt: 2026-11-27T09:43:18+00:00
- Git-Stand: fd6f0d3 (mit ungespeicherten Änderungen)
- Goldstandard: eval/goldstandard.csv (501 Anfragen)
- Aufteilung: Entwicklung 251, Test 250 (wie in P5)
- Sachgruppen-Vokabular: lokal, nur lesend (483 Codes, geladen am 2026-11-12T09:50:08.339529+00:00 aus https://d-nb.info/standards/vocab/gnd/gnd-sc.nt)

> Die Zuordnung wirkt nur auf die Reihenfolge der GND-Bedeutungen bei gesetztem Fachfilter. Der Fachfilter je Anfrage ist simuliert: Hauptgruppen ihres Fachgebiets laut `FACHGEBIET_MAIN_GROUPS` – **eine Annahme von Claude**. Die Messung von Bedeutung@1 beruht auf dieser Annahme.

## Regeln (vorab festgelegt)

- Vorschlag je bisher nicht zugeordnetem Code nur auf der Entwicklungshälfte: mindestens 3 Anfragen, mindestens 80 % davon mit erwarteter Notation in einer Hauptgruppe; eine zweite Hauptgruppe nur, wenn erst beide zusammen 80 % erreichen und jede mindestens 3 Anfragen hat. Bestehende Einträge bleiben.
- Code muss im DNB-Sachgruppen-Vokabular stehen, sonst kein Vorschlag.
- Übernahme, wenn auf der Testhälfte Bedeutung@1 und Bedeutungs-MRR nicht schlechter sind und eine um ≥ 0.01 besser ist (gleiche Regel wie bei den Gewichten). Gezählt nur Anfragen mit mindestens 2 Bedeutungen.

## Vorschläge

| Code | DNB-Benennung | Hauptgruppen | Anfragen | gestützt | Anteil | im Vokabular | Beispiele |
|---|---|---|---|---|---|---|---|
| 31.9a | Elektrotechnik, Elektrische Energietechnik | ZN, ZP | 11 | 9 | 0.8182 | ja | Transformator, Spannung, Photovoltaik, Elektromotor, Akkumulator |
| 31.9b | Elektronik, Kommunikationstechnik | ZN | 6 | 5 | 0.8333 | ja | Operationsverstärker, Signalverarbeitung, Leistungselektronik, Netz, Maus |

## Messung

| Teil | Zuordnung | Anfragen (≥ 2 Bedeutungen) | Bedeutung@1 | Bedeutungs-MRR |
|---|---|---|---|---|
| Testhälfte | bisher | 38 | 0.3947 | 0.4298 |
| Testhälfte | erweitert | 38 | 0.3947 | 0.4298 |
| Entwicklungshälfte | bisher | 51 | 0.2941 | 0.3497 |
| Entwicklungshälfte | erweitert | 51 | 0.3137 | 0.3595 |
| Voller Bestand | bisher | 89 | 0.3371 | 0.3839 |
| Voller Bestand | erweitert | 89 | 0.3483 | 0.3895 |

**Nicht übernommen: keine Kennzahl um ≥ 0.01 besser (sense@1 0.3947 → 0.3947 (+0.0), sense_mrr 0.4298 → 0.4298 (+0.0)).**

## Gegenproben

- Eigene Reihenfolge der Bedeutungen gleich ranking.rank_senses (bisherige Zuordnung): ja
- dasselbe mit erweiterter Zuordnung (Testhälfte): ja
- Trefferliste (Grundlage von Hit@k und MRR) mit beiden Zuordnungen gleich (Testhälfte, 250 Anfragen): ja
