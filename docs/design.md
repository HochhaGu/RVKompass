# RVKompass – Design (Variante A „Klar“)

**Übersicht**
1. Status & Entscheidungen
2. Farben
3. Typografie
4. Layout
5. Komponenten
6. Barrierefreiheit
7. Design-Punkte (entschieden)
8. Logo & Icons

## 1. Status & Entscheidungen
- 16.11.2026: Variante A „Klar“ gewählt (Entwurf: https://claude.ai/artifact/KekdDhX6PgrswFUNwiag5q)
- Hauptgruppen als Facette links neben Suche + Ergebnissen (nicht ausklappbar unter dem Suchfeld)
- Sucherweiterungen als abwählbare Chips unter der Suchleiste, entfernte Begriffe wiederherstellbar
- Relevanz: 5 Segmente + Stufenwort (gering · schwach · mittel · hoch · sehr hoch)
- Genau ein Link zu ghochhauser.at, im Footer („Ein Projekt von ghochhauser.at“)
- Navigation: Suche · Hilfe · Feedback (mailto:rvkompass@ghochhauser.at)
- Logo: Originallogo überall, wo es groß sichtbar ist; darunter die Kompass-Marke (siehe Abschnitt 8)

## 2. Farben
| Token | Wert | Rolle |
|---|---|---|
| navy-900 | #13294B | Footer, Notation, Überschriften |
| navy-700 | #1C3A6B | Primärbutton, Links, Checkbox-Akzent |
| navy-100 | #E8EDF5 | aktive Navigation, Flächen |
| gold-500 | #C9A24B | Header-Linie, Relevanz-Segmente, Chip-Rand (nur Flächen, nie Text auf Weiß) |
| gold-50 | #FAF4E6 | Chip-Hintergrund |
| gold-700 | #7A5A12 | Gold als Text (Chip-Typ) |
| gold-300 | #F1D58F | Link im Footer (auf Navy) |
| text | #1A2233 | Fließtext |
| muted | #4A5568 | Sekundärtext (≥ 4,5:1) |
| border | #D9DFE8 | Karten, Rahmen |
| bg | #F6F7FA | Seitenhintergrund |
| seg-off | #E3E7EE | leeres Relevanz-Segment |

## 3. Typografie
- Überschriften, Notationen: Montserrat 600/700 (passt zur Logo-Wortmarke)
- Text: Source Sans 3 400/600/700
- Größen: Notation 22 px · Treffertitel 18 px · Text 15–16 px · Hilfstext 14 px · Labels 13 px

## 4. Layout
- Container max. 1280 px, Padding 32 px
- Zeile: Facette (250–300 px) | Hauptbereich (flex); unter ~600 px stapelt die Facette nach oben
- Header weiß mit 3 px Goldlinie unten, Logo (Kombination horizontal) 44 px hoch + Claim „Klassifizieren mit Richtung“
- Footer navy-900

## 5. Komponenten
- **Suchkarte:** Label, Eingabe 52 px mit 2 px Navy-Rand, Button „Vorschläge anzeigen“, darunter Chip-Zeile
- **Chip:** Pille, gold-50/gold-500-Rand, Begriff + Typ (Synonym, Oberbegriff, Unterbegriff, verwandt), ×-Button 30 px mit aria-label
- **Treffer-Karte:** Notation · Benennung · Hierarchiepfad · „Warum dieser Vorschlag?“ (aufklappbar) · Relevanzblock rechts
- **Facetten-Eintrag:** Checkbox · Buchstabe · Name · Trefferzahl; Gruppen ohne Treffer abgeschwächt
- Radius: Karten 12 px, Eingabe/Button 10 px, Chips 999 px

## 6. Barrierefreiheit
- Fokusrahmen 3 px gold-500
- Relevanz zusätzlich als Text und aria-label („Relevanz hoch, Stufe 4 von 5“)
- Touch-Ziele ≥ 44 px; Chip-× sichtbar 32 px, Klickfläche per Padding auf 44 px erweitern
- Skip-Link „Zum Inhalt“

## 7. Design-Punkte (alle entschieden, 16.11.2026)
- [x] Homonyme (16.11.): Bedeutungs-Umschalter als Tabs über der Ergebnisliste, „Begriff (Bedeutung) · Trefferzahl“; Fachkontext-Bedeutung vorgewählt, immer nur eine Gruppe sichtbar; nur eingeblendet, wenn ≥ 2 Bedeutungen
- [x] Startzustand (16.11.): ohne „Häufig gesucht“ – Ergebnisbereich zeigt „So kommst du zur passenden RVK-Stelle“ (3 Schritte) + 4 Beispielsuchen als Buttons (inkl. mehrdeutiges Beispiel „Bank“); Facette ausgegraut bis zur ersten Suche. Anonyme Suchzählung später optional vorbereiten (Mindestschwelle, Datenschutz ergänzen)
- [x] Keine Treffer (16.11., Entwurf A-Keine-Treffer): Ursache benennen + konkrete Auswege (Facettenauswahl zurücksetzen mit Trefferzahl, allgemeinere Begriffe, Feedback-Link)
- [x] Erwartungsmanagement (16.11.): feste, dezente Hinweiszeile direkt unter dem Suchschlitz (13 px, muted, Info-Icon in gold-700): „Vorschläge sind eine Orientierung, keine geprüfte Klassifikation – bitte die Notation vor der Vergabe in RVK-Online prüfen.“ Kein Wegklicken, keine kontextuellen Zusatzhinweise
- [x] Dunkelmodus (16.11.): keiner. Alle Farben ausschließlich über CSS-Variablen (Tokens aus Abschnitt 2), damit ein späterer Dunkelmodus ohne Umbau möglich ist
- [x] Responsive-Grundregel (16.11.): Suchleiste steht auf jedem Gerät ganz oben. Grid: Desktop „Facette | Suche / Facette | Ergebnisse“; ≤ 860 px Reihenfolge Suche → Hauptgruppen (eingeklappt, Button „Hauptgruppen filtern · n gewählt“) → Ergebnisse. DOM-Reihenfolge = Suche, Facette, Ergebnisse (auch Tab-Reihenfolge)
- [x] RVK-Bemerkung (16.11.): im Aufklapp-Bereich gemeinsam mit der Begründung; Button-Label „Warum? · RVK-Bemerkung“, wenn eine Bemerkung existiert, sonst „Warum dieser Vorschlag?“; Verweise (z. B. „→ ST 301 anzeigen“) als Link

## 8. Logo & Icons (entschieden 16.11.2026)
**Regel:** Originallogo (Buch + Kompassnadel) überall, wo es groß sichtbar ist (ab ca. 40 px Höhe). Darunter ersetzt die Kompass-Marke (Variante A: goldene Nadel + Ring auf Navy) das Logo – die Buchseiten sind klein nicht mehr erkennbar.

| Datei (Repo) | Inhalt | Einsatz |
|---|---|---|
| `app/static/img/logo-rvkompass.svg` | Bildmarke + Wortmarke horizontal | Header (44 px) |
| `app/static/img/logo-mark.svg` | Bildmarke (Buch + Nadel), quadratisch | große Einzeldarstellung (z. B. Hilfeseite, Präsentationen) |
| `app/static/img/logo-wordmark.svg` | Wortmarke allein | Reserve (Druck, Folien) |
| `app/static/img/favicon.svg` | Kompass-Marke, Navy-Fläche mit 14/64 Radius | Favicon (moderne Browser) |
| `app/static/favicon.ico` | Kompass-Marke 16/32/48 px | Favicon-Fallback |
| `app/static/img/apple-touch-icon.png` | Kompass-Marke 180 px, vollflächig | Home-Bildschirm iOS/Android |

- Wortmarke: Montserrat, „RV“ Bold + „Kompass“ Medium, einfarbig navy-900, als Pfade (keine Schrift nötig)
- Farben ausschließlich navy-900 #13294B und gold-500 #C9A24B; kein Verlauf
- Quelle: KI-generierte Rastervorlage, farbgetrennt vektorisiert (16.11.2026)
