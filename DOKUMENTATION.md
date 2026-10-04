# Dokumentation paperless-jev

**Deutsch** · [English](DOCUMENTATION.md)

## Architektur

```
Paperless NGX ──Webhook "Dokument hinzugefügt"──▶ paperless-jev ──POST /v1/systemone──▶ TypeSafe (Jev)
      ▲                  (oder Polling)              │
      └──────────── REST: GET Dokument, PATCH ───────┘
```

| Modul | Aufgabe |
|---|---|
| `app.py` | FastAPI: Web-UI, `/hook/{instanz}`, `/health`, Start der Hintergrundverarbeitung |
| `processor.py` | Warteschlange (asyncio), Polling, Verarbeitung, Zurückschreiben nach Paperless |
| `classifier.py` | baut die Jev-Anfrage und wertet sie aus (reine Funktionen, getestet) |
| `candidates.py` | findet Datumsangaben und Korrespondenten-Kandidaten per Code |
| `llm.py` / `titles.py` | optional: lokales Sprachmodell (Ollama oder OpenAI-kompatibel, z. B. SGLang/vLLM) für Titel und für Beschreibungen aus Stichworten |
| `paperless.py` / `jev.py` | schlanke HTTP-Clients (Paperless-ngx 2.x und 3.x, TypeSafe System One) |
| `config.py` / `db.py` / `vault.py` | Einstellungen, Instanzen, Beschreibungen, Jobs in SQLite; Tokens mit Fernet verschlüsselt |
| `static/review.js` / `static/scan.js` | Review mit pdf.js, Scanner mit OpenCV.js und jsPDF (im Browser) |

Alles Persistente liegt in `/data`: `paperless-jev.db` und – ohne `PJ_SECRET_KEY` – `secret.key`.
**Beide Dateien zusammen sichern**, sonst sind die gespeicherten Tokens nicht mehr lesbar.

## Ablauf pro Dokument

1. Dokument laden; Dokumente mit dem Tag *ai-ignorieren* werden übersprungen.
2. Stammdaten der Instanz laden (5 Minuten Cache).
3. Optional ähnliche, bereits abgelegte Dokumente (`more_like_id`) als Kontext.
4. **Eine** Jev-Anfrage mit allen Fragen:
   - `choice` Dokumenttyp, Korrespondent, Speicherpfad – Optionen = Paperless-Stammdaten + „none of these“;
     pro Option die Beschreibung und (Typ, Speicherpfad) Titel bereits abgelegter Dokumente als Beispiele
   - `choice` Ausstellungsdatum – Optionen = per Regex gefundene Datumsangaben inkl. Kontext
   - je Tag ein `noul` („trifft Tag X zu?“) – für neue Tags und, zur **Gegenprüfung**, für die schon gesetzten
5. Entscheidung nach Modus:
   - **Probelauf**: nur protokollieren.
   - **Nur Vorschläge**: Tag *ai-review* setzen, Review-Queue.
   - **Automatisch**: sichere Werte setzen. Ist noch ein Feld leer, ohne sicheren Wert → *ai-review*,
     sonst *ai-klassifiziert* + Posteingang entfernen. Unsichere Tag-Vorschläge allein lösen kein Review
     aus (umschaltbar) – sie erscheinen aber im Review, wenn das Dokument ohnehin geprüft wird.
   - **Widersprüche**: Widerspricht Jev einem vorhandenen Wert sicher, kommt das Dokument ins Review; ab der
     Schwelle *Vorhandenen Wert ersetzen* (z. B. 0.98) korrigiert Jev ihn selbst. Ein schon gesetzter Tag, den
     Jev sicher für falsch hält, schickt das Dokument ebenfalls ins Review – entfernt wird nie automatisch.
6. Im Review bestätigte/korrigierte Werte werden geschrieben; Korrekturen werden gezählt
   (Übersicht) und dienen zum Nachjustieren von Beschreibungen und Schwellen.

## Review

Ein Dokument nach dem anderen („3 von 22“). Das PDF wird mit pdf.js direkt im Review gerendert; Werte, die Jev
gewählt hat (Absender, Typ, Datum), sind in der Textebene markiert – grün sicher, gelb unsicher. Felder lassen sich
über Auswahl-Blätter ändern (Jevs Vorschläge mit Wahrscheinlichkeit, heutiger Wert, Suche), Tags sind Chips; laut
Jev falsche vorhandene Tags sind vorab abgewählt und werden beim Übernehmen entfernt. Nach Übernehmen oder
Verwerfen folgt das nächste Dokument.

- **Alle erneut prüfen** klassifiziert alle Review-Dokumente mit der aktuellen Konfiguration neu; ein neuer Lauf
  ersetzt ältere Review-Einträge desselben Dokuments (Status *ersetzt*).
- **Dokument löschen** löscht in Paperless (Papierkorb, wiederherstellbar) und markiert die Einträge als *gelöscht*.

## Designentscheidungen

- **Jev nur für Entscheidungen.** Laut [Jev-1.13-Jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
  sind Rechnen, Datumsvergleiche und Zählen schwach – Datumskandidaten findet daher Code, Jev wählt nur.
- **Englische Fragen, Beschreibungen in der Sprache der Oberfläche.** Jev ist primär auf Englisch
  trainiert, die festen Fragen sind darum englisch. Für die Beschreibungen hat ein Vergleich keinen
  Vorteil von Englisch gezeigt (siehe *Messungen*), sie bleiben in der Sprache der Oberfläche.
- **Vorhandene Werte bleiben** (Einstellung „überschreiben“ aus), damit Paperless-eigenes Matching
  und manuelle Zuordnungen Vorrang haben – ausser Jev widerspricht sehr sicher (eigene, einstellbare Schwelle,
  Standard aus). Ausnahme Datum: Paperless setzt immer eines, Jev korrigiert nur bei hoher Confidence.
- **Ablage-Konventionen über Beispiele.** Jev liest wörtlich: „Gutschriftsanzeige“ landet ohne Hilfe bei
  *Gutschrift*, auch wenn du solche Belege unter *Kontoauszug* ablegst. Deshalb bekommt jede Option die
  Titel von bis zu 5 bereits abgelegten Dokumenten (nicht im Posteingang) als strukturiertes Kriterium
  (`{"what": …, "titles_of_documents_already_filed_here": […]}`, siehe docs.typesafe.ai/primitives/advanced).
  Die Beispiele werden eine Stunde gecacht.
- **Korrespondenten-Vorfilter** erst ab 255 Einträgen (Choice-Limit). Ein früherer Vorfilter ab 30
  Einträgen hat im ersten Probelauf die richtigen Korrespondenten aussortiert.
- **Text-Kürzung** auf `max_chars` (Anfang 70 %, Ende 30 %): Absender, Datum und Beträge stehen fast immer dort; Jev erlaubt 32k Tokens für State + längste Frage.
- **Kein Redis/Celery.** Für Posteingangs-Volumen reicht eine In-Process-Queue; offene Jobs werden nach einem Neustart aus SQLite wieder eingereiht.
- **Modell pinnen**: `jev-latest` wandert bei Releases mit; wer Schwellen kalibriert hat, trägt eine feste Version ein (z. B. `jev-1.13.0`).

## Titel

Jev formuliert keinen Text. Titel entstehen darum wahlweise
- **per Sprachmodell** (empfohlen, ersetzt paperless-gpt): nach der Klassifizierung erzeugt ein lokales Modell
  einen Titel – über Ollama (z. B. `qwen3:8b`) oder eine OpenAI-kompatible API (z. B. SGLang mit
  `qwen3.8-flash-next`); bei Qwen3 wird der Denkmodus abgeschaltet. Als Stilvorlage dienen bis zu 8 Titel bereits abgelegter Dokumente
  desselben Korrespondenten bzw. Typs. Jahreszahlen, Daten und Personennamen werden vermieden, weil
  Paperless sie separat speichert. Dauer ca. 1-2 s pro Dokument.
- **per Vorlage**, z. B. `{document_type} {correspondent} {created:%Y-%m}`,
- oder gar nicht.

Antworten des Sprachmodells werden geprüft: Rückfragen, Erklärungen oder Markdown werden verworfen und einmal
neu angefragt; ist ein Titel nur zu lang, wird um eine kürzere Fassung gebeten (bis 100 Zeichen akzeptiert).
Kaputte Titel im Archiv (z. B. Reste anderer Tools) gehen nicht als Stilvorlage mit. *Titel vorschlagen* im
Review zeigt den Titel vorab, ohne etwas zu schreiben.

Ein im Review eingetragener Titel hat Vorrang. Paperless-Workflows mit Auslöser *Dokument aktualisiert*,
die einen Titel setzen (z. B. „Kontoauszug {{ created_month_name }} {{ created_year }}“), laufen nach
paperless-jev und haben damit das letzte Wort.

## Beschreibungen aus Stichworten

Unter *Beschreibungen* genügen Stichworte („Bankbelege, Gutschrift, Belastung – nicht: Steuerbescheinigung“).
Beim Speichern formuliert das Sprachmodell geänderte Einträge zu einer Beschreibung aus – in der Sprache der
Oberfläche, mit „Nicht: …“ bzw. „Not: …“ für Ausschlüsse – und nutzt dazu die Titel bereits abgelegter
Dokumente. Gespeichert werden beide Fassungen: die Stichworte (im Eingabefeld) und der Text für Jev
(darunter mit „→“). Erfundene Ausschlüsse werden entfernt, wenn die Stichworte kein „nicht/kein/ohne“ enthalten.
Ein Eintrag wird nur neu ausformuliert, wenn sich seine Stichworte ändern.

Die Zuweisungsregeln aus Paperless (Suchbegriff und Algorithmus) stehen grau beim Eintrag. Unter *Regeln*
lassen sie sich zusätzlich als Hinweis an Jev mitgeben (Standard: aus, siehe *Messungen*).

## Tags ausmisten und neu prüfen

- **Deaktivierte Tags** (unter Beschreibungen) schlägt Jev nie vor. Steht einer auf einem Dokument, kommt es ins
  Review; der Tag ist vorab abgewählt und wird beim Übernehmen entfernt. Ein deaktivierter Obertag bleibt nur, wenn
  das Dokument einen Untertag hat, der selbst bleibt.
- **Dokumente mit Tag prüfen** (Übersicht) reiht alle Dokumente mit einem Tag ein, auch abgelegte. Probelauf
  (Quelle `tag-test`) zählt nicht fürs Polling; ohne *Titel neu erzeugen* bleibt der Titel.
- **Lösch-Assistent**: Hängen Dokumente an einem Eintrag, fragt eine eigene Seite, ob sie auf einen anderen
  übertragen werden (Tag zusätzlich setzen, sonst ersetzen – per `bulk_edit`). Untertags eines gelöschten Tags
  rücken eine Ebene hoch.

## Lokale Modelle (Ollama, clef)

`jev.py` spricht neben TypeSafe auch Ollamas `/v1/systemone` an – gleiches Format (`state`, `questions` mit
`choice`/`noul`), aber mit Grenzen, die der Client ausgleicht:

- höchstens **64 Fragen** pro Anfrage → Fragen werden aufgeteilt, Antworten zusammengeführt;
- **2–26 Kandidaten** pro Auswahlfrage → Turnier: Optionen in Gruppen à 25 (+ „none of these“) bewerten, die
  25 wahrscheinlichsten aller Gruppen kommen ins Finale, dessen Antwort zählt;
- `keep_alive: 30m`, damit das Modell (clef ~18 GB VRAM) geladen bleibt.

Lokale Modelle bekommen **keine Beispiel-Titel und keine ähnlichen Dokumente**: Mit diesen strukturierten Kriterien
bestätigte clef 0 von 25 vorhandenen, korrekten Tags, ohne sie 17 von 25; die Confidence beim Dokumenttyp stieg von
0.33 auf 0.66. Sie haben **eigene Schwellwerte** (`local_thresholds`), weil clef tiefere Confidence-Werte meldet:
Vorgabe Typ 0.6, Korrespondent 0.7, Datum 0.8 (ab da lag clef in der Messung nie falsch), Tags 0.95.
Unter *Regeln* werden jeweils die Schwellen des aktiven Klassifizierers bearbeitet.

Die Fragen bleiben auch für clef englisch: Mit deutschen Fragen stieg die Confidence an den 43 echten Dokumenten
überall – auch bei falschen Antworten (Korrespondent 36 statt 38 richtig, 2 falsch automatisch gesetzte Werte
statt 0, 46 statt 34 falsche Tag-Vorschläge). clefs Confidence hängt also an der Formulierung; die Schwellen nicht
zu knapp setzen.

## Protokoll

Standardmässig zeigt das Protokoll nur offene Einträge (Review, Probeläufe, Fehler, wartend); Filter nach Status,
Dokumenttyp, Korrespondent und Tag, ein Klick auf einen Wert in der Tabelle filtert direkt. Zeiten werden in der
Zeitzone `TZ` angezeigt (Standard Europe/Zurich), gespeichert wird UTC.

Jeder Lauf ist ein Job in SQLite. Die Jobs sind zugleich das Gedächtnis fürs Polling: ein Dokument, das
schon bewertet wurde, wird nicht erneut klassifiziert. *Protokoll bereinigen* löscht darum nur Fehler,
Einzeltests, verworfene und übersprungene Einträge; Probeläufe nur mit Warnung. Laufende, wartende und
erledigte Einträge bleiben immer stehen.

## Messungen

Blindtests an je 60 bereits abgelegten Dokumenten (Jev sieht die heutige Ablage nicht; verglichen wird
mit ihr). Zwei identische Läufe unterscheiden sich um ±2 Treffer – kleinere Unterschiede sind Zufall.

| Frage | Ergebnis |
|---|---|
| Beschreibungen englisch oder deutsch? | Dokumenttyp EN 44–46/60, DE 49/60, ohne 46/60. Der Vorsprung von DE kam fast ganz von 4 Dividenden-Belegen, die eine englische Abgrenzung falsch lenkte. Beschreibungen senken vor allem falsche Tags (Steuerrelevant: 8 statt 14 Fehlalarme). |
| Paperless-Zuweisungsregeln als Hinweis? | Korrespondent 52/59 mit und ohne, Tags gleich (innerhalb der Schwankung), aber +40 % Tokens. Deshalb standardmässig aus. |
| clef statt Jev (32 erfundene Dokumente, `bench/`)? | Typ 30 vs. 29, Korrespondent 31 vs. 31, Datum 31 vs. 30 von 32; Tags ab 50 %: Präzision 94 vs. 88 %, Trefferquote 84 vs. 92 %. 5.2 s vs. 0.2 s pro Dokument. |
| clef statt Jev (43 echte, im Review geprüfte Dokumente)? | Typ 38 vs. 43, Korrespondent 38 vs. 43, Datum 32 vs. 33; neue Tags 0 richtig / 34 falsch vs. 18 / 41. Jevs Werte waren Ausgangspunkt des Reviews – leichter Vorteil für Jev. |
| Gegenprüfung vorhandener Tags? | 70 gesetzte Tags an 60 Dokumenten: 64 bestätigt, 6 unsicher, 0 als sicher falsch – keine Fehlalarme, rund +20 % Tokens. Standardmässig an. |

## Dokument-Viewer

Die Vorschau öffnet das PDF auf dem Desktop in einem Overlay (iframe), auf dem Handy in einem neuen Tab.
Die App sendet `X-Frame-Options: SAMEORIGIN` und `frame-ancestors 'self'`. Ein Reverse Proxy darf nicht
zusätzlich `X-Frame-Options: DENY` setzen (Traefik: `frameDeny: true`), sonst meldet der Browser
„Verbindung abgelehnt“. Der Viewer hängt `?view=…` an, damit der Browser kein zwischengespeichertes PDF
mit alten Headern verwendet.

## Scannen

Unter *Scannen* nimmt das Handy Belege direkt im Browser auf (Kamera nur über HTTPS). OpenCV.js erkennt den
Belegrand live, entzerrt die Seite perspektivisch und bietet die Filter Original, Graustufen und «Scan»
(Beleuchtung ausgeglichen, kontrastreich). Mehrere Seiten werden im Browser mit jsPDF zu einem PDF und über
paperless-jev an `POST /api/documents/post_document/` geschickt; das API-Token bleibt auf dem Server. Die Seite
verfolgt den Paperless-Task und reiht das neue Dokument sofort zur Klassifizierung ein, ohne auf Webhook oder
Polling zu warten. Fertige PDFs (z. B. iOS «Dokumente scannen») lassen sich auf derselben Seite hochladen.
Ein `Permissions-Policy`-Header des Reverse Proxys darf die Kamera nicht sperren (`camera=(self)`).

Bibliotheken werden zur Laufzeit vom CDN geladen, nicht mitgeliefert: OpenCV.js (`@techstark/opencv-js`,
Apache 2.0, ~10 MB, erst beim Start der Kamera), jsPDF (MIT), pdf.js (Apache 2.0, im Review).

## Webhook

Paperless (≥ 2.14) → *Workflows* → neuer Workflow:

- Auslöser *Dokument hinzugefügt*
- Aktion *Webhook*: URL `http://paperless-jev:8000/hook/<instanzname>?token=<secret>` (Secret steht auf der Setup-Seite),
  *Webhook-Parameter verwenden* aktiv, Parameter `doc_id` = `{{ doc_id }}`

Der Webhook reiht das Dokument sofort ein. Polling (Standard alle 5 Minuten) bleibt als Fallback aktiv
und verarbeitet nur Dokumente im Posteingang, die noch nie bewertet wurden (Fehler: bis zu 3 Versuche).

## Datenschutz

Der OCR-Text (gekürzt) und die Namen der Stammdaten gehen an `api.typesafe.ai`. TypeSafe trainiert laut
Doku nicht auf Kundendaten; Zero Data Retention gibt es nur im Enterprise-Plan. Sensible Dokumente mit
dem Tag *ai-ignorieren* versehen (z. B. per Paperless-Workflow nach Speicherpfad oder Korrespondent) – oder ein
lokales Modell über Ollama verwenden, dann verlässt nichts das eigene Netz.

## Kosten

jev-1.13: 0.042 USD pro Mio. Input-Tokens, Output kostenlos. Gemessen: im Schnitt rund 10k Tokens pro
Dokument (Text bis 12'000 Zeichen, ~50 Tag-Fragen, Beispieltitel), also rund 0.0004 USD. Die Übersicht zeigt die Summe
(nur TypeSafe-Tokens; lokale Modelle kosten nichts).
