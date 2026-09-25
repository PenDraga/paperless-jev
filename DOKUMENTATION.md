# Dokumentation paperless-jev

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
| `titles.py` | optional: Titel per lokalem Ollama (einziger generativer Schritt) |
| `paperless.py` / `jev.py` | schlanke HTTP-Clients (Paperless API v10, TypeSafe System One) |
| `config.py` / `db.py` / `vault.py` | Einstellungen, Instanzen, Beschreibungen, Jobs in SQLite; Tokens mit Fernet verschlüsselt |

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
   - je Tag ein `noul` („trifft Tag X zu?“)
5. Entscheidung nach Modus:
   - **Probelauf**: nur protokollieren.
   - **Nur Vorschläge**: Tag *ai-review* setzen, Review-Queue.
   - **Automatisch**: sichere Werte setzen. Ist noch ein Feld leer, ohne sicheren Wert → *ai-review*,
     sonst *ai-klassifiziert* + Posteingang entfernen. Unsichere Tag-Vorschläge allein lösen kein Review
     aus (umschaltbar) – sie erscheinen aber im Review, wenn das Dokument ohnehin geprüft wird.
6. In der Review-Queue bestätigte/korrigierte Werte werden geschrieben; Korrekturen werden gezählt
   (Übersicht) und dienen zum Nachjustieren von Beschreibungen und Schwellen.

## Designentscheidungen

- **Jev nur für Entscheidungen.** Laut [Jev-1.13-Jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13)
  sind Rechnen, Datumsvergleiche und Zählen schwach – Datumskandidaten findet daher Code, Jev wählt nur.
- **Englische Fragen, deutscher Text.** Jev ist primär auf Englisch trainiert. Fragen und
  Beschreibungen daher englisch formulieren; die Schwellen an echten Daten kalibrieren.
- **Vorhandene Werte bleiben** (Einstellung „überschreiben“ aus), damit Paperless-eigenes Matching
  und manuelle Zuordnungen Vorrang haben. Ausnahme Datum: Paperless setzt immer eines, Jev korrigiert nur bei hoher Confidence.
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
- **per Ollama** (empfohlen, ersetzt paperless-gpt): nach der Klassifizierung erzeugt ein lokales Modell
  (z. B. `qwen3:8b`) einen Titel. Als Stilvorlage dienen bis zu 8 Titel bereits abgelegter Dokumente
  desselben Korrespondenten bzw. Typs. Jahreszahlen, Daten und Personennamen werden vermieden, weil
  Paperless sie separat speichert. Dauer ca. 1-2 s pro Dokument.
- **per Vorlage**, z. B. `{document_type} {correspondent} {created:%Y-%m}`,
- oder gar nicht.

Ein im Review eingetragener Titel hat Vorrang. Paperless-Workflows mit Auslöser *Dokument aktualisiert*,
die einen Titel setzen (z. B. „Kontoauszug {{ created_month_name }} {{ created_year }}“), laufen nach
paperless-jev und haben damit das letzte Wort.

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
dem Tag *ai-ignorieren* versehen (z. B. per Paperless-Workflow nach Speicherpfad oder Korrespondent).

## Kosten

jev-1.13: 0.042 USD pro Mio. Input-Tokens, Output kostenlos. Ein Dokument mit 12'000 Zeichen und
~50 Fragen liegt bei grob 4–6k Tokens, also rund 0.0002 USD. Die Übersicht zeigt die Summe.
