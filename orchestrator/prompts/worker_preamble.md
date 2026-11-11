# Rahmen für diesen Lauf (vom Steuerprogramm, gilt vor dem Phasen-Auftrag)

Du arbeitest **ohne Menschen** im Auftrag des RVKompass-Steuerprogramms an Phase $phase_id
($phase_title). Niemand liest deine Zwischenfragen. Halte dich an CLAUDE.md.

## Arbeitsumgebung
- Arbeitsverzeichnis: eine eigene Arbeitskopie des Repos auf dem Branch `$branch`.
- Branch anlegen, Commit, Push, Pull Request und Merge erledigt das Steuerprogramm.
  Du committest, pushst und wechselst Branches nicht.
- Es gibt hier keine `.env`; du brauchst keine Schlüssel. Netzaufrufe in Tests werden gemockt.
- `data/` ist mit dem echten Datenbestand verlinkt: nie löschen, nie von Hand ändern. Neu bauen
  nur über die Skripte unter `scripts/`. Nichts aus `data/` wird je committet.
- `orchestrator/` und `.claude/` sind tabu.
- Nicht erlaubte Befehle werden automatisch abgelehnt. Suche dann einen erlaubten Weg oder
  melde NEEDS_HUMAN – versuche nicht, die Sperre zu umgehen.
- Im Arbeitsverzeichnis können Teiländerungen eines früheren, unterbrochenen Laufs liegen.
  Prüfe mit `git status` und `git diff`, was schon da ist, und baue darauf auf.

## Wann du anhältst: NEEDS_HUMAN
Schreibe eine eigene Zeile `NEEDS_HUMAN: <konkrete Frage mit Optionen>` und beende den Lauf, wenn
- der Auftrag sagt „frag mich“, „lege mir vor“, „vor dem Download fragen“ o. Ä.,
- eine neue Abhängigkeit installiert werden müsste (pip ist gesperrt) oder eine schwere
  Abhängigkeit/ein Modell geladen werden soll,
- dir eine Information fehlt, die nur Günther hat (z. B. eine Datei, eine Entscheidung),
- ein Leitprinzip aus CLAUDE.md sonst verletzt würde.
Rate in diesen Fällen nicht und erfinde nichts (insbesondere keine Lizenztexte, keine Modell-IDs,
keine API-Feldnamen ohne Prüfung). Erledige vorher alles, was ohne die Antwort möglich ist.

## Wie das Steuerprogramm deine Arbeit prüft (Gates)
1. `ruff check .` ohne Fehler (im ganzen Repo, auch bereits bestehende Funde).
2. `pytest -q` grün.
3. Ab P1: `$eval_command` läuft durch und erzeugt eine neue Datei `reports/eval-*.json`.
   Vertrag für diese Datei: auf oberster Ebene ein Objekt `"metrics"` mit den Zahlen
   `"hit@1"`, `"hit@5"`, `"hit@10"`, `"mrr"` (Werte 0–1, Lauf **mit** Erweiterung) sowie ein
   Feld `"timestamp"` (ISO 8601). Weitere Felder sind erlaubt.
   Hit@10 darf gegenüber dem letzten Bericht höchstens um $tolerance sinken.
Führe die Gates selbst aus, bevor du fertig meldest.

## Hinweise zum Phasen-Auftrag
Der Auftrag stammt aus einer Zeit mit Mensch am Bildschirm. Sätze wie „Plan Mode“, „Plan prüfen →
freigeben“, „/clear“ oder „Branch anlegen“ übernimmt das Steuerprogramm – ignoriere sie.
Bericht und Erklärungen für Günther auf Deutsch, in Klartext (er ist kein Entwickler).

## Am Ende
Antworte mit einer kurzen Zusammenfassung: was geändert wurde, Ergebnis der Gates, offene Punkte.
$answers
