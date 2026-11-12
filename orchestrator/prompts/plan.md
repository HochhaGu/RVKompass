$preamble

---

# Deine Aufgabe in diesem Lauf: nur planen

Erstelle einen Umsetzungsplan für den folgenden Phasen-Auftrag. **Ändere nichts.** Lies dazu
CLAUDE.md und den betroffenen Code. Der Plan wird danach von einer zweiten, unabhängigen Instanz
gegen den Auftrag und CLAUDE.md geprüft und anschließend ohne Rückfrage umgesetzt.

Der Plan muss enthalten:
- Übersicht (Inhaltsverzeichnis) am Anfang
- Ziel und Kontext in zwei, drei Sätzen
- Schritte in Reihenfolge, je mit betroffenen Dateien
- welche Tests neu oder geändert werden
- wie die Gates erfüllt werden (ruff, pytest, ab P1 Evaluation mit dem JSON-Vertrag)
- Punkte, an denen NEEDS_HUMAN nötig wird (oder „keine“)

Gib den **vollständigen Plan als deine Antwort** aus (Markdown), nicht nur einen Verweis auf eine
Datei. Ist der Auftrag ohne Antwort von Günther gar nicht planbar, schreibe stattdessen
`NEEDS_HUMAN: <Frage>`.
$revision

---

# Phasen-Auftrag

$phase_prompt
