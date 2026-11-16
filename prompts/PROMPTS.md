# RVKompass – Prompt-Sequenz für Claude Code

## Übersicht
- S0 Einrichtung der Werkstatt auf dem VPS (einmalig, vor P0)
- P0 Onboarding und Audit
- P1 Evaluationsbasis (Goldstandard + `scripts/evaluate.py`)
- P2 Evidence-Datenmodell (Transparenz im Kern)
- P3 GND-Erweiterung über lobid-gnd + GND-ID-Join
- P4 LLM-Erweiterung, verifiziert und pro Begriff
- P5 Scoring neu und kalibriert
- P6 Oberfläche: „Warum dieser Treffer?“, Gruppierung, Fachfilter
- P7 Embedding-Modellvergleich
- P8 Betrieb: Container, Sync, Monitoring
- P9 (optional) Fremddaten per ISBN und Nutzer-Feedback
- P10 Design Variante A „Klar“ (Umstellung der Oberfläche nach docs/design.md)

Verbindliche Projektentscheidungen (10.11.2026): Hosting auf VPS mit Docker · Lizenz EUPL 1.2,
privates Projekt ohne Institutionsbezug · GND erst live mit Cache, ab P8 lokaler Abzug ·
Homonyme gruppiert mit Vorrang für den Fachkontext · neue RVK-Stände automatisch live nach
Prüfungen, mit Info-Mail · Erwartungsmanagement in die Oberfläche eingebaut.

Regeln für jede Session: Plan Mode (Shift+Tab) → Plan prüfen → freigeben. Eine Phase pro Session,
danach `/clear`. Branch `phase-N-kurzname`. Phase gilt erst als fertig, wenn `pytest -q` grün ist und
(ab P1) ein Evaluationsbericht in `reports/` liegt.

---

## S0 – Einrichtung der Werkstatt (einmalig)
```
Wir sind auf meinem VPS (Ubuntu LTS) im Repo ~/dev/rvkompass. Ich bin kein Entwickler –
erkläre jeden Schritt in einem Satz, bevor du ihn ausführst.

1. Prüfe, ob python3, python3-venv, git, jq, tmux und docker vorhanden sind. Fehlt etwas,
   nenne mir den apt-Befehl – installieren tue ich selbst.
2. Lege eine virtuelle Umgebung .venv an und installiere requirements.txt und
   requirements-dev.txt darin. torch bitte als CPU-Variante (kein CUDA), um Platz zu sparen.
3. Lege .env aus .env.example an, trage aber KEINE Schlüssel ein. Sag mir danach genau,
   welche Zeilen ich selbst mit nano ausfüllen muss und wofür sie gut sind.
4. Erkläre mir, wie lange Download (fetch_marcxml_dump.py), Import (sync_rvk.py) und
   Embeddings (build_embeddings.py) voraussichtlich dauern, und gib mir die drei Befehle zum
   Starten in einer eigenen tmux-Sitzung „daten“. Ausführen tue ich sie selbst.
5. Wenn die Daten da sind: pytest -q laufen lassen und den Dev-Server auf Port 5000 starten,
   damit ich ihn über einen SSH-Tunnel im Browser ansehen kann.
Abschluss: kurze Liste „Was ist jetzt wo?“ (Ordner, Datenbank, .env, .venv).
```

## P0 – Onboarding und Audit
```
Lies CLAUDE.md, README.md und den gesamten Code unter app/, scripts/ und tests/.
Ändere noch nichts.

Liefere mir einen Audit-Bericht als docs/audit-2026-11.md (beginnt mit Inhaltsübersicht):
1. Architektur in 10 Sätzen, inkl. Datenfluss von der Eingabe bis zur Ergebnistabelle.
2. Wo genau geht heute Nachvollziehbarkeit verloren? (Hinweis: query + expanded_terms werden zu
   EINEM Vektor verschmolzen – prüfe, was das für die Zuordnung Begriff→Treffer bedeutet.)
3. Ist der angezeigte Prozentwert fachlich als „Konfidenz“ vertretbar? Begründe.
4. Technische Schulden: veraltete Abhängigkeiten (anthropic-SDK, Modell-ID), nicht implementierter
   /internal/sync, .gitignore ignoriert .claude/ komplett, README beschreibt noch Webhosting
   (Entscheidung: VPS mit Docker, siehe P8).
5. Liste der Stellen, die für P1–P6 angefasst werden müssen.
6. Prüfe die gesamte Git-Historie auf versehentlich committete Schlüssel, Tokens oder .env-Inhalte
   (z. B. git log -p mit Suchmustern). Nur berichten, nichts umschreiben.

Danach diese Vorbereitungen für die Veröffentlichung:
- .gitignore: .claude/settings.local.json ignorieren, .claude/settings.json und .claude/skills/
  versionieren.
- LICENSE mit dem offiziellen Text der EUPL 1.2 (deutsche Fassung) anlegen; SPDX-Kennung
  EUPL-1.2 in README nennen. Den Lizenztext nicht selbst formulieren – frag mich nach der Datei,
  falls du ihn nicht verlässlich beschaffen kannst.
- README: Satz „Privates Projekt von Günther Hochhauser.“ ergänzen. Keine Institution nennen.
- ruff in requirements-dev.txt ergänzen. Tests laufen lassen.
```

## P1 – Evaluationsbasis
```
Ziel: Jede spätere Änderung wird messbar.

1. Lege eval/goldstandard.csv an (Vorlage liegt bei: eval/goldstandard_vorlage.csv) mit den Spalten
   query;erwartete_notationen;quelle;fachgebiet;anmerkung
   erwartete_notationen ist eine durch | getrennte Liste (mehrere korrekte Stellen erlaubt).
2. Schreibe scripts/evaluate.py:
   - ruft ranking.search(...) direkt auf (kein HTTP), mit und ohne Erweiterung (--no-expansion)
   - Kennzahlen: Hit@1, Hit@5, Hit@10, MRR, Hauptgruppen-Hit@5 (erste Buchstaben der Notation,
     z. B. "ZN" – zählt als Teiltreffer, wenn die Systemstelle knapp daneben liegt),
     mittlere Antwortzeit, Anteil Anfragen ohne Treffer
   - Ausgabe: reports/eval-<datum>-<git-shorthash>.md und .json, plus Tabelle der 10 schlechtesten
     Anfragen mit erwarteter vs. gelieferter Top-3
   - optional --compare <alter_report.json> für Vorher/Nachher-Differenz
3. Tests für die Metrikfunktionen (reine Funktionen, ohne Datenbank).
4. Erster Baseline-Lauf mit den Beispielzeilen, Bericht ablegen.
5. Hilfsskript scripts/build_gold_candidates.py: holt zu einer Liste von Suchbegriffen Titel mit
   RVK-Notation aus OFFENEN Katalog-/Verbundschnittstellen (Verfügbarkeit und Lizenz zuerst
   prüfen und mir vorlegen) und schreibt Kandidatenzeilen zur manuellen Prüfung. Keine
   institutionellen Zugänge verwenden – RVKompass ist ein privates Projekt.
```

## P2 – Evidence-Datenmodell
```
Ziel: Jeder Treffer weiß, warum er da ist.

1. Führe in app/models.py Dataclasses ein:
   ExpansionTerm(term, source, relation, gnd_id|None, parent_term|None)
     source ∈ {input, gnd, llm, vector}
     relation ∈ {direct, synonym, broader, narrower, related, llm_verified, embedding}
   Evidence(notation_id, via_term: ExpansionTerm, match_type ∈ {register_exact, register_gnd_id,
     label_fts, vector}, raw_score: float)
   Hit(notation, evidences: list[Evidence], score: float, score_components: dict)
2. Baue ranking.search so um, dass es list[Hit] liefert. Bestehendes Verhalten (gleiche Treffer,
   gleiche Reihenfolge) muss erhalten bleiben – prüfe das mit scripts/evaluate.py --compare.
3. Ersetze das Verschmelzen von Anfrage und Erweiterungsbegriffen zu einem Vektor durch
   Retrieval PRO Begriff (Multi-Query), damit jede Evidenz einem Begriff zugeordnet ist.
4. Tests: ein Treffer ohne Evidence darf nicht existieren (Assertion im Code + Test).
```

## P3 – GND-Erweiterung (Kernstück)
```
Ziel: Semantische Erweiterung über kontrolliertes Vokabular statt Blackbox.

1. Neuer Service app/services/expansion/gnd.py:
   - lobid-gnd-Suche nach dem Eingabebegriff, gefiltert auf Sachschlagwörter
     (prüfe die aktuelle API-Doku unter https://lobid.org/gnd/api, bevor du Feldnamen annimmst;
     erwartet: preferredName, variantName, broaderTermGeneral/Generic/Partitive, relatedTerm)
   - Liefert ExpansionTerm-Objekte mit relation synonym|broader|narrower|related und gnd_id
   - Unterbegriffe: über Rückwärtssuche (Begriffe, deren broaderTerm* die GND-ID enthält), begrenzt
   - Timeout 4 s, Cache-Tabelle gnd_cache (Anfrage → JSON, 30 Tage), bei Fehler leere Liste
   - Schnittstelle als Interface (GndSource) bauen: jetzt LobidGndSource, ab P8 LocalGndSource
     mit lobid als Rückfallebene – Aufrufer dürfen den Unterschied nicht merken
2. GND-ID-Join: register_entry.gnd_id ist bereits befüllt. Neue Repository-Funktion
   get_notations_by_gnd_ids(ids) → Evidence mit match_type register_gnd_id.
   Das ist der stärkste, voll erklärbare Pfad: Begriff → GND-Datensatz → RVK-Registereintrag.
3. Homonyme: Liefert lobid mehrere Kandidaten („Feder“, „Bank“, „Mutter“), wird NICHT still
   aufgelöst. Jede Bedeutung wird ein eigener Sense (preferredName, Unterscheidungszusatz,
   GND-Sachgruppe, gnd_id) mit eigenen Evidenzen. Rangfolge der Senses: Übereinstimmung der
   GND-Sachgruppe mit dem aktiven Fachfilter zuerst (Mapping Sachgruppe → RVK-Hauptgruppen als
   konfigurierbare Tabelle), danach Trefferzahl. Kein LLM für die Disambiguierung.
4. Evaluation vorher/nachher, Bericht in reports/.
```

## P4 – LLM-Erweiterung, verifiziert
```
Ziel: Das LLM darf vorschlagen, aber nicht entscheiden.

1. query_expansion.py: Modell-ID und SDK aktualisieren (aktuelles Haiku-Modell, ID über die
   Anthropic-Doku verifizieren). Prompt so ändern, dass strukturiertes JSON zurückkommt:
   [{"begriff": "...", "beziehung": "synonym|oberbegriff|unterbegriff|verwandt|fachkontext",
     "begruendung": "max. 12 Wörter"}]
   Kontext im Prompt: „Bibliothek mit technischem Schwerpunkt, Nutzer ist fachfremd.“
2. Verifikation: Jeder LLM-Begriff wird gegen RVK-Register (exakt/normalisiert) und lobid-gnd
   geprüft. Nur verifizierte Begriffe werden ExpansionTerm(source=llm, relation=llm_verified).
   Nicht verifizierte Begriffe werden in der UI grau als „nicht im Normvokabular gefunden“ gezeigt,
   aber nicht gesucht.
3. Cache bleibt; Tageslimit bleibt. Begründung des LLM wird mitgespeichert und angezeigt.
4. Evaluation: drei Varianten vergleichen – nur GND, nur LLM, GND+LLM.
```

## P5 – Scoring neu
```
Ziel: Ein Score, den man erklären kann.

1. Score je Notation = max über Evidenzen (Pfadgewicht × Evidenzstärke) + Bonus für unabhängige
   Pfade (z. B. +0,05 je weiterem Pfadtyp, gedeckelt). Startgewichte in config:
   register_exact 1.0 · gnd_synonym 0.9 · gnd_narrower 0.75 · gnd_broader 0.7 · gnd_related 0.6 ·
   llm_verified 0.5 · vector 0.4 × cosine
2. score_components pro Treffer füllen, damit die UI die Zusammensetzung als Balken zeigen kann.
3. Gewichte per Grid-Search gegen den Goldstandard optimieren (Ziel: MRR), Overfitting-Schutz durch
   Aufteilung in Entwicklungs- und Testhälfte. Ergebnis + gewählte Gewichte in reports/.
4. Anzeige: Stufen „hoch / mittel / niedrig“ statt Prozent. Schwellen aus der Evaluation ableiten.
```

## P6 – Oberfläche und Transparenz
```
Ziel: Ein Fachfremder versteht in 10 Sekunden, warum ein Treffer kommt.

0. Durchgängig „Vorschläge“ statt „Ergebnisse“; Footer „RVK-Stand: <Version>“ aus der Datenbank.
1. Oberhalb der Treffer: „Ihre Suche wurde erweitert“ als Baum:
   Eingabe → GND-Begriff (mit Link auf lobid) → Synonyme / Ober- / Unter- / verwandte Begriffe,
   LLM-Vorschläge mit Status verifiziert/nicht verifiziert. Jeder Begriff abwählbar (HTMX-Neusuche).
2. Homonyme: Bei mehreren Senses Ergebnisblöcke je Bedeutung, mit Sachgruppe, Checkbox zum
   Ein-/Ausblenden; die zum Fachfilter passende Bedeutung aufgeklappt, die übrigen eingeklappt
   mit Trefferzahl.
3. Treffer gruppiert nach RVK-Hauptgruppe (z. B. „ZN Elektrotechnik“) mit Anzahl – Orientierung,
   in welchem Fachgebiet der Begriff überhaupt verortet ist.
4. Pro Treffer ein Aufklapper „Warum?“: Evidenzpfad als Kette, z. B.
   Eingabe „Wärmepumpe“ → GND-Oberbegriff „Heiztechnik“ → Registereintrag an <Notation>.
   Score-Balken nach Komponenten.
5. Erwartungsmanagement am Treffer: Hierarchiepfad, RVK-Bemerkung (Feld note) sichtbar,
   Warnhinweis „Nur über Ähnlichkeit gefunden – bitte im Register prüfen“ wenn ausschließlich
   Vektor-Evidenz, Prüflink „in RVK-Online prüfen“ gleichrangig neben „kopieren“.
   Etikett „geändert in Version …“ für Stellen mit jüngerer Änderung laut RVK-Historie.
6. Hilfeseite /hilfe „So arbeiten Sie mit RVKompass“ (eine Bildschirmseite, beginnt mit Übersicht):
   Vorschlag ≠ Entscheidung, Evidenzarten erklärt, lokale Aufstellungspraxis und Signaturbildung
   sind nicht Teil des Werkzeugs.
7. Filter „Hauptgruppen einschränken“ (Mehrfachauswahl), Voreinstellung über .env konfigurierbar.
8. Barrierefreiheit: Tastaturbedienung, Kontraste WCAG AA, keine Information nur über Farbe.
```

## P7 – Embedding-Modellvergleich
```
Ziel: Bestes Modell für deutsche technische Fachsprache, betriebstauglich.

Vergleiche mindestens: paraphrase-multilingual-MiniLM-L12-v2 (Ist), intfloat/multilingual-e5-base,
BAAI/bge-m3. Für jedes Modell: Embeddings in separatem Verzeichnis bauen, evaluate.py laufen
lassen, Speicherbedarf, Indexgröße und Antwortzeit messen. Zielplattform ist ein VPS mit Docker;
RAM-Bedarf im Container daher mit ausweisen. Optional prüfen, ob ONNX die Antwortzeit senkt.
Frage mich vor dem Download jedes Modells. Ergebnis als Entscheidungstabelle in reports/.
```

## P8 – Betrieb
```
Ziel: Läuft stabil ohne mein Zutun.

Zielplattform: derselbe VPS, auf dem wir entwickeln (Werkstatt in ~/dev/rvkompass). Der Live-Betrieb
läuft getrennt davon als Docker-Container mit eigenem Datenverzeichnis unter /srv/rvkompass.
Die Domain richte ich selbst ein – frag mich nach dem Namen.

1. Dockerfile + docker-compose (gunicorn, nicht Flask-Dev-Server), Volumes für data/ und logs/,
   Reverse Proxy mit HTTPS (z. B. Caddy), automatischer Neustart. README-Deployment auf VPS
   umschreiben, Webhosting-/Passenger-Teile entfernen.
2. RVK-Sync als monatlicher Cron-Job im Container:
   a) Download-Seite prüfen – neuer Versionsname? Sonst Ende.
   b) Import in *_new-Tabellen, Embeddings neu berechnen.
   c) Prüfungen: Notationsanzahl weicht < 5 % ab · Stichprobe bekannter Notationen vorhanden ·
      Goldstandard-Notationen gegen RVK-Historie prüfen, gelöschte als „gelöscht“ markieren
      statt mitzuzählen · evaluate.py: Hit@10 sinkt nicht um mehr als 0,05.
   d) Bestanden → atomarer Tausch automatisch, alte DB als Backup (letzte 2 behalten).
      Nicht bestanden → alte DB bleibt live.
   e) In beiden Fällen Info-Mail an mich, HTML direkt im Mailtext (kein Link, kein Anhang):
      alter/neuer Stand, Notationen gesamt, neu/geändert/gelöscht, Goldstandard-Markierungen,
      Hit@10 und MRR vorher/nachher, Backup-Datei, bei Fehler Ursache.
      SMTP-Zugang ausschließlich über .env.
   Schwellen konfigurierbar. /internal/sync stößt denselben Ablauf manuell an (Token aus .env).
3. GND lokal: Sachschlagwörter der GND (CC0) als Abzug laden, in SQLite ablegen, monatlich
   aktualisieren; LocalGndSource aus P3 aktivieren, lobid nur noch als Rückfallebene.
4. Health-Endpunkt (/health: DB-Version, RVK-Stand, Anzahl Notationen, Cache-Treffer heute).
5. Anonyme Nutzungsstatistik (Anfragen/Tag, Null-Treffer-Quote) ohne IP-Speicherung –
   Datenschutzerklärung entsprechend anpassen.
6. Deployment-Anleitung docs/deployment.md (beginnt mit Inhaltsübersicht), inkl. Backup-
   und Update-Routine für den Server.
```

## P9 – optional: Fremddaten und Feedback
```
1. Eingabe einer ISBN → Abfrage offener Katalogschnittstellen (z. B. K10plus-SRU, lobid-resources;
   Verfügbarkeit und Lizenz zuerst prüfen) → RVK-Notationen anderer Bibliotheken als eigene
   Evidenzart „fremddaten“.
2. Feedback-Buttons pro Treffer („übernommen“ / „passt nicht“), gespeichert ohne Personenbezug.
   Export als CSV, der den Goldstandard laufend erweitert.
```

---

## P10 – Design Variante A „Klar“
```
Ziel: Die Oberfläche folgt vollständig docs/design.md (Variante A „Klar“). Ranking, Erweiterung
und Scoring bleiben unverändert – die Evaluation muss dieselben Kennzahlen liefern wie vorher.

Verbindliche Quelle ist docs/design.md (Farben, Typografie, Layout, Komponenten,
Barrierefreiheit, Abschnitt 7 „Design-Punkte“, Abschnitt 8 „Logo & Icons“). Bei Widerspruch
zwischen diesem Auftrag und docs/design.md gilt docs/design.md; Unklarheiten als Rückfrage.

Bereits im Repo (nicht neu erzeugen, nicht verändern):
- app/static/img/: logo-rvkompass.svg, logo-mark.svg, logo-wordmark.svg, favicon.svg,
  apple-touch-icon.png; app/static/favicon.ico
- app/static/fonts/: Montserrat-SemiBold.woff2, Montserrat-Bold.woff2,
  SourceSans3-Variable.woff2 (Latin-Teilmenge) samt OFL-Lizenztexten
Keine externen Ressourcen: keine Google Fonts, kein CDN, keine Dritt-Requests (RVKompass bleibt
ohne Cookies und Dritte). Schriften per @font-face aus /static/fonts, font-display: swap.

A. Backend-Voraussetzungen (zuerst, mit Tests ohne Netz)
1. Trefferzahl je Hauptgruppe: Für die Facette braucht jede RVK-Hauptgruppe die Zahl der
   Vorschläge über die UNGEFILTERTE Vorschlagsmenge derselben Suche (gleiche Erweiterung,
   ohne main_group_filter). Heute gibt es nur result.filtered_out als Summe. Möglichst ohne
   zweite vollständige Suche umsetzen (z. B. Zählung im Ranking vor dem Filtern).
   Gleiche Zahl liefert den Auswegs-Knopf „Hauptgruppen-Auswahl zurücksetzen (n Vorschläge)“
   im Keine-Treffer-Zustand.
2. Verweise als Links: In RVK-Bemerkung (note) und Verweis (see_also) Notationen erkennen
   (Muster wie „ST 301“, „ZN 8330“, Bereiche „ST 300 - ST 310“) und als Link ausgeben, der eine
   Suche nach dieser Notation auslöst: „→ ST 301 anzeigen“. Reine Funktion in
   app/services/presentation.py, Text bleibt sonst unverändert und wird korrekt escaped.
3. Wiederherstellen abgewählter Erweiterungsbegriffe: die bestehende Abwahl-Logik
   (tree_keys/apply_exclusions) so nutzen, dass entfernte Begriffe gelistet und einzeln
   wieder hinzugefügt werden können.

B. Oberfläche (danach)
1. Tokens: alle Farben aus docs/design.md Abschnitt 2 als CSS-Variablen in :root; im übrigen CSS
   keine festen Farbwerte (Vorbereitung für einen späteren Dunkelmodus, aber keinen bauen).
2. base.html: Skip-Link „Zum Inhalt“; weißer Header mit 3 px Goldlinie, Logo
   (logo-rvkompass.svg, 44 px hoch, alt="RVKompass") + Claim „Klassifizieren mit Richtung“;
   Navigation Suche · Hilfe · Feedback (mailto:rvkompass@ghochhauser.at); Favicon-Links
   (favicon.svg, favicon.ico, apple-touch-icon.png); Footer navy-900 mit genau einem Link
   „Ein Projekt von ghochhauser.at“, dazu Impressum, Datenschutz und „RVK-Stand: <Version>“.
3. Layout: Container max. 1280 px. DOM- und Tab-Reihenfolge Suche → Facette → Ergebnisse.
   Desktop: Facette links (250–300 px), rechts Suche über Ergebnissen. ≤ 860 px: Suche oben,
   dann eingeklappte Facette (Knopf „Hauptgruppen filtern · n gewählt“), dann Ergebnisse.
4. Suchkarte: Label, Eingabe 52 px, Knopf „Vorschläge anzeigen“, direkt darunter die feste
   Hinweiszeile aus Abschnitt 7 (Wortlaut exakt übernehmen), darunter die Chip-Zeile.
5. Chips statt Erweiterungsbaum: je Begriff Pille mit Typ (Synonym, Oberbegriff, Unterbegriff,
   verwandt) und ×-Knopf (aria-label „<Begriff> entfernen“, Klickfläche ≥ 44 px); entfernte
   Begriffe wiederherstellbar (A3). HTMX-Neusuche wie bisher.
6. Facette: Checkbox · Buchstabe · Name · Trefferzahl (A1); Gruppen ohne Treffer abgeschwächt;
   vor der ersten Suche ausgegraut. Bestehende .env-Voreinstellung der Hauptgruppen bleibt.
7. Startzustand: „So kommst du zur passenden RVK-Stelle“ (3 Schritte) + 4 Beispielsuchen als
   Knöpfe, darunter „Bank“ (mehrdeutig). Kein „Häufig gesucht“.
8. Homonyme: Bedeutungs-Tabs über der Ergebnisliste („Begriff (Bedeutung) · Trefferzahl“),
   Fachkontext-Bedeutung vorgewählt, immer nur ein Block sichtbar, nur ab 2 Bedeutungen.
   ARIA-Tabs-Muster mit Pfeiltasten; ohne JavaScript alle Blöcke untereinander sichtbar.
9. Treffer-Karte: Notation (Montserrat 22 px) · Benennung · Hierarchiepfad · Relevanzblock
   rechts (5 Segmente + Stufenwort gering/schwach/mittel/hoch/sehr hoch, aria-label „Relevanz
   hoch, Stufe 4 von 5“; nie Prozent oder „Konfidenz“) · Aufklapper: Label „Warum? ·
   RVK-Bemerkung“ wenn eine Bemerkung existiert, sonst „Warum dieser Vorschlag?“; darin
   Evidenzkette, Bemerkung und Verweise mit Links (A2). Vektor-Warnhinweis, „in RVK-Online
   prüfen“ und „kopieren“ bleiben erhalten.
10. Keine Treffer: Ursache benennen + Auswege (Hauptgruppen-Auswahl zurücksetzen mit Zahl aus
    A1, allgemeinere Begriffe, Feedback-Link).
11. Hilfe, Impressum, Datenschutz im neuen Layout; Hilfeseite an die neue Bedienung anpassen
    (Chips, Facette, Tabs). Inhalte der Rechtstexte nicht ändern.

C. Qualität
- Barrierefreiheit: WCAG AA-Kontraste (Gold nie als Text auf Weiß, dafür gold-700),
  Fokusrahmen 3 px gold-500, Touch-Ziele ≥ 44 px, keine Information nur über Farbe.
  tests/test_accessibility.py entsprechend erweitern.
- Bestehende Template-Tests an das neue Markup anpassen, ohne Prüfungen abzuschwächen;
  neue Tests für A1–A3, Chips, Tabs, Facettenzahlen, Startzustand, Keine-Treffer-Zustand.
- Evaluation: Hit@1/5/10 und MRR identisch zum Stand vor P10 (reine Darstellungsänderung).
- Bericht docs/phasen/phase-10.md: was umgesetzt wurde, Abweichungen von docs/design.md mit
  Begründung, offene Punkte.
```
