---
name: eval
description: Führt die RVKompass-Evaluation gegen den Goldstandard aus und vergleicht mit dem letzten Bericht. Aufruf mit /eval.
disable-model-invocation: true
allowed-tools: Bash(python scripts/evaluate.py *) Bash(ls *)
---

## Übersicht
1. Letzten Bericht finden
2. Evaluation laufen lassen
3. Ergebnis zusammenfassen

## 1. Letzten Bericht finden
Suche in `reports/` die jüngste `eval-*.json`.

## 2. Evaluation laufen lassen
`python scripts/evaluate.py --gold eval/goldstandard.csv --out reports/ --compare <letzter Bericht>`

## 3. Ergebnis zusammenfassen
Tabelle mit Hit@1, Hit@5, Hit@10, MRR, Hauptgruppen-Hit@5, Null-Treffer-Quote – jeweils alt, neu, Differenz.
Danach die drei auffälligsten Verschlechterungen mit Anfrage, erwarteter und gelieferter Top-3.
Keine Interpretation über die Zahlen hinaus, außer auf Nachfrage.
