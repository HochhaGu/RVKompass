# Phase P3 – GND-Erweiterung

## Übersicht
1. Worum es ging
2. Was sich geändert hat
3. Kennzahlen vorher/nachher
4. Was das für die Nutzung bedeutet
5. Offene Punkte
6. Empfehlung für die nächste Phase

## Worum es ging
Bisher erweiterte RVKompass Suchbegriffe nur über ein Sprachmodell, und das ungeprüft. In den letzten Messungen kam dabei nichts an. P3 erweitert stattdessen über die GND (Gemeinsame Normdatei, ein geprüftes Schlagwort-Verzeichnis). Gesucht wird nach Synonymen sowie Ober-, Unter- und verwandten Begriffen. Mehrdeutige Begriffe wie „Mutter“ oder „Bank“ werden nicht still entschieden: Jede Bedeutung wird einzeln geführt.

## Was sich geändert hat

- **GND-Abfrage**
  - Was: Ein neuer Baustein fragt lobid-gnd (den offenen GND-Dienst des hbz) nach Schlagwörtern zum Suchbegriff. Er übernimmt Vorzugsname, Namensvarianten, Ober-, Unter- und verwandte Begriffe sowie die GND-Sachgruppe (das Fachgebiet des Begriffs).
  - Schutz: Er wartet höchstens 4 Sekunden. Antworten werden 30 Tage zwischengespeichert. Bei einem Fehler gibt es keine Erweiterung, aber keinen Absturz. Nach 3 Fehlern in Folge fragt er nicht mehr an.
  - Eine Bedeutung zählt nur, wenn ihr Name oder eine Namensvariante genau dem Suchbegriff entspricht. Ganze Sätze bekommen deshalb keine GND-Erweiterung. Das ist Absicht, sonst gäbe es Rauschen.
  - Der Baustein ist austauschbar. In P8 kann ein lokaler GND-Abzug an seine Stelle treten, ohne dass sich die Suche ändert.
  - Kein Sprachmodell beteiligt.
  - Wo: `app/services/expansion/gnd.py`.

- **Weg über die GND-Nummer**
  - Was: Ein GND-Begriff findet RVK-Registereinträge mit derselben GND-Nummer, höchstens 10 je Nummer. Das ist der am besten nachvollziehbare Weg.
  - Wo: `app/services/rvk_repository.py` (`get_notations_by_gnd_ids`, dazu Zwischenspeicher `gnd_cache`).

- **Einbindung in die Suche**
  - Was: Jeder GND-Begriff wird im RVK-Register zweifach gesucht: über den gleichen Registerbegriff und über die GND-Nummer. Jeder Treffer trägt eine Begründung mit Herkunft „GND“, Beziehung (z. B. Synonym) und Bedeutung. GND-Begriffe laufen nicht durch die Vektorsuche.
  - Zuschlag auf den Relevanzwert: 0.08 für Treffer über die Bedeutung selbst oder ein Synonym, 0.02 für Ober-, Unter- und verwandte Begriffe. Das war der beste von drei Versuchen. Der Zuschlag ist sichtbar (`gnd_bonus`).
  - Ordnung der Bedeutungen: erst Fachfilter, dann Trefferzahl, dann die Reihenfolge von lobid.
  - Ohne GND-Bedeutungen bleibt das Ergebnis wie vorher. Ein Test sichert das ab.
  - Wo: `app/services/ranking.py`, `app/models.py` (Klasse `GndSense`).

- **Suchseite**
  - Was: Bei mehreren Bedeutungen erscheint „Mehrere Bedeutungen gefunden“ mit Name, Sachgruppe und bis zu 5 Systemstellen je Bedeutung. Bei genau einer steht „GND-Begriff: …“. Der Fachfilter ist vorerst nur ein unsichtbares Formularfeld `fach`. Das Auswahlfeld kommt in P5.
  - Wo: `app/routes/search.py`, `app/templates/_results_table.html`.

- **Einstellungen**
  - Was: GND ein/aus (`ENABLE_GND_EXPANSION`), Adresse, Zeitlimit, Cache-Dauer, Grenzen und die Tabelle Sachgruppe → RVK-Hauptgruppe.
  - Wo: `app/config.py`.

- **Index auf die GND-Nummer**
  - Was: Er macht die Abfrage schneller. Er wird beim nächsten regulären Neuaufbau mit `sync_rvk.py` angelegt. Am bestehenden Datenbestand wurde nichts von Hand geändert.
  - Wo: `app/models.py`, `scripts/sync_rvk.py`.

- **Evaluation**
  - Was: Der Lauf mit Erweiterung nutzt jetzt auch die GND. Der Bericht nennt, wie viele Anfragen GND-Bedeutungen bekamen, und führt mehrdeutige Anfragen einzeln auf.
  - Wo: `scripts/evaluate.py`, `app/services/evaluation.py`.

- **Tests**
  - Was: 53 neue Tests, insgesamt 270. Alle laufen ohne Netz.
  - Wo: `tests/test_gnd_expansion.py`, `tests/test_search_route.py` und Ergänzungen in vier weiteren Testdateien.

## Kennzahlen vorher/nachher
Die Kennzahlen sind Relevanzmaße, keine Wahrscheinlichkeiten. Hit@N heißt: Die erwartete Systemstelle steht unter den ersten N Treffern. MRR ist der Durchschnitt von 1 geteilt durch den Rang des ersten richtigen Treffers.

„Vorher“ ist der Stand am Ende von P2. „Nachher“ ist `reports/eval-2026-11-11-64f8ae8-3.json`.

| Kennzahl | vorher | nachher | Änderung |
|---|---|---|---|
| Hit@1 | 0.196 | 0.258 | +0.062 |
| Hit@5 | 0.402 | 0.464 | +0.062 |
| Hit@10 | 0.484 | 0.557 | +0.072 |
| MRR | 0.285 | 0.343 | +0.058 |
| Hauptgruppen-Treffer | – | – | – |
| Antwortzeit, erste Suche eines Begriffs | – | rund 354 ms | – |
| Antwortzeit, Wiederholung (aus Zwischenspeicher) | – | rund 196 ms | – |

- Die Untergrenze für Hit@10 war 0.4645. Sie wird deutlich übertroffen.
- 46 von 97 Anfragen bekamen GND-Bedeutungen. Der Nutzen ist also echt gemessen.
- Die Verbesserung kommt allein aus der GND. Das Sprachmodell lieferte keine Begriffe, weil kein Schlüssel vorhanden war.
- Von 28 Anfragen mit geänderter Rangfolge wurde nur „Bank“ schlechter (Platz 1 → 3). Deutlich besser wurden „Kostenrechnung“ (11 → 1), „Baustatik“ (14 → 2) und „Mutter“ (vorher nicht in der Trefferliste → 7).
- Der Zuschlag wurde in drei Läufen abgestimmt. Alle Berichte liegen in `reports/` (`eval-2026-11-11-64f8ae8*`). Der dritte Lauf ist der gültige.

Mehrdeutige Anfragen aus dem Goldstandard (ohne Fachfilter, die Evaluation setzt keinen):

| Anfrage | erwartet | Rang vorher | Rang nachher | gefundene GND-Bedeutungen |
|---|---|---|---|---|
| Feder | ZL 4300 | 1 | 1 | Feder, Vogelfeder |
| Mutter | ZL 4620 | – | 7 | Mutter, Mutter <Motiv>, Mutter <Technik> |
| Spannung | ZQ 3400 | – | – | Spannung, Elektrische Spannung, Spannung <Geologie>, Mechanische Spannung |
| Ermüdung | ZM 3200 | 17 | 8 | Ermüdung, Ermüdung <Motiv>, Materialermüdung |
| Bank | QK 000 | 1 | 3 | Bank, Bank <Möbel> |
| Virus | ST 276 | – | – | keine (kein exakt passender Datensatz) |
| Decke | ZH 7300 | 1 | 1 | Decke, Tektonische Decke, Decke <Textilien>, Decke <Geologie> |

## Was das für die Nutzung bedeutet
- Die Vorschläge sind messbar besser, vor allem bei Fachbegriffen aus einem Wort.
- Bei mehrdeutigen Begriffen sieht man, welche Bedeutungen die GND kennt und welche Systemstellen dazugehören. Die Auswahl trifft weiter der Mensch.
- Umschreibungen in ganzen Sätzen profitieren nicht. Dafür ist P4 gedacht.
- Die erste Suche nach einem neuen Begriff dauert etwas länger, weil lobid gefragt wird. Wiederholungen kommen aus dem Zwischenspeicher.
- Fällt lobid aus, liefert die Suche Ergebnisse wie vor P3.
- Die Anzeige „Konfidenz“ in Prozent besteht noch. Sie wird in P5 ersetzt.

## Offene Punkte
- **Bitte prüfen: Zuordnung Sachgruppe → RVK-Hauptgruppe** (`app/config.py`, `GND_SUBJECT_CATEGORY_TO_RVK`).
  - Belegung: Informatik → ST, Werkstoffkunde → ZM, Bautechnik → ZI/ZH, Energietechnik → ZP, Maschinenbau → ZL, Betriebswirtschaftslehre → QP.
  - Abweichung vom Plan: Datenbank-Abfragen außerhalb der App waren in diesem Lauf gesperrt. Die Hauptgruppen stammen deshalb aus dem Goldstandard, nicht aus der Datenbank.
  - Fertigungstechnik fehlt, weil sie im Goldstandard teils als ZL, teils als ZM vorkommt. Elektrotechnik fehlt, weil kein Sachgruppen-Code geprüft war.
  - Bitte fachlich prüfen und ergänzen.
- **Abweichung vom Auftrag:** Die Datenbankfunktion `get_notations_by_gnd_ids` liefert nur die Nummern der Systemstellen. Die Begründungen baut die Suche selbst, weil nur sie den Suchbegriff und die Bedeutung kennt. Für Nutzer:innen ändert das nichts.
- **Testvorlage ist nachgebaut.** Echte lobid-Antworten ließen sich nicht speichern (Bot-Schutz, Netzabrufe gesperrt). Die Vorlage `tests/fixtures/lobid_gnd_mutter_nachgebaut.json` nutzt erfundene Nummern und ist so gekennzeichnet. Die Feldnamen sind durch die 46 echten Antworten in der Evaluation bestätigt. Empfehlung: Diese Seite im Browser öffnen, die Antwort gekürzt speichern und als Vorlage ergänzen: `https://lobid.org/gnd/search?q=Mutter&filter=type:SubjectHeading&format=json&size=5`
- **Unterbegriffe:** Die Rückwärtssuche funktioniert bei lobid. Beispiel: Für „Halbleiter“ kamen „Dotierter Halbleiter“ und „Verbindungshalbleiter“.
- **Fehlermeldung in der Evaluation:** Im Prüfprotokoll steht die Meldung `TypeError: Client.__init__() got an unexpected keyword argument 'proxies'`. Der Lauf selbst ist durchgegangen und hat gültige Kennzahlen geliefert. Die Meldung stammt vermutlich vom Sprachmodell-Baustein, der ohne Schlüssel keine Begriffe liefert. Das ist nicht geklärt. Es sollte vor P4 geprüft werden, denn P4 braucht diesen Baustein.
- **Bot-Schutz bei lobid:** Die App kam in diesem Lauf durch. Ob das dauerhaft so bleibt, ist offen. Ein Ausfall zeigt sich in der Evaluation als `queries_with_gnd_senses = 0`. P8 (lokaler GND-Abzug) macht davon unabhängig.
- **Index auf `register_entry.gnd_id`** fehlt im aktuellen Datenbestand. Er kommt mit dem nächsten Lauf von `sync_rvk.py`.
- **Fachfilter ohne Auswahlfeld:** Er wird von der Evaluation nicht genutzt. Ob er die Bedeutungen sinnvoll ordnet, ist nicht gemessen.
- **„Bank“ rutschte von Platz 1 auf 3:** Treffer über GND-Begriffe mit Zuschlag überholen den erwarteten Treffer. Ob die Tabelle nach der gewählten Bedeutung gefiltert werden soll, ist eine Entscheidung für P5.
- **Zuschlag:** Der beste Wert lag am oberen Rand der drei Versuche. Bei nur 97 Anfragen droht eine Überanpassung an den Goldstandard. Weiter abstimmen sollte man erst mit einem größeren Goldstandard.
- **Altlasten:** Die Anzeige „Konfidenz“ (P5) und der Wert `llm_unverified` (P4) bleiben vorerst.

## Empfehlung für die nächste Phase
P4 (geprüfte Begriffe vom Sprachmodell) kann auf dem GND-Dienst aufbauen. Vorschläge des Sprachmodells werden dann gegen die GND geprüft. Davon würden auch Umschreibungen in ganzen Sätzen profitieren. Vorher sollten drei Dinge erledigt sein:
1. Die Zuordnungstabelle Sachgruppe → RVK-Hauptgruppe prüfen.
2. Die Fehlermeldung des Sprachmodell-Bausteins klären.
3. Eine echte lobid-Antwort als Testvorlage ergänzen.
