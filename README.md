# paperless-jev

Klassifiziert neue Dokumente im Paperless-NGX-Posteingang mit [TypeSafe Jev](https://docs.typesafe.ai/introduction):
Dokumenttyp, Korrespondent, Speicherpfad, Ausstellungsdatum und Tags. Das Tool läuft als Docker-Container
und wird komplett über eine Web-UI konfiguriert (Deutsch/Englisch, für Handy optimiert; Dokumente lassen
sich direkt im Review ansehen).

Jev erzeugt keinen Text, es **wählt aus**: aus deinen vorhandenen Paperless-Stammdaten bzw. aus
Datumsangaben, die der Code vorher im OCR-Text gefunden hat. Jede Antwort bringt eine kalibrierte
*Confidence* mit. Damit entscheidet paperless-jev:

| Confidence | Aktion |
|---|---|
| ≥ Auto-Schwelle | Wert wird direkt in Paperless gesetzt |
| ≥ Review-Schwelle | Vorschlag in der Review-Queue (Tag `ai-review`) |
| darunter | ignoriert – Dokument bleibt im Posteingang |

## Schnellstart

```bash
cp .env.example .env
docker compose up -d --build
# → http://localhost:8090
```

1. **Setup**: TypeSafe-API-Key eintragen, Paperless-Instanz(en) mit URL und API-Token anlegen, „Verbindung testen“.
2. **Regeln**: im Modus *Probelauf* starten, Felder und Schwellwerte wählen.
3. **Beschreibungen**: für Dokumenttypen/Tags kurze (englische) Beschreibungen hinterlegen.
4. **Übersicht → Posteingang jetzt verarbeiten** und im Protokoll prüfen, wie gut die Vorschläge sind.
5. Danach Modus *Automatisch* aktivieren und in Paperless den Webhook-Workflow anlegen (Anleitung auf der Setup-Seite).

Betrieb neben Paperless hinter Traefik, nur aus dem internen Netz erreichbar (IP-Allowlist `admin-only@file`): [`deploy/compose.yaml`](deploy/compose.yaml).

## Probelauf auf der Kommandozeile (Phase 0)

Schreibt nichts nach Paperless, zeigt pro Dokument Vorschlag, Confidence und aktuellen Wert:

```bash
docker run --rm -it --network paperless_paperless-internal \
  -e PAPERLESS_URL=http://paperless:8000 -e PAPERLESS_HOST=paperless.example.com \
  -e PAPERLESS_TOKEN=... -e TYPESAFE_API_KEY=... \
  paperless-jev:latest paperless-jev-spike --limit 20
```

Mit `--doc 123 --doc 456` lassen sich gezielt bereits abgelegte Dokumente prüfen; am Ende steht die
Übereinstimmung mit dem Ist-Zustand pro Feld. Beschreibungen lassen sich vorab testen – Vorlage:
[`beschreibungen.beispiel.json`](beschreibungen.beispiel.json), eingebunden per
`-v $PWD/beschreibungen.beispiel.json:/b.json ... --descriptions /b.json`.

## Entwicklung

```bash
pip install -e '.[dev]'
pytest
PJ_DATA_DIR=./data paperless-jev
```

Details zu Architektur und Entscheidungen: [DOKUMENTATION.md](DOKUMENTATION.md).
