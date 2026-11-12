# Phase P6 – Oberfläche und Transparenz

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Wer fachfremd klassifiziert, soll in etwa 10 Sekunden sehen, warum ein Vorschlag kommt. Dazu gehört: wie die Suche erweitert wurde, welche Bedeutung des Worts gemeint ist, in welchem Fachgebiet der Vorschlag liegt und über welchen Weg er gefunden wurde. Auch soll klar sein, wo man ihn prüfen muss.

P6 ändert nur Anzeige, Bedienung und Filter. Die Rangfolge der Vorschläge bleibt gleich. Ohne Abwahl und ohne Filter rechnet die Suche genau wie in Phase 5.

## Was sich geändert hat

- **Wortwahl**
  - Was: Überall steht jetzt „Vorschläge“ statt „Ergebnisse“ oder „Treffer“. Das Wort „Konfidenz“ ist weg; es heißt „Relevanzstufe“.
  - Warum: Der Score ist ein Maß für Relevanz, keine Wahrscheinlichkeit. Das ist ein Leitprinzip des Projekts.
  - Wo: `app/templates/search.html`, `app/services/embedding_service.py`.

- **Footer „RVK-Stand“**
  - Was: Unten auf jeder Seite steht der RVK-Stand, z. B. „2026/2“. Er kommt aus dem letzten erfolgreichen Abgleich. Ist er nicht lesbar, steht dort „unbekannt“.
  - Warum: Man sieht, mit welcher RVK-Ausgabe die Vorschläge entstanden sind.
  - Wo: `app/services/rvk_repository.py`, `app/__init__.py`, `app/templates/base.html`.

- **Baum „Ihre Suche wurde erweitert“**
  - Was: Über den Vorschlägen steht ein Baum. Er zeigt: Ihre Eingabe, dann die GND-Begriffe (GND ist die Gemeinsame Normdatei, ein Schlagwortverzeichnis; mit Link auf lobid-gnd und Sachgruppe), dann Synonyme, Ober-, Unter- und verwandte Begriffe. Darunter stehen die Vorschläge des Sprachmodells. Der Status steht als Text da, z. B. „verifiziert“ oder „nicht verifiziert – nicht gesucht“.
  - Jeder Begriff hat ein Häkchen. Nimmt man es weg, wird sofort ohne ihn neu gesucht. Die Eingabe selbst lässt sich nicht abwählen.
  - Warum: Man sieht, was die Suche zusätzlich anstößt, und kann Unpassendes ausschalten.
  - Wo: `app/services/presentation.py`, `app/templates/_expansion_tree.html`, `app/routes/search.py`.

- **Mehrere Bedeutungen eines Worts (z. B. „Mutter“)**
  - Was: Jede Bedeutung bekommt einen eigenen, aufklappbaren Block mit Sachgruppe und Zahl der Vorschläge. Jeder Vorschlag steht in genau einem Block. Aufgeklappt ist die Bedeutung, die zum gewählten Fachgebiet passt, sonst die erste. Über Häkchen lassen sich Blöcke ein- und ausblenden.
  - Warum: Mehrdeutige Wörter werden nicht still aufgelöst, sondern offen gezeigt.
  - Wo: `app/services/presentation.py`, `app/templates/_results_table.html`.

- **Gruppen nach Hauptgruppe**
  - Was: Die Vorschläge stehen nach RVK-Hauptgruppe geordnet, z. B. „ZN Elektrotechnik (5)“. Die Rangnummer über alle Vorschläge bleibt an jeder Zeile.
  - Warum: Fachfremde finden so schneller das passende Fachgebiet.
  - Wo: `app/services/presentation.py`, `app/services/rvk_repository.py`, `app/templates/_hit_group.html`.

- **Aufklapper „Warum?“ am Vorschlag**
  - Was: Er zeigt die Kette, über die der Vorschlag gefunden wurde. Beispiel: Eingabe → GND-Begriff → GND-Oberbegriff → Registereintrag → Notation. Dazu stehen die Bestandteile der Relevanz als Text und als Balken da. Gezeigt werden höchstens 5 Ketten, der Rest als „+N weitere“.
  - Warum: Jeder Vorschlag soll eine nachvollziehbare Herkunft haben.
  - Wo: `app/services/presentation.py`, `app/templates/_hit_row.html`.

- **Hilfen am Vorschlag**
  - Was: Sichtbar sind Hierarchiepfad, „RVK-Bemerkung“ und „siehe auch“. Wurde ein Vorschlag nur über Ähnlichkeit gefunden, steht dort ein Warnhinweis: „Nur über Ähnlichkeit gefunden – bitte im Register prüfen“. „kopieren“ und „in RVK-Online prüfen“ sind jetzt Textknöpfe statt Symbole.
  - Warum: Die Oberfläche soll Erwartungen richtig setzen. Geprüft wird weiter von Menschen.
  - Wo: `app/services/ranking.py`, `app/templates/_hit_row.html`.

- **Etikett „geändert in Version …“**
  - Was: Die Anzeige ist eingebaut, erscheint aber derzeit nie. Sie braucht Einträge in der neuen, noch leeren Tabelle `notation_change`. Ein Test stellt sicher, dass ohne Daten nichts angezeigt wird.
  - Warum: Es wird nichts erfunden. Die Datenbank kennt keine Änderungshistorie.
  - Wo: `app/models.py`, `app/services/rvk_repository.py`, `app/templates/_hit_row.html`.

- **Hilfeseite `/hilfe`**
  - Was: „So arbeiten Sie mit RVKompass“. Sie erklärt: Ein Vorschlag ist keine Entscheidung. Jeder Fundweg wird mit einem Satz zur Verlässlichkeit erklärt. Dazu kommen „Warum?“, der Warnhinweis und die Relevanzstufen. Auch steht dort, was das Werkzeug nicht tut (lokale Aufstellungspraxis, Signaturbildung).
  - Wo: `app/routes/help.py`, `app/templates/hilfe.html`.

- **Filter „Hauptgruppen einschränken“**
  - Was: Auf der Startseite lassen sich mehrere Hauptgruppen ankreuzen. Dann kommen nur Vorschläge aus diesen Gruppen. Ein Hinweis nennt, wie viele Fundstellen dadurch wegfallen. Die Voreinstellung steht in der `.env` unter `DEFAULT_MAIN_GROUPS` (Standard: leer, also kein Filter). Der alte Parameter `fach` geht weiter.
  - Warum: Der Filter wirkt nach dem Sortieren. Die Rangfolge bleibt dadurch unverändert.
  - Wo: `app/config.py`, `app/routes/search.py`, `app/templates/search.html`, `app/services/ranking.py`.

- **Barrierefreiheit**
  - Was: Sprunglink „Zum Inhalt“, sichtbares Feldlabel, deutlicher Fokusrahmen, Tabellen mit Überschriften. Die Farben erreichen mindestens Kontrast 4,5:1 (Stufe AA). Status und Warnhinweise stehen als Text da, nicht nur als Farbe. Die Seite ist auch per Tastatur bedienbar.
  - Wo: `app/static/style.css`, `app/static/app.js`, alle Templates.

- **Tests**
  - Neu sind drei Testdateien (Darstellung, Hilfeseite, Barrierefreiheit). Erweitert wurden die Tests für Suchroute, Ranking, Repository und Konfiguration.
  - Wo: `tests/`.

## Kennzahlen vorher/nachher
Alle Werte sind Relevanzmaße für die Rangfolge, keine Wahrscheinlichkeiten. Sie zeigen, wie weit oben die richtige Stelle in der Vorschlagsliste steht.

| Kennzahl | vorher | nachher | Änderung |
|---|---|---|---|
| Hit@1 (richtige Stelle auf Platz 1) | 0.258 | 0.258 | ±0 |
| Hit@5 (unter den ersten 5) | 0.443 | 0.443 | ±0 |
| Hit@10 (unter den ersten 10) | 0.546 | 0.546 | ±0 |
| MRR (Durchschnitt der Kehrwerte der Plätze) | 0.340 | 0.340 | ±0 |
| Hauptgruppen-Treffer | – | – | – |

Die Rangfolge hat sich nicht verändert. Das war so geplant, denn P6 berührt nur die Anzeige.

Zum Lauf: Das Sprachmodell (claude-haiku-5-5) war eingerichtet, es gab aber 0 neue Aufrufe, weil alles aus dem Zwischenspeicher kam. Es gab 0 Fehler. Von 641 Vorschlägen waren 538 geprüft.

## Was das für die Nutzung bedeutet
- Sie sehen zu jedem Vorschlag, woher er kommt. Der Aufklapper „Warum?“ zeigt den Weg Schritt für Schritt.
- Sie können die Erweiterung steuern. Unpassende Begriffe im Baum lassen sich abwählen, die Suche läuft sofort neu.
- Bei mehrdeutigen Wörtern entscheidet das Werkzeug nicht für Sie. Es zeigt alle Bedeutungen und klappt die passendste auf.
- Mit dem Hauptgruppen-Filter schränken Sie ein, in welchen Fachgebieten gesucht wird. Was dadurch wegfällt, wird genannt.
- Der Warnhinweis sagt Ihnen, wann ein Vorschlag nur über Ähnlichkeit kommt und besonders sorgfältig zu prüfen ist.
- Der Link „in RVK-Online prüfen“ führt zur Gegenprobe im Original.
- Ein Vorschlag bleibt ein Vorschlag. Die Entscheidung treffen weiter Sie.
- Das Etikett „geändert in Version …“ sehen Sie vorerst nicht (siehe Offene Punkte).

## Offene Punkte
- **Kontrolle im Browser steht noch aus.** Diese Phase wurde über Tests geprüft, teils mit echten Daten. Bitte einmal ansehen: Startseite, „Mutter“ (Bedeutungen), „Wärmepumpe“ („Warum?“), `/hilfe` und die Bedienung nur mit Tastatur.
- **Etikett „geändert in Version …“:** Es erscheint erst, wenn die Tabelle `notation_change` gefüllt ist. Das soll der monatliche Abgleich in Phase 8 tun. RVKompass kennt Änderungen deshalb erst ab dem ersten Vergleich, ältere nicht.
- **Stellen jenseits der ersten 20:** Gehört eine Stelle zu einer Bedeutung, steht aber nicht unter den ersten 20 Vorschlägen, erscheint sie nur als Kurzliste mit bis zu 5 Stellen.
- **Filterhinweis:** Die Zahl „N weitere Fundstellen außerhalb der gewählten Hauptgruppen“ zählt alle Fundstellen außerhalb der Auswahl, nicht nur die, die sonst unter den ersten 20 gestanden hätten.
- **Evaluationsläufe:** Es liegen mehrere Berichte vom 12.11.2026 vor (`reports/eval-2026-11-12-65b7dc2*.json`). Der Lauf ohne API-Schlüssel nennt für „nur GND“ einen Hit@10 von 0.5567. Die Tabelle oben nutzt den Lauf mit Schlüssel und Zwischenspeicher (0.546). Bitte beim nächsten Lauf mit frischem Aufruf des Sprachmodells beide Werte sauber nebeneinanderlegen.

## Empfehlung für die nächste Phase
Zuerst den Blick im Browser machen. Fällt dort etwas auf, sollte es vor Phase 8 behoben werden, weil die Oberfläche dann fertig sein soll.

Danach weiter mit Phase 8. Sie liefert den lokalen GND-Abzug und den monatlichen RVK-Abgleich. Dabei sollte der Abgleich auch `notation_change` füllen, damit das Etikett „geändert in Version …“ erstmals Daten hat. Vor der Umstellung auf den lokalen GND-Abzug sollte die Evaluation einmal vollständig mit Sprachmodell laufen, damit ein sauberer Vergleichswert vorliegt.
