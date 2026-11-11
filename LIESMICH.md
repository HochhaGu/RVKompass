# RVKompass – Claude-Code-Starterkit

## Übersicht
1. Inhalt des Kits
2. Einspielen ins Repo
3. Erste Session

## 1. Inhalt des Kits
- `CLAUDE.md` – Projektgedächtnis, wird von Claude Code bei jedem Start geladen
- `.claude/settings.json` – Berechtigungen (erlaubt / nachfragen / verboten) und Formatierungs-Hook
- `.claude/skills/eval/` – Slash-Befehl `/eval`
- `.claude/skills/phase-abschluss/` – Slash-Befehl `/phase-abschluss <N>`
- `prompts/PROMPTS.md` – Prompt-Sequenz P0–P9
- `eval/goldstandard_vorlage.csv` – Vorlage für den Goldstandard

## 2. Einspielen ins Repo
1. Inhalt des Kits in das Wurzelverzeichnis von `rvkompass` kopieren.
2. In `.gitignore` die Zeile `.claude/` ersetzen durch `.claude/settings.local.json`
   (sonst wird die Teamkonfiguration nie versioniert – P0 erledigt das auch automatisch).
3. `pip install ruff` (für den Formatierungs-Hook) und `jq` muss installiert sein.
4. Commit auf einem Branch `setup-claude-code`.
5. Lizenzdatei (EUPL 1.2), README-Hinweis „Privates Projekt“ und Prüfung der Git-Historie auf Schlüssel erledigt Prompt P0 – erst danach das Repo öffentlich schalten.

## 3. Erste Session
Auf dem VPS im Repo `claude` starten → Ordner vertrauen → Shift+Tab bis „plan mode“ → zuerst Prompt S0 (Einrichtung), danach P0. Details: „RVKompass – Schritt für Schritt“, Teil A.
