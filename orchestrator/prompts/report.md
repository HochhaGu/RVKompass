# Auftrag: Phasenbericht schreiben

Phase $phase_id ($phase_title) ist umgesetzt, alle Gates sind grün. Schreibe die Datei
`docs/phasen/phase-$phase_number.md` für Günther. Er ist **kein Entwickler**: Klartext, kurze
Sätze, Fachwörter beim ersten Auftreten in einem Halbsatz erklären. Ändere keine andere Datei.

Aufbau (genau diese Reihenfolge):
1. `# Phase $phase_id – $phase_title`
2. `## Übersicht` – nummerierte Liste der folgenden Abschnitte
3. `## Worum es ging` – Ziel der Phase in 2–3 Sätzen
4. `## Was sich geändert hat` – Änderungen in Klartext, je Punkt: was, warum, wo (Datei)
5. `## Kennzahlen vorher/nachher` – Tabelle aus den Zahlen unten; „–“, wo keine vorliegen.
   Kennzahlen sind Relevanzmaße, keine Wahrscheinlichkeiten – nicht als Prozent-Konfidenz darstellen.
6. `## Was das für die Nutzung bedeutet`
7. `## Offene Punkte`
8. `## Empfehlung für die nächste Phase`

Antworte danach nur mit „Bericht geschrieben“.

## Daten

Gates:
$gates

Kennzahlen vorher: $metrics_before
Kennzahlen nachher: $metrics_after

Geänderte Dateien (git diff --stat gegen $base):
```
$diffstat
```

Plan der Phase:
$plan

Zusammenfassung der Umsetzung:
$summary
