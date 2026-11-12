---
name: phase-abschluss
description: Schließt eine Umsetzungsphase von RVKompass ab – Tests, Evaluation, Commit, Abschlussbericht. Aufruf mit /phase-abschluss <Phasennummer>.
disable-model-invocation: true
allowed-tools: Bash(pytest *) Bash(ruff *) Bash(python scripts/evaluate.py *) Bash(git status *) Bash(git diff *) Bash(git add *) Bash(git commit *)
---

## Übersicht
1. Qualitätsgates
2. Commit
3. Abschlussbericht

## 1. Qualitätsgates
- `ruff check .` ohne Fehler
- `pytest -q` grün
- Evaluation gelaufen (ab Phase 1), Bericht in `reports/`
Schlägt ein Gate fehl: stoppen, Ursache nennen, nichts committen.

## 2. Commit
Auf dem Phasen-Branch committen, Nachricht auf Deutsch:
`Phase $ARGUMENTS: <Kurzbeschreibung>` plus Stichpunkte der Änderungen. Kein Push.

## 3. Abschlussbericht
Datei `docs/phasen/phase-$ARGUMENTS.md`, beginnt mit Inhaltsübersicht:
Ziel · Was geändert wurde (Dateien) · Kennzahlen vorher/nachher · Offene Punkte · Empfehlung für die nächste Phase.
