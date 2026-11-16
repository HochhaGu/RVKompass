# Phase P10 – Design Variante A „Klar“

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Die Oberfläche des RVKompass wurde komplett auf das Design „Variante A – Klar“ aus `docs/design.md` umgestellt. Dazu gehören neue Farben und Schriften, Hauptgruppen als Liste links, Begriffe als Chips (kleine Pillen zum Entfernen) und Treffer als Karten. Die Rangfolge der Vorschläge sollte sich dabei nicht ändern. Das haben wir gemessen.

## Was sich geändert hat

### Im Hintergrund
- **Trefferzahl je Hauptgruppe**
  - Was: Jede Suche zählt mit, wie viele Fundstellen in jeder Hauptgruppe liegen. Der Filter spielt dabei keine Rolle. Es gibt keine zweite Suche.
  - Warum: Die Liste links soll die Zahlen zeigen.
  - Wo: `app/services/ranking.py`
- **Verweise als Links**
  - Was: Notationen in RVK-Bemerkung und „siehe auch“ (zum Beispiel „ST 301“ oder „ST 300 - ST 310“) werden zu Links „→ ST 301 anzeigen“. Der übrige Text bleibt gleich und wird sicher ausgegeben.
  - Warum: Du kannst Verweisen direkt folgen.
  - Wo: `app/services/presentation.py`, `app/static/app.js`, `app/routes/search.py`
- **Notation als Suchbegriff (über den Auftrag hinaus)**
  - Was: Tippst du eine Notation ein, steht über den Vorschlägen eine Karte „Eingegebene Notation“. Herkunft: „Direkt: Notation eingegeben“.
  - Warum: Der Suchindex (die durchsuchbare Datenbank) kennt die Notationen selbst nicht. Ohne diese Karte würde ein Verweis-Link ins Leere führen.
  - Wo: `app/services/presentation.py`, `app/services/rvk_repository.py`
  - Rangfolge und Messung bleiben davon unberührt.
- **Entfernte Begriffe zurückholen**
  - Was: Begriffe, die du aus der Erweiterung entfernst, stehen in einer Liste. Jeder lässt sich einzeln wieder hinzufügen.
  - Warum: Eine Abwahl soll nicht endgültig sein.
  - Wo: `app/services/presentation.py` (ersetzt den früheren Erweiterungsbaum), `app/routes/search.py`

### In der Oberfläche
- **Farben und Schriften:** Farben stehen nur als Variablen am Anfang des Stylesheets. So ist ein späterer Dunkelmodus ohne Umbau möglich. Die Schriften liegen auf dem eigenen Server, es gibt keine Anfragen an fremde Dienste. Wo: `app/static/style.css`
- **Kopf und Fuß:** Weißer Kopf mit Logo, Claim und den Menüpunkten Suche · Hilfe · Feedback. Im Fuß steht genau ein Link: „Ein Projekt von ghochhauser.at“. Wo: `app/templates/base.html`
- **Hauptgruppen links:** Jede Gruppe zeigt ihre Trefferzahl. Gruppen ohne Treffer sind abgeschwächt. Auf dem Handy ist die Liste hinter dem Knopf „Hauptgruppen filtern · n gewählt“ eingeklappt. Wo: `app/templates/_facet_list.html`, `app/templates/search.html`
- **Suchkarte und Chips:** Unter dem Suchfeld steht fest die Hinweiszeile, dass Vorschläge nur eine Orientierung sind. Darunter stehen die Erweiterungsbegriffe als Chips mit ×. Begriffe vom Sprachmodell tragen den Zusatz „KI, geprüft“. Wo: `app/templates/search.html`, `app/templates/_chips.html`
- **Bedeutungs-Tabs:** Bei mehrdeutigen Wörtern gibt es Reiter, zum Beispiel „Mutter (Technik) · 1“. Sie lassen sich mit Pfeiltasten bedienen. Ohne JavaScript stehen alle Bedeutungen untereinander. Wo: `app/templates/_results_table.html`, `app/static/app.js`
- **Treffer-Karten:** Notation, Benennung und Hierarchie, dazu fünf Relevanzstufen als Segmente und als Wort. Prozent gibt es nirgends. Der Aufklapper heißt „Warum? · RVK-Bemerkung“ oder „Warum dieser Vorschlag?“. Wo: `app/templates/_hit_card.html`, `app/templates/_hit_list.html`
- **Startseite:** „So kommst du zur passenden RVK-Stelle“ mit drei Schritten und vier Beispielsuchen (Klimawandel, Umwelttechnik, Konfliktmanagement, Bank). Wo: `app/templates/_start_state.html`
- **Keine Vorschläge:** Die Seite nennt die Ursache und bietet Auswege an: Auswahl zurücksetzen, allgemeinere Begriffe, Feedback-Link. Wo: `app/templates/_results_table.html`
- **Hilfe, Impressum, Datenschutz:** Die Hilfe ist neu in „du“-Form und beschreibt die neue Bedienung. Impressum und Datenschutz haben nur eine neue Hülle, die Texte sind wörtlich gleich. Wo: `app/templates/hilfe.html`, `impressum.html`, `datenschutz.html`

### Abweichungen vom Plan, die du kennen solltest
- **Fünf statt drei Relevanzstufen.** „mittel“ und „hoch“ liegen an den bisherigen, geprüften Grenzen. „sehr hoch“ und „schwach“ sind neu und aus diesen Grenzen abgeleitet, also **nicht** am Goldstandard (der Prüfsammlung mit richtigen Antworten) abgestimmt. Scoring und Einstellungen sind unverändert.
- **Umschaltpunkt 860 px.** `docs/design.md` nennt an einer Stelle 600 px, an einer späteren 860 px. Umgesetzt ist 860 px.
- **Chip-×:** sichtbar 32 px, Klickfläche 44 px.
- **Fokusrahmen:** Gold wie gefordert, dazu ein schmaler dunkler Ring. Gold allein ist auf Weiß zu kontrastarm.
- **Zwei zusätzliche Farbvariablen:** Weiß für Flächen und Weiß für Text auf Navy.
- **Ansprache „du“** auf Startseite und Hilfe. Die Rechtstexte sind unverändert.
- **Einleitung „Was ist der RVKompass?“** zog von der Startseite auf die Hilfeseite um.

## Kennzahlen vorher/nachher
Diese Zahlen sind Relevanzmaße (Trefferquoten und mittlerer Rang), keine Wahrscheinlichkeiten.

| Kennzahl | vorher | nachher | Änderung |
|---|---|---|---|
| Hit@1 | 0.258 | 0.258 | +0.000 |
| Hit@5 | 0.443 | 0.443 | +0.000 |
| Hit@10 | 0.546 | 0.546 | +0.000 |
| MRR | 0.343 | 0.343 | +0.000 |
| Hauptgruppen-Treffer | – | – | – |

Prüfungen:
- `ruff check`: bestanden.
- `pytest`: 686 Tests bestanden.
- Evaluation: Sprachmodell `claude-haiku-5-5`, 0 Fehler, 641 Vorschläge, davon 541 geprüft.
- Hit@10-Vergleich: 0.546 → 0.546, unverändert.

Hinweis zu den Berichten in `reports/`: Dort liegen drei Läufe vom 16.11.2026. Der Lauf `eval-2026-11-16-afc2ce4-2` (ohne Live-Sprachmodell) zeigt Hit@5 0.4639, Hit@10 0.5567 und MRR 0.3461. Der Lauf `-3` zeigt die Werte aus der Tabelle. Die Werte unterscheiden sich also je nach Lauf. Wichtig für diese Phase ist nur: In beiden Läufen ist vorher gleich nachher.

## Was das für die Nutzung bedeutet
- Die Vorschläge kommen in derselben Reihenfolge wie vorher. Neu ist nur die Darstellung.
- Du siehst jetzt auf einen Blick, in welchen Hauptgruppen wie viele Fundstellen liegen.
- Du kannst Erweiterungsbegriffe entfernen und wieder zurückholen.
- Bei mehrdeutigen Wörtern wählst du die Bedeutung über Reiter. Passt eine Bedeutung zum Fachfilter, ist sie vorgewählt.
- Die Relevanz steht immer auch als Wort da. Sie ist ein Maß für die Passung, keine Wahrscheinlichkeit.
- Die Notation musst du weiterhin selbst in RVK-Online prüfen. Der Hinweis steht fest unter dem Suchfeld.

## Offene Punkte
- **Äußere Stufen kalibrieren:** „sehr hoch“ (ab 0.7348) und „schwach“ (ab 0.4957) sind abgeleitet, nicht gemessen. Sie sollten später gegen den Goldstandard abgestimmt werden.
- **Zahl und Liste zählen Verschiedenes:** Die Zahl in der Hauptgruppen-Liste zählt alle Fundstellen. Die Liste zeigt höchstens 20 Vorschläge. Nach „Auswahl zurücksetzen (n Vorschläge)“ siehst du also eventuell weniger als n.
- **Alte Vorlagen löschen:** `app/templates/_expansion_tree.html`, `_hit_group.html` und `_hit_row.html` werden nicht mehr verwendet. Löschen war in diesem Lauf nicht erlaubt. Sie sollten vor dem Zusammenführen mit `git rm` entfernt werden.
- **Prüfung im Browser:** Pfeiltasten in den Reitern, Fokus nach dem × und die Darstellung bis 860 px sind nur über das Markup getestet. Ein Blick im echten Browser (Desktop und Handy) fehlt noch.
- **Dunkelmodus:** vorbereitet, nicht gebaut.
- **„Häufig gesucht“:** Eine anonyme Suchzählung ist für später gedacht. Sie braucht eine Mindestschwelle und eine Ergänzung der Datenschutzerklärung.

## Empfehlung für die nächste Phase
1. Zuerst die drei alten Vorlagen mit `git rm` entfernen und kurz im Browser prüfen (Desktop und Handy).
2. Danach die äußeren Relevanzstufen gegen den Goldstandard abstimmen. Das passt zum Grundsatz „Messen statt meinen“.
3. Dann die Facetten-Zahl und die Listenlänge angleichen, damit „n Vorschläge“ stimmt.
4. Erst danach Dunkelmodus oder „Häufig gesucht“ angehen.
