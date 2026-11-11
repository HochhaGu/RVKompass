# Das Steuerprogramm (Orchestrator)

## Übersicht
1. Was es tut
2. Ablauf einer Phase
3. Wann du eine Mail bekommst
4. Befehle
5. Wenn das Programm anhält: antworten und fortsetzen
6. Statusseite
7. Sicherheit: was der Roboter darf und was nicht
8. Einrichtung (einmalig, mit sudo)
9. Dateien und Ordner
10. Fehlerbilder

## 1. Was es tut
Das Steuerprogramm arbeitet die Phasen P0 bis P8 aus `prompts/PROMPTS.md` nacheinander ab, ohne
dass du am Bildschirm sitzen musst. Für jede Phase lässt es Claude Code einen Plan schreiben,
diesen von einer zweiten Claude-Instanz prüfen, die Umsetzung erledigen und das Ergebnis mit festen
Prüfungen („Gates“) kontrollieren. Ist alles grün, landet die Phase per Pull Request in `develop`.
`main` wird nie angefasst. Du bekommst nur Mails, wenn du gebraucht wirst oder etwas fertig ist.

Der Roboter arbeitet in einer eigenen Arbeitskopie `~/dev/rvkompass-phasen`. Dein Ordner
`~/dev/rvkompass` bleibt unberührt. In der Arbeitskopie gibt es keine `.env`; `data/` ist dort nur
ein Verweis auf deinen Datenbestand.

## 2. Ablauf einer Phase
| Schritt | Was passiert |
|---|---|
| Branch | Branch `phase-N-<stichwort>` wird von `origin/develop` angelegt. |
| Plan | Claude erstellt im Plan-Modus einen Plan (ändert dabei nichts). Ablage: `orchestrator/runs/PN/plan.md`. |
| Prüfung | Eine zweite, unabhängige Claude-Instanz prüft den Plan gegen Phasen-Auftrag und CLAUDE.md und antwortet mit `{"freigabe": …, "gruende": […]}`. |
| Überarbeitung | Bei Ablehnung wird der Plan **einmal** mit den Gründen überarbeitet und erneut geprüft. Zweite Ablehnung → Pause. |
| Umsetzung | Claude setzt den Plan mit enger Erlaubnisliste um (siehe Abschnitt 7). |
| Gates | `ruff check .`, `pytest -q`, ab P1 `scripts/evaluate.py`. Hit@10 darf gegenüber dem letzten Bericht höchstens um 0,02 sinken. |
| Reparatur | Ist ein Gate rot: **ein** Reparaturversuch, dann Gates erneut. Wieder rot → Pause. |
| Bericht | Claude schreibt `docs/phasen/phase-N.md` (beginnt mit Übersicht, Klartext). |
| Ausliefern | Commit, Push des Phasen-Branches, Pull Request nach `develop`, Merge. |

Feste Haltepunkte: nach **P0** (du schaltest das Repo öffentlich) und nach **P8** (du gibst die
Live-Schaltung frei).

## 3. Wann du eine Mail bekommst
| Mail | Wann |
|---|---|
| Eingreifen nötig | sofort, wenn das Programm anhält (Rückfrage, rote Gates, Haltepunkt, technischer Fehler) |
| Alles fertig | sofort nach P8 |
| Wochenbrief | montags 08:00, mit den Phasen, die seit dem letzten Brief fertig wurden |
| Claude-Anmeldung läuft bald ab | einmalig, 5 Tage vor Ablauf |

**Keine** Mail gibt es, wenn das Claude-Nutzungslimit erreicht ist: Das Programm wartet dann
und versucht es stündlich erneut.

**Mailversand fehlgeschlagen?** Das hält das Steuerprogramm **nie** an – die Phasen laufen normal
weiter. Stattdessen:
- Der Fehler steht im Protokoll (`journalctl -u rvkompass-orchestrator`,
  `orchestrator/logs/orchestrator.log`) und in den Meldungen.
- Die Mail kommt in die Warteschlange `orchestrator/logs/mail_warteschlange/` (eine Datei je Mail).
- Die Statusseite zeigt oben den Kasten „Mails warten auf Versand“ mit Betreff, Anzahl Versuche,
  letztem Fehler und nächstem Versuch; `--status` zeigt dasselbe im Terminal.
- Alle 10 Minuten (und sofort nach einem Neustart) wird der Versand erneut versucht. Ist der
  Mailserver nicht erreichbar, bricht der Versuch nach der ersten Mail ab, statt jede Mail einzeln
  auf eine Zeitüberschreitung warten zu lassen.
- Das gilt auch für den Wochenbrief: Schlägt er fehl, holt der Dienst ihn nach.

## 4. Befehle
Alle Befehle im Ordner `~/dev/rvkompass`:

| Befehl | Wirkung |
|---|---|
| `.venv/bin/python -m orchestrator --status` | Stand anzeigen und Statusseite neu schreiben |
| `.venv/bin/python -m orchestrator --resume` | nach einer Pause weitermachen (entfernt auch die PAUSE-Datei) |
| `touch orchestrator/PAUSE` | nach dem laufenden Schritt anhalten (ohne Mail) |
| `.venv/bin/python -m orchestrator --dry-run` | zeigen, was passieren würde – ändert nichts, sendet nichts |
| `.venv/bin/python -m orchestrator --dry-run --only P3` | dasselbe für eine einzelne Phase |
| `.venv/bin/python -m orchestrator --only P0` | genau eine Phase im Vordergrund abarbeiten (Dienst vorher stoppen) |
| `.venv/bin/python -m orchestrator --test-mail` | Testmail senden |
| `.venv/bin/python -m orchestrator --send-digest` | Wochenbrief sofort senden |
| `journalctl -u rvkompass-orchestrator -f` | Protokoll des Dienstes live mitlesen |

Ohne Schalter läuft das Programm im Dauerbetrieb – so startet es der Dienst.

### ⚠ Dienst steuern: `restart` startet auch einen gestoppten Dienst

> **`sudo systemctl restart rvkompass-orchestrator` startet den Dienst immer – auch wenn er
> vorher gestoppt war.** Damit beginnt bzw. setzt das Steuerprogramm sofort die Phasenarbeit fort.
> Nur verwenden, wenn der Dienst laufen **soll**.

| Du willst … | Befehl | Startet den Dienst? |
|---|---|---|
| eine geänderte Unit-Datei übernehmen | `sudo cp deploy/systemd/rvkompass-orchestrator.service /etc/systemd/system/` und danach **nur** `sudo systemctl daemon-reload` | **nein** |
| nachsehen, ob er läuft | `systemctl is-active rvkompass-orchestrator` | nein |
| neuen Code oder neue Unit in einem **laufenden** Dienst wirksam machen | erst `systemctl is-active rvkompass-orchestrator` prüfen; nur bei `active`: `sudo systemctl restart rvkompass-orchestrator` | ja |
| starten (P0 bzw. nächster Schritt beginnt) | `sudo systemctl start rvkompass-orchestrator` | ja |
| starten und nach jedem Reboot automatisch starten | `sudo systemctl enable --now rvkompass-orchestrator` | ja |
| anhalten | `sudo systemctl stop rvkompass-orchestrator` | – |

`daemon-reload` lädt nur die Unit-Dateien neu und startet nichts. Ein gestoppter Dienst übernimmt
die neue Unit beim nächsten `start` von selbst. Wird der Dienst mitten in einem Schritt gestoppt,
wiederholt er diesen Schritt beim nächsten Start. Soll eine Phase stattdessen ganz von vorn
beginnen, muss sie zurückgesetzt werden (Zustand in `state.json` und lokaler Phasen-Branch in
`~/dev/rvkompass-phasen`).

## 5. Wenn das Programm anhält: antworten und fortsetzen
1. Mail „Eingreifen nötig“ lesen: Sie nennt Phase, Schritt, Grund und ggf. die Frage.
2. Wenn eine Frage gestellt wurde, die Antwort in `orchestrator/antworten/PN.md` schreiben
   (z. B. `nano orchestrator/antworten/P7.md`). Einfacher Text genügt. Die Datei gilt für alle
   weiteren Läufe dieser Phase – du kannst sie ergänzen.
3. `.venv/bin/python -m orchestrator --resume` ausführen. Der Dienst macht innerhalb einer Minute
   mit dem angehaltenen Schritt weiter.

Bekannte Stellen, an denen gefragt wird: P0 (Lizenztext EUPL 1.2, falls nicht verlässlich
beschaffbar), P1 (offene Katalogschnittstellen vorlegen), P7 (vor jedem Modell-Download), jede
neue Abhängigkeit (pip ist für den Roboter gesperrt – du installierst selbst).

Was die Claude-Läufe genau getan haben, steht in `orchestrator/runs/PN/` (Prompt und Antwort je
Schritt).

## 6. Statusseite
`https://rvkompass.ghochhauser.at/status/` – passwortgeschützt (Zugang aus `STATUS_USER` und
`STATUS_PASSWORD` in `.env`). Zeigt Zustand, Phase, Schritt, alle Phasen mit Kennzahlen
(Hit@1/5/10, MRR), Pull-Request-Links und die letzten 20 Meldungen. Die Seite wird nach jedem
Schritt neu geschrieben und lädt sich im Browser jede Minute neu. Kann das Programm nicht nach
`/srv/rvkompass-status` schreiben, legt es die Seite ersatzweise unter
`orchestrator/status_out/index.html` ab.

## 7. Sicherheit: was der Roboter darf und was nicht
- **Kein Lesen von Schlüsseln:** Die Arbeitskopie enthält keine `.env`; Lesen von `.env`-Dateien
  ist zusätzlich verboten. Schlüssel liest nur das Steuerprogramm selbst; die Evaluation bekommt
  die App-Schlüssel als Umgebungsvariablen. Bekannte Schlüsselwerte werden in Protokollen und
  Mails geschwärzt.
- **Kein Push, kein Branch-Wechsel, kein Commit, kein Löschen (`rm`), kein `pip`, kein `sudo`,
  kein `curl`, kein `gh`** für den Roboter. Das alles macht – soweit nötig – nur das
  Steuerprogramm.
- **Erlaubt** sind Lesen/Schreiben im Projekt, `pytest`, `ruff`, `python scripts/…`, Git-Lesebefehle
  und Webzugriffe nur auf die Domains, die in `orchestrator/phases.yaml` je Phase stehen.
- **Modus `dontAsk`:** Alles, was nicht ausdrücklich erlaubt ist, wird automatisch abgelehnt.
- **Vor jedem Commit** prüft das Steuerprogramm: keine Dateien aus `data/`, keine `.env`, keine
  Schlüsselmuster und keine Werte aus `.env` im Diff. Schlägt das an → Pause statt Push.
- **Nie main:** Push und Merge mit `main` als Ziel verweigert das Programm hart.
- Ehrliche Grenze: `python scripts/…` kann theoretisch beliebigen Code ausführen. Deshalb liegen
  die harten Schutzschichten außerhalb des Roboters (keine `.env` in der Arbeitskopie, Push nur
  durch das Programm, Live-Schaltung und `main` nur durch dich).

## 8. Einrichtung (einmalig, mit sudo)
Diese Befehle führst du selbst aus, im Ordner `~/dev/rvkompass`:

```bash
# 1. Ordner für die Statusseite: gehört dir, Caddy darf lesen
sudo mkdir -p /srv/rvkompass-status
sudo chown guenther:caddy /srv/rvkompass-status
sudo chmod 2750 /srv/rvkompass-status

# 2. Passwortschutz aus STATUS_USER/STATUS_PASSWORD (.env) erzeugen
bash deploy/caddy/make_status_auth.sh | sudo tee /etc/caddy/rvkompass-status.auth >/dev/null
sudo chown root:caddy /etc/caddy/rvkompass-status.auth
sudo chmod 640 /etc/caddy/rvkompass-status.auth

# 3. Caddy-Konfiguration einspielen (alte Datei wird gesichert), prüfen, neu laden
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.vor-rvkompass
sudo mkdir -p /etc/caddy/sites
sudo cp deploy/caddy/Caddyfile /etc/caddy/Caddyfile
sudo cp deploy/caddy/rvkompass.caddy /etc/caddy/sites/rvkompass.caddy
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
sudo systemctl reload caddy

# 4. Dienste installieren
sudo cp deploy/systemd/rvkompass-orchestrator.service \
        deploy/systemd/rvkompass-orchestrator-digest.service \
        deploy/systemd/rvkompass-orchestrator-digest.timer /etc/systemd/system/
sudo systemctl daemon-reload   # lädt nur neu, startet nichts
sudo systemctl enable --now rvkompass-orchestrator-digest.timer

# 5. Erst wenn du startklar bist: Steuerprogramm starten (beginnt mit P0)
sudo systemctl enable --now rvkompass-orchestrator
```

Port 80 und 443 müssen von außen erreichbar sein, damit Caddy das HTTPS-Zertifikat holen kann.
Ändert sich STATUS_PASSWORD, Schritt 2 wiederholen und `sudo systemctl reload caddy`.

**Claude-Anmeldung:** Der Dienst nutzt die Anmeldung von Claude Code des Benutzers guenther.
Läuft sie ab, hält das Programm mit einer Mail an. Für den Dauerbetrieb empfiehlt sich eine
langlebige Anmeldung: `claude setup-token`.

## 9. Dateien und Ordner
| Pfad | Inhalt |
|---|---|
| `orchestrator/phases.yaml` | Phasen, Haltepunkte, Erlaubnisliste, Schwellen, Pfade |
| `orchestrator/prompts/` | Rahmen für den Roboter, Plan-, Prüf- und Berichtsauftrag |
| `orchestrator/templates/` | Vorlagen für Mails und Statusseite |
| `orchestrator/state.json` | aktueller Zustand (nicht versioniert); Neustart macht hier weiter |
| `orchestrator/antworten/` | deine Antworten auf Rückfragen (nicht versioniert) |
| `orchestrator/runs/` | Prompts und Antworten aller Claude-Läufe (nicht versioniert) |
| `orchestrator/logs/` | Protokoll, Mail-Warteschlange, Wochenbrief-Merkliste (nicht versioniert) |
| `deploy/caddy/`, `deploy/systemd/` | Server-Konfiguration |
| `tests/test_orchestrator/` | Tests für Gate-, Pause- und Schutzlogik |

## 10. Fehlerbilder
| Anzeige | Bedeutung und Abhilfe |
|---|---|
| „wartet auf Nutzungslimit“ | Claude-Kontingent erschöpft; stündlicher neuer Versuch, nichts zu tun. `--resume` versucht sofort. |
| „wartet nach Störung“ | Claude kurz nicht erreichbar; neuer Versuch nach 10 Min, nach 6 Fehlversuchen Pause mit Mail. |
| „Claude-Anmeldung ungültig oder abgelaufen“ | `claude setup-token` oder `claude` → `/login`, dann `--resume`. |
| „Schutzprüfung hat angeschlagen“ | Im Commit wären verbotene Dateien oder ein Schlüssel gelandet. Arbeitskopie prüfen (`git -C ~/dev/rvkompass-phasen status`), bereinigen, `--resume`. |
| „Gates auch nach dem Reparaturversuch rot“ | Details in der Mail und in `orchestrator/runs/PN/gates-*.md`. Selbst beheben oder per Antwortdatei Hinweise geben, dann `--resume` (ein neuer Reparaturversuch ist dann wieder frei). |
| „Mails warten auf Versand“ | Mailversand klappt nicht, Ablauf läuft weiter. `--test-mail` zeigt den Fehler; häufig sind die Mail-Ports beim Anbieter gesperrt oder SMTP-Angaben in `.env` falsch. Nach der Behebung gehen die wartenden Mails innerhalb von 10 Minuten raus. |
| „läuft bereits“ bei `--only` | Der Dienst arbeitet; zuerst `sudo systemctl stop rvkompass-orchestrator`. |
