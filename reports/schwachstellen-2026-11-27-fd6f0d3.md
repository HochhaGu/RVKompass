# RVKompass – Schwachstellen (P12)

- Zeitpunkt: 2026-11-27T09:44:39+00:00
- Evaluationsbericht: reports/eval-2026-11-27-fd6f0d3.json
- Diagnosebericht: reports/scoring-diagnose-2026-11-27-fd6f0d3.json
- Anfragen: 501

Ursachen in fester Prüfreihenfolge (die erste zutreffende zählt). „GND-Bedeutung führt woanders hin“ ist eine Annäherung an „falsche Bedeutung gewählt“: Es gibt GND-Bedeutungen, die erste erwartete Stelle hat keinen GND-Fundweg und keine der ersten drei Vorschläge liegt in einer erwarteten Hauptgruppe.

## Häufigkeit der Ursachen (Anfragen ohne Treffer unter den ersten 10)

| Ursache | alle | alt (bis P11) | neu Technik | neu andere Fachgebiete |
|---|---|---|---|---|
| richtige Stelle nicht unter den Kandidaten | 183 | 24 | 80 | 79 |
| keine GND-Bedeutung und kein Treffer über das RVK-Register | 33 | 5 | 19 | 9 |
| GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung) | 19 | 3 | 5 | 11 |
| nur über inhaltliche Ähnlichkeit (Vektor) gefunden, zu weit hinten | 1 | 0 | 1 | 0 |
| gefunden, aber hinter Platz 20 abgeschnitten | 13 | 1 | 4 | 8 |
| gefunden, aber hinter Platz 10 | 17 | 5 | 6 | 6 |

Davon Homonyme (Typ „Mehrdeutig“): 33

## Schwächste Fachgebiete (MRR, mindestens 5 Anfragen)

| Fachgebiet | Anfragen | Hit@1 | Hit@10 | MRR |
|---|---|---|---|---|
| Pädagogik und Bildung | 10 | 0 | 0 | 0 |
| Sprache und Literatur | 9 | 0 | 0.2222 | 0.0401 |
| Verfahrens- und Umwelttechnik | 5 | 0 | 0.2 | 0.05 |
| Land- und Forstwirtschaft, Ernährung | 10 | 0 | 0.1 | 0.0525 |
| Medizin und Pflege | 10 | 0 | 0.1 | 0.0553 |
| Biologie und Chemie | 9 | 0 | 0.1111 | 0.0761 |
| Recht | 10 | 0 | 0.3 | 0.0917 |
| Musik | 10 | 0.1 | 0.2 | 0.11 |

## Typen nach MRR (mindestens 5 Anfragen)

| Typ | Anfragen | Hit@1 | Hit@10 | MRR |
|---|---|---|---|---|
| Fachfremd umschrieben | 154 | 0.1104 | 0.3247 | 0.1794 |
| Mehrdeutig | 49 | 0.2041 | 0.3265 | 0.2473 |
| Spezifisch/Kompositum | 102 | 0.1765 | 0.3824 | 0.2525 |
| Englisch/Jargon | 54 | 0.3704 | 0.5185 | 0.4116 |
| Fachbegriff | 142 | 0.4789 | 0.7183 | 0.5735 |

## Die 15 schwächsten Anfragen – alt (bis P11)

| Anfrage | Typ | erwartet | Top-3 | Rang | Rang ungekürzt | Ursache | Homonym |
|---|---|---|---|---|---|---|---|
| Automatisierungstechnik | Fachbegriff | ZQ 6000 | QP 225, ZI 8860, MS 4850 | – | 22 | GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung) | nein |
| Halbleiter | Fachbegriff | ZN 4800 | UP 3250, UP 3200, UP 3050 | – | 26 | GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung) | nein |
| Leichte Materialien für Flugzeuge | Fachfremd umschrieben | ZL 3230 | ZO 7210, ZO 7205, ZO 7340 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Messunsicherheit bestimmen | Spezifisch/Kompositum | ZQ 3115 | QC 020, QH 233, MT 23100 | – | 31 | keine GND-Bedeutung und kein Treffer über das RVK-Register | nein |
| PID-Regler Auslegung | Spezifisch/Kompositum | ZQ 5000 | HF 571, EE 1650, EE 1660 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Smart Grid | Englisch/Jargon | ZP 4450 | ZN 8500, ZG 9225, ZN 8550 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Spannung | Mehrdeutig | ZQ 3400 | UF 1800, TG 3500, UF 2000 | – | 34 | GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung) | ja |
| Temperaturmessung in Industrieöfen | Spezifisch/Kompositum | ZP 3279 | ZQ 3800, UH 5740, ZL 7400 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Warum Häuser nicht einstürzen | Fachfremd umschrieben | ZI 3300 | ZH 5000, ZH 5260, ZH 8950 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Warum Metall rostet | Fachfremd umschrieben | ZM 7510 | UQ 7000, UQ 7360, UQ 7020 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Wie eine Fabrik von selbst läuft | Fachfremd umschrieben | ZQ 6000 | ZL 3170, ZL 3180, ZM 9000 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Wirkungsgrad von Gasturbinenkraftwerken | Spezifisch/Kompositum | ZL 5475 | ZP 3300, ZP 3580, ZP 3275 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Zugversuch an Stahlproben | Spezifisch/Kompositum | ZM 3600 | ZK 8200, ZI 4941, ZI 7200 | – | 25 | gefunden, aber hinter Platz 20 abgeschnitten | nein |
| Additive Fertigung von Metallbauteilen | Spezifisch/Kompositum | ZM 9045 | ZK 8200, ZL 3235, ZM 8475 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Computer lernt aus Beispielen | Fachfremd umschrieben | ST 301 | AL 40450, AL 34330, SR 9000 | – | – | richtige Stelle nicht unter den Kandidaten | nein |

## Die 15 schwächsten Anfragen – neu Technik

| Anfrage | Typ | erwartet | Top-3 | Rang | Rang ungekürzt | Ursache | Homonym |
|---|---|---|---|---|---|---|---|
| Big Data | Englisch/Jargon | ST 530 | AK 54535, MR 2200, AP 95500 | – | 25 | GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung) | nein |
| Blockchain | Englisch/Jargon | ST 610 | QK 305, MF 3360, ZN 8580 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Bogen | Mehrdeutig | ZI 3300 | LK 80130, ZH 7400, AL 27300 | – | – | richtige Stelle nicht unter den Kandidaten | ja |
| Bus | Mehrdeutig | ST 185 | ZO 4450, ZO 4430, ZQ 6150 | – | – | richtige Stelle nicht unter den Kandidaten | ja |
| Destillation in der chemischen Industrie | Spezifisch/Kompositum | VN 7000 | ZL 3930, QR 525, PF 450 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| DevOps | Englisch/Jargon | ST 230 | NH 9300, BL 2870, EE 7926 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Dichtungstechnik | Fachbegriff | ZL 4400 | ZM 8300, ZN 8770, ZM 8315 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Die Oberfläche von Metall härter machen | Fachfremd umschrieben | ZM 7610 | UP 7560, UQ 7000, UQ 7340 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Digital Twin | Englisch/Jargon | ZQ 5000 | AP 99431, AP 99449, ST 340 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Druck | Mehrdeutig | UP 2500, UX 6000, ZL 7200, ZM 8200, AN 28000, AM 23000, AN 25400 | NK 4940, RC 35238, RC 40238 | – | – | richtige Stelle nicht unter den Kandidaten | ja |
| E-Mobility | Englisch/Jargon | ZO 4480 | QR 760, ST 273, ST 207 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Ein Bauteil am Computer entwerfen | Fachfremd umschrieben | ZG 9146 | ST 150, ZN 4200, ST 166 | – | 55 | keine GND-Bedeutung und kein Treffer über das RVK-Register | nein |
| Eine Fabrik am Bildschirm überwachen | Fachfremd umschrieben | ZM 9240 | ZG 9280, ZG 9225, PF 269 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Entscheiden, ob sich eine neue Maschine lohnt | Fachfremd umschrieben | QP 720 | ZL 7130, ZL 5000, ZG 9225 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Festigkeitsberechnung mit der Finite-Elemente-Methode | Spezifisch/Kompositum | ZI 3310 | QH 160, UP 4000, SK 910 | – | – | richtige Stelle nicht unter den Kandidaten | nein |

## Die 15 schwächsten Anfragen – neu andere Fachgebiete

| Anfrage | Typ | erwartet | Top-3 | Rang | Rang ungekürzt | Ursache | Homonym |
|---|---|---|---|---|---|---|---|
| Abseits | Mehrdeutig | ZY 2021 | QP 632, ZH 7300, ZL 3760 | – | – | richtige Stelle nicht unter den Kandidaten | ja |
| Als Fabriken und Dampfmaschinen alles veränderten | Fachfremd umschrieben | NW 3900, NW 3100 | ZL 5300, ZL 3170, ZL 5200 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Alte Gebäude erhalten | Fachfremd umschrieben | LH 60310 | ZH 7050, ZH 6750, ZH 7080 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Ausdauertraining im Alter | Spezifisch/Kompositum | ZX 7550 | MS 2600, QX 400, MT 26200 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Ausgebrannt sein im Job | Fachfremd umschrieben | CU 3000 | AR 17100, MS 5200, AR 17450 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Barock | Fachbegriff | LH 65760, GH 1004 | AP 64700, EC 3920, EK 2582 | – | 45 | GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung) | nein |
| Bestandsentwicklung | Fachbegriff | AN 71000, AN 70000 | ZI 3950, AP 94400, AP 94300 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Blasmusikkapellen in Österreich | Spezifisch/Kompositum | LC 87160, LS 20390 | AM 82247, AM 83880, DQ 5007 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Blended Learning | Englisch/Jargon | DP 2050 | CM 6000, GB 3031, GB 3030 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| CRISPR | Englisch/Jargon | WG 3450, WC 4460 | ZL 3800, UQ 5400, ZL 6100 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Compliance | Englisch/Jargon | PE 380, QP 341 | CV 5000, WT 3600, WT 3604 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Darf ich Fotos von fremden Leuten ins Netz stellen | Fachfremd umschrieben | PZ 3700 | AP 95200, AP 96950, AP 97800 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Denkmalpflege von Kirchenbauten | Spezifisch/Kompositum | BR 5890, LH 60300, NZ 28042 | BO 9550, BO 9470, BS 4500 | – | 59 | gefunden, aber hinter Platz 20 abgeschnitten | nein |
| Dialekte in Oberösterreich | Spezifisch/Kompositum | LC 60160, GD 2420 | DQ 5007, GB 2914, AL 60439 | – | – | richtige Stelle nicht unter den Kandidaten | nein |
| Dinge ausleihen statt kaufen | Fachfremd umschrieben | QR 790, ZH 9317, AN 66100, AN 73000 | ND 6080, QL 632, QL 700 | – | – | richtige Stelle nicht unter den Kandidaten | nein |

## Vorschläge nach Wirkung

1. richtige Stelle nicht unter den Kandidaten: 183 Anfragen
2. keine GND-Bedeutung und kein Treffer über das RVK-Register: 33 Anfragen
3. GND-Bedeutung führt woanders hin (Annäherung: falsche Bedeutung): 19 Anfragen
4. gefunden, aber hinter Platz 10: 17 Anfragen
5. gefunden, aber hinter Platz 20 abgeschnitten: 13 Anfragen
6. nur über inhaltliche Ähnlichkeit (Vektor) gefunden, zu weit hinten: 1 Anfragen
