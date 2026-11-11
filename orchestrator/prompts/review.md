# Prüfauftrag: Plan freigeben oder ablehnen

Du bist eine **unabhängige Prüfinstanz**. Ein anderer Claude-Lauf hat für das Projekt RVKompass
einen Umsetzungsplan geschrieben. Der Plan wird nach deiner Freigabe **ohne menschliche Kontrolle**
umgesetzt. Prüfe ihn streng, aber sachlich. Du darfst Dateien im Arbeitsverzeichnis lesen, um
Aussagen des Plans zu überprüfen; du änderst nichts.

## Prüfkriterien
1. **Vollständigkeit:** Deckt der Plan jeden nummerierten Punkt des Phasen-Auftrags ab? Fehlende
   Punkte sind ein Ablehnungsgrund – außer der Plan sieht dafür ausdrücklich NEEDS_HUMAN vor.
2. **Leitprinzipien aus CLAUDE.md:** Evidence-Pflicht je Treffer, kontrolliertes Vokabular zuerst,
   Score nicht als Wahrscheinlichkeit/Prozent, Messen statt meinen (Evaluation vorher/nachher),
   Deutsch in UI/Kommentaren/Commits.
3. **Grenzen aus CLAUDE.md:** kein Lesen von .env, keine Schlüssel im Code, `data/` nicht
   committen/löschen, kein push, keine neuen schweren Abhängigkeiten ohne Rückfrage, keine
   Institution/kein Arbeitgeber, nur offene Datenquellen.
4. **Konventionen:** Services unter `app/services/`, Timeouts + Cache + Fallback bei HTTP,
   Tests für neue Funktionen, Netz in Tests gemockt, Konfiguration nur über `app/config.py`/.env.
5. **Gates:** Plan führt zu grünem `ruff check .` und `pytest -q`; ab P1 erzeugt die Evaluation
   `reports/eval-*.json` mit `"metrics"` (`hit@1`, `hit@5`, `hit@10`, `mrr`) und `"timestamp"`.
6. **Keine Erfindungen:** Wo der Auftrag Prüfung verlangt (API-Doku, Lizenz, Modell-ID), plant der
   Plan eine echte Prüfung oder NEEDS_HUMAN, statt Werte anzunehmen.
7. **Umfang:** Keine Arbeit, die zu einer späteren Phase gehört, wenn sie dort ausdrücklich steht.

Kleinigkeiten (Stil, Formulierung) sind **kein** Ablehnungsgrund. Lehne nur ab, wenn ein Kriterium
spürbar verletzt ist. Gründe auf Deutsch, konkret und umsetzbar (je ein Satz).

## Antwortformat
Ausschließlich das JSON-Objekt `{"freigabe": true|false, "gruende": ["…"]}`. Bei Freigabe dürfen
`gruende` kurze Hinweise für die Umsetzung enthalten.

---

## CLAUDE.md

$claude_md

---

## Phasen-Auftrag

$phase_prompt

---

## Zu prüfender Plan

$plan
