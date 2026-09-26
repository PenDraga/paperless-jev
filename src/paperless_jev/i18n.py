"""Mehrsprachige Oberfläche (Deutsch/Englisch) ohne Zusatzbibliothek.

Die deutschen Texte im Code sind zugleich die Schlüssel; ``EN`` enthält die
Übersetzungen. Fehlt eine, erscheint der deutsche Text. Die Sprache gilt pro
Request (Cookie ``lang``, sonst Accept-Language des Browsers).
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

LANGUAGES = {"de": "Deutsch", "en": "English"}
DEFAULT = "de"
COOKIE = "lang"

current: ContextVar[str] = ContextVar("lang", default=DEFAULT)


def pick(cookie: str | None, accept_language: str | None) -> str:
    if cookie in LANGUAGES:
        return cookie
    for part in (accept_language or "").split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGUAGES:
            return code
    return DEFAULT


def gettext(text: str, **kwargs: Any) -> str:
    if current.get() == "en":
        text = EN.get(text, text)
    return text.format(**kwargs) if kwargs else text


EN: dict[str, str] = {
    # --- Navigation, Rahmen -------------------------------------------------
    "Übersicht": "Overview",
    "Review": "Review",
    "Protokoll": "Log",
    "Regeln": "Rules",
    "Beschreibungen": "Descriptions",
    "Setup": "Setup",
    "Klassifizierung mit TypeSafe Jev": "Classification with TypeSafe Jev",
    "Sprache": "Language",
    "Navigation": "Navigation",
    "Mehr": "More",
    # --- Status, Felder, Arten ------------------------------------------------
    "wartet": "queued",
    "läuft": "running",
    "Probelauf": "Dry run",
    "erledigt": "done",
    "verworfen": "dismissed",
    "übersprungen": "skipped",
    "Fehler": "Error",
    "Dokumenttyp": "Document type",
    "Korrespondent": "Correspondent",
    "Speicherpfad": "Storage path",
    "Ausstellungsdatum": "Issue date",
    "Tags": "Tags",
    "Dokumenttypen": "Document types",
    "Korrespondenten": "Correspondents",
    "Speicherpfade": "Storage paths",
    "Ollama": "Ollama",
    "OpenAI-kompatibel (SGLang, vLLM, LM Studio …)": "OpenAI-compatible (SGLang, vLLM, LM Studio …)",
    "Dokument ansehen": "View document",
    "Einzelnes Dokument testen": "Test a single document",
    "ID oder Paperless-Link – schreibt nichts nach Paperless": "ID or Paperless link – writes nothing to Paperless",
    "z. B. 2474 oder https://paperless…/documents/2474/details": "e.g. 2474 or https://paperless…/documents/2474/details",
    "Testen": "Test",
    "Bitte eine Dokument-ID oder einen Paperless-Link angeben": "Please enter a document ID or a Paperless link",
    "Test abgeschlossen – nichts wurde in Paperless geändert": "Test finished – nothing was changed in Paperless",
    "Test – dieses Ergebnis wurde nicht in Paperless geschrieben. Passt es, kannst du es unten von Hand übernehmen.":
        "Test – this result was not written to Paperless. If it fits, you can apply it manually below.",
    "Erneut testen": "Test again",
    "Sofort neu bewerten, ohne etwas zu schreiben": "Re-evaluate immediately without writing anything",
    "Einreihen und im aktuellen Modus verarbeiten": "Queue and process in the current mode",
    "Schliessen": "Close",
    "In neuem Tab öffnen": "Open in new tab",
    # --- Job-Fehler, die im Protokoll stehen --------------------------------------
    "Tag zum Ignorieren gesetzt": "Ignore tag is set",
    "Kein TypeSafe-API-Key konfiguriert": "No TypeSafe API key configured",
    "Keine Fragen - alle Felder deaktiviert?": "No questions - are all fields disabled?",
    "Instanz existiert nicht mehr": "Instance no longer exists",
    # --- Übersicht ------------------------------------------------------------
    "Noch nicht eingerichtet – bitte unter <a href=\"/setup\">Setup</a> TypeSafe-Key und mindestens eine Paperless-Instanz hinterlegen.":
        "Not set up yet – please add a TypeSafe key and at least one Paperless instance under <a href=\"/setup\">Setup</a>.",
    "Modus": "Mode",
    "Probelauf – schreibt nichts": "Dry run – writes nothing",
    "Nur Vorschläge (Review)": "Suggestions only (review)",
    "Automatisch + Review": "Automatic + review",
    "Modell": "Model",
    "Polling alle {n} min": "Polling every {n} min",
    "Polling aus": "Polling off",
    "Warteschlange": "Queue",
    "in Review": "in review",
    "Probeläufe": "dry runs",
    "ohne Review erledigt": "done without review",
    "Tokens (≈ ${cost})": "tokens (≈ ${cost})",
    "Korrekturen im Review ({n} geprüft)": "Corrections in review ({n} checked)",
    "{field}: {n}× korrigiert": "{field}: corrected {n}×",
    "Bisher keine Korrekturen.": "No corrections so far.",
    "Auswertung": "Evaluation",
    "({n} Dokumente, jeweils letzter Lauf)": "({n} documents, latest run each)",
    "Feld": "Field",
    "sicher": "confident",
    "davon = aktueller Wert": "of which = current value",
    "würde leeres Feld füllen": "would fill empty field",
    "unsicher (Review)": "uncertain (review)",
    "keine Antwort": "no answer",
    "<strong>Neue Tags</strong> für {n} Dokumente:": "<strong>New tags</strong> for {n} documents:",
    "keine": "none",
    "grün = sicher, gelb = unsicher. „davon = aktueller Wert“ vergleicht mit dem, was heute in Paperless steht – bei bereits sauber abgelegten Dokumenten ein guter Genauigkeitstest.":
        "green = confident, yellow = uncertain. “of which = current value” compares with what Paperless holds today – a good accuracy test for documents that are already filed correctly.",
    "Posteingang jetzt verarbeiten": "Process inbox now",
    "Alle aktiven Instanzen": "All active instances",
    "auch bereits bewertete Dokumente neu klassifizieren": "also re-classify documents that were already evaluated",
    "Einreihen": "Queue",
    "Zuletzt verarbeitet": "Recently processed",
    "ganzes Protokoll": "full log",
    "Instanz": "Instance",
    "Anteil der erledigten Dokumente, die nicht ins Review mussten": "share of finished documents that did not need a review",
    "= aktueller Wert": "= current value",
    "sicher, = aktueller Wert": "confident, = current value",
    "widerspricht aktuellem Wert": "contradicts current value",
    # --- Protokoll-Tabelle --------------------------------------------------------
    "ist: {value}": "is: {value}",
    "entspricht dem aktuellen Wert": "matches the current value",
    "Dokument": "Document",
    "Status": "Status",
    "Datum": "Date",
    "Tags (neu)": "Tags (new)",
    "Details →": "Details →",
    "Noch keine Einträge.": "No entries yet.",
    "keiner passt": "none fits",
    "Balken = Confidence: <span class=\"badge auto\">sicher</span> würde automatisch gesetzt · <span class=\"badge suggest\">unsicher</span> käme ins Review · grau = zu unsicher, wird ignoriert · ✓ = entspricht dem aktuellen Wert. Tags: Vorschläge mit Balken, schon in Paperless gesetzte Tags umrandet mit ✓. Genaue Werte beim Darüberfahren.":
        "Bar = confidence: <span class=\"badge auto\">confident</span> would be set automatically · <span class=\"badge suggest\">uncertain</span> would go to review · grey = too uncertain, ignored · ✓ = matches the current value. Tags: suggestions with a bar, tags already set in Paperless outlined with ✓. Hover for exact values.",
    "Posteingang einreihen oder ein einzelnes Dokument testen.": "Queue the inbox or test a single document.",
    "Titel oder Dokument-ID suchen": "Search title or document ID",
    "alle Status": "all statuses",
    "{n} Einträge": "{n} entries",
    "Filtern": "Filter",
    "schon in Paperless gesetzt": "already set in Paperless",
    "offen": "open",
    "erledigt (inkl. verworfen)": "finished (incl. dismissed)",
    "einzeln": "individual",
    "Offen": "Open",
    "Review, Probeläufe und Fehler": "review, dry runs and errors",
    "Anzahl": "Number",
    "alle Dokumenttypen": "all document types",
    "alle Korrespondenten": "all correspondents",
    "alle Tags": "all tags",
    "Filter zurücksetzen": "Reset filters",
    "Dokumenttyp, Korrespondent und Tags filtern nach dem Vorschlag von Jev.": "Document type, correspondent and tags filter by Jev's suggestion.",
    "Titel vorschlagen": "Suggest title",
    "zeigt den Titel, der beim Übernehmen entstehen würde – schreibt nichts": "shows the title that applying would create – writes nothing",
    "ins Titelfeld": "use as title",
    "Titel sind unter Regeln ausgeschaltet": "Titles are switched off under Rules",
    "Kein Titel erzeugt – Sprachmodell unter Regeln prüfen": "No title created – check the language model under Rules",
    "Zuweisungsregeln aus Paperless (Suchbegriffe wie Policen- oder Kontonummern) als Hinweis an Jev mitgeben":
        "pass Paperless matching rules (search terms like policy or account numbers) to Jev as a hint",
    "Zuweisungsregel aus Paperless – geht als Hinweis an Jev": "Matching rule from Paperless – passed to Jev as a hint",
    "beliebiges Wort": "any word",
    "alle Wörter": "all words",
    "exakt": "exact",
    "Regex": "regex",
    "ungefähr": "fuzzy",
    "Protokoll bereinigen": "Clean up log",
    "Ausgewählte Einträge endgültig löschen?": "Delete the selected entries permanently?",
    "Einzeltests": "Single tests",
    "Achtung: zählen für die Auswertung. Dokumente, die noch im Posteingang liegen, werden beim nächsten Polling erneut klassifiziert (kostet Tokens).":
        "Caution: they count towards the evaluation. Documents still in the inbox are classified again at the next poll (costs tokens).",
    "Löschen": "Delete",
    "Laufende und wartende Einträge bleiben immer stehen. Erledigte Einträge lassen sich hier nicht löschen – sie zeigen, was nach Paperless geschrieben wurde.":
        "Running and queued entries are always kept. Finished entries cannot be deleted here – they show what was written to Paperless.",
    "Nichts ausgewählt": "Nothing selected",
    "{n} Einträge gelöscht": "{n} entries deleted",
    "Diesen Eintrag endgültig löschen?": "Delete this entry permanently?",
    "Eintrag löschen": "Delete entry",
    "Eintrag #{id} gelöscht": "Entry #{id} deleted",
    "Laufende oder wartende Einträge lassen sich nicht löschen": "Running or queued entries cannot be deleted",
    # --- Review, Job ------------------------------------------------------------
    "Review-Queue": "Review queue",
    "in Paperless öffnen": "open in Paperless",
    "Details": "Details",
    "Vorschau": "Preview",
    "Nichts zu prüfen.": "Nothing to review.",
    "Neue unsichere Fälle erscheinen hier automatisch.": "New uncertain cases show up here automatically.",
    "Quelle": "Source",
    "{n} Fragen": "{n} questions",
    "Neu klassifizieren": "Re-classify",
    "Vorschlag bearbeiten und übernehmen": "Edit and apply suggestion",
    "In Paperless geschrieben": "Written to Paperless",
    "Rohdaten der Auswertung": "Raw evaluation data",
    "Stammdaten nicht verfügbar": "Master data not available",
    "Feld übernehmen": "Apply field",
    "– leer –": "– empty –",
    "nicht bewertet": "not evaluated",
    "bisher: {value}": "previously: {value}",
    "Alternativen": "Alternatives",
    "aktuell {value}": "currently {value}",
    "Titel": "Title",
    "leer = automatisch (Sprachmodell)": "empty = automatic (language model)",
    "leer = automatisch (Vorlage)": "empty = automatic (template)",
    "leer = unverändert": "empty = unchanged",
    "aktuell: {value}": "currently: {value}",
    "(zusätzlich zu den vorhandenen)": "(in addition to the existing ones)",
    "Weitere Tags": "More tags",
    "Übernehmen & Posteingang verlassen": "Apply & leave inbox",
    "Verwerfen": "Dismiss",
    # --- Setup -------------------------------------------------------------------
    "API-Key": "API key",
    "(gespeichert – leer lassen, um ihn zu behalten)": "(saved – leave empty to keep it)",
    "gespeichert": "saved",
    "API-Key von console.typesafe.ai/keys": "API key from console.typesafe.ai/keys",
    "Für stabile Schwellwerte auf eine Version pinnen, z. B. <code>jev-1.13.0</code>. <code>jev-latest</code> wechselt bei neuen Releases automatisch.":
        "Pin a version for stable thresholds, e.g. <code>jev-1.13.0</code>. <code>jev-latest</code> moves automatically with new releases.",
    "Speichern": "Save",
    "Verbindung testen": "Test connection",
    "Paperless-Instanzen": "Paperless instances",
    "deaktiviert": "disabled",
    "Name": "Name",
    "API-URL (aus dem Container erreichbar)": "API URL (reachable from the container)",
    "Öffentliche URL": "Public URL",
    "Für Links – und als Host-Header, damit Paperless (ALLOWED_HOSTS) den Zugriff über den Containernamen akzeptiert.":
        "For links – and as Host header so Paperless (ALLOWED_HOSTS) accepts access via the container name.",
    "API-Token": "API token",
    "gespeichert – leer lassen, um ihn zu behalten": "saved – leave empty to keep it",
    "aktiv": "active",
    "<strong>Webhook für sofortige Verarbeitung</strong> – in Paperless unter <em>Workflows</em> anlegen:":
        "<strong>Webhook for immediate processing</strong> – create it in Paperless under <em>Workflows</em>:",
    "Auslöser: <em>Dokument hinzugefügt</em>": "Trigger: <em>Document Added</em>",
    "Aktion: <em>Webhook</em>, URL": "Action: <em>Webhook</em>, URL",
    "Läuft paperless-jev im selben Docker-Netz, besser die interne Adresse verwenden, z. B.":
        "If paperless-jev runs in the same Docker network, better use the internal address, e.g.",
    "„Webhook-Parameter verwenden“ aktivieren, Parameter": "Enable “Use webhook params”, parameter",
    "Instanz {name} wirklich löschen?": "Really delete instance {name}?",
    "Instanz löschen": "Delete instance",
    "Neue Instanz hinzufügen": "Add new instance",
    "z. B. home": "e.g. home",
    "In Paperless: Profil → „API-Auth-Token“": "In Paperless: profile → “API Auth Token”",
    "Hinzufügen": "Add",
    # --- Regeln --------------------------------------------------------------------
    "Betriebsmodus": "Operating mode",
    "<strong>Probelauf</strong> – klassifizieren und protokollieren, nichts in Paperless ändern":
        "<strong>Dry run</strong> – classify and log, change nothing in Paperless",
    "<strong>Nur Vorschläge</strong> – jedes Dokument landet in der Review-Queue":
        "<strong>Suggestions only</strong> – every document goes to the review queue",
    "<strong>Automatisch</strong> – sichere Werte direkt setzen, unsichere Fälle in die Review-Queue":
        "<strong>Automatic</strong> – set confident values directly, uncertain cases go to the review queue",
    "Felder und Schwellwerte": "Fields and thresholds",
    "Jev liefert pro Antwort eine kalibrierte <em>Confidence</em> (bei Tags die Wahrscheinlichkeit für „ja“). Ab <em>Auto</em> wird der Wert gesetzt, ab <em>Review</em> vorgeschlagen, darunter ignoriert.":
        "Jev returns a calibrated <em>confidence</em> per answer (for tags the probability of “yes”). From <em>Auto</em> the value is set, from <em>Review</em> it is suggested, below that it is ignored.",
    "Auto ≥": "Auto ≥",
    "Review ≥": "Review ≥",
    "bereits gesetzte Werte überschreiben (z. B. aus dem Paperless-eigenen Matching)":
        "overwrite values that are already set (e.g. from Paperless' own matching)",
    "Sammel-Korrespondent, wenn keiner passt": "Catch-all correspondent if none fits",
    "z. B. Diverses – leer = Feld bleibt leer": "e.g. Diverses – empty = field stays empty",
    "unsichere Tag-Vorschläge allein schicken ein Dokument ins Review":
        "uncertain tag suggestions alone send a document to review",
    "ins Review, wenn Jev einem vorhandenen Wert sicher widerspricht (z. B. falsche Paperless-Zuordnung)":
        "send to review if Jev confidently contradicts an existing value (e.g. wrong Paperless matching)",
    "Posteingangs-Tag entfernen, wenn ein Dokument fertig klassifiziert ist":
        "remove the inbox tag once a document is fully classified",
    "Tags für den Status": "Status tags",
    "(werden bei Bedarf angelegt)": "(created when needed)",
    "Erledigt": "Done",
    "Review nötig": "Needs review",
    "Ignorieren": "Ignore",
    "Dokumente mit diesem Tag gehen nie an Jev.": "Documents with this tag are never sent to Jev.",
    "Titel nicht ändern": "Do not change titles",
    "<strong>Titel vom Sprachmodell formulieren</strong> – Stil nach Titeln bereits abgelegter Dokumente desselben Korrespondenten":
        "<strong>Let the language model write titles</strong> – style based on titles of documents already filed for the same correspondent",
    "Titel aus Vorlage": "Title from template",
    "Vorlage": "Template",
    "Platzhalter": "Placeholders",
    "Fehlt ein Wert, bleibt der Titel unverändert.": "If a value is missing, the title stays unchanged.",
    "Sprachmodell": "Language model",
    "für Titel und für Beschreibungen aus Stichworten": "for titles and for descriptions from keywords",
    "Schnittstelle": "Interface",
    "URL": "URL",
    "(nur OpenAI-kompatibel und nur falls nötig)": "(OpenAI-compatible only, and only if required)",
    "(nur OpenAI-kompatibel und nur falls nötig – gespeichert, leer lassen zum Behalten)":
        "(OpenAI-compatible only, and only if required – saved, leave empty to keep)",
    "Erst speichern, dann testen. Paperless-Workflows, die bei „Dokument aktualisiert“ einen Titel setzen, haben danach das letzte Wort.":
        "Save first, then test. Paperless workflows that set a title on “Document Updated” have the last word afterwards.",
    "Verarbeitung": "Processing",
    "Polling-Intervall (Minuten, 0 = aus)": "Polling interval (minutes, 0 = off)",
    "Max. Zeichen Dokumenttext": "Max. characters of document text",
    "Lange Dokumente: Anfang (70 %) + Ende (30 %).": "Long documents: beginning (70 %) + end (30 %).",
    "Ähnliche Dokumente als Kontext": "Similar documents as context",
    "0 = aus": "0 = off",
    "Beispieltitel je Typ/Tag": "Example titles per type/tag",
    "Titel bereits abgelegter Dokumente – so lernt Jev deine Ablage-Konventionen. 0 = aus":
        "Titles of documents already filed – this is how Jev learns your filing conventions. 0 = off",
    # --- Beschreibungen ---------------------------------------------------------------
    "Jev wählt aus deinen Paperless-Stammdaten. Eine kurze Beschreibung pro Eintrag verbessert die Treffsicherheit deutlich. Schreib wie eine Regel: was dazugehört und was ausdrücklich nicht, z. B. Dokumenttyp „Kontoauszug“: <em>Bankbelege zu Kontobewegungen: Kontoauszüge, Gutschrifts- und Belastungsanzeigen. Keine Jahres- oder Steuerbescheinigungen.</em>":
        "Jev picks from your Paperless master data. A short description per entry noticeably improves accuracy. Write it like a rule: what belongs and what explicitly does not, e.g. document type “Kontoauszug”: <em>Bank documents about account movements: statements, credit and debit advices. No annual or tax certificates.</em>",
    "Es genügen <strong>Stichworte</strong>, z. B. <em>Bankbelege, Gutschrift, Belastung – nicht: Steuerbescheinigung</em>. Beim Speichern formuliert das Sprachmodell geänderte Einträge zu einer ganzen Beschreibung aus – in der Sprache der Oberfläche – und nutzt dafür auch die Titel bereits abgelegter Dokumente. Das Ergebnis steht unter dem Feld – passt es nicht, Stichworte ergänzen und erneut speichern.":
        "<strong>Keywords</strong> are enough, e.g. <em>bank documents, credit, debit – not: tax certificate</em>. On save, the language model turns changed entries into a full description – in the interface language – also using the titles of documents already filed. The result is shown below the field – if it does not fit, add keywords and save again.",
    "Schreib in der Sprache der Oberfläche. Mit einem Sprachmodell unter <a href=\"/rules\">Regeln</a> reichen auch Stichworte – es formuliert sie aus.":
        "Write in the interface language. With a language model under <a href=\"/rules\">Rules</a>, keywords are enough – it writes them out.",
    "Deaktivierte Einträge schlägt Jev nie vor.": "Jev never suggests disabled entries.",
    "Zuerst unter <a href=\"/setup\">Setup</a> eine Instanz anlegen.": "First create an instance under <a href=\"/setup\">Setup</a>.",
    "Beschreibung für Jev": "Description for Jev",
    "Stichworte, optional": "keywords, optional",
    "optional": "optional",
    "An Jev gesendet": "Sent to Jev",
    "Keine Einträge.": "No entries.",
    "geänderte Einträge mit dem Sprachmodell ausformulieren":
        "write out changed entries with the language model",
    # --- Meldungen ---------------------------------------------------------------------
    "{n} Dokument(e) eingereiht": "{n} document(s) queued",
    "Neu eingereiht": "Queued again",
    "Verworfen": "Dismissed",
    "Übernommen": "Applied",
    "TypeSafe-Einstellungen gespeichert": "TypeSafe settings saved",
    "Kein API-Key gespeichert": "No API key saved",
    "OK - {model} antwortet \"{choice}\" (Confidence {confidence})": "OK - {model} answers \"{choice}\" (confidence {confidence})",
    "Für eine neue Instanz wird ein Token benötigt": "A new instance needs a token",
    "Instanz {name} gespeichert": "Instance {name} saved",
    "Instanz gelöscht": "Instance deleted",
    "Unbekannte Instanz": "Unknown instance",
    "OK - {inbox} im Posteingang, {types} Typen, {correspondents} Korrespondenten, {tags} Tags":
        "OK - {inbox} in inbox, {types} types, {correspondents} correspondents, {tags} tags",
    "Regeln gespeichert": "Rules saved",
    "URL und Modell speichern, dann testen": "Save URL and model, then test",
    "Modell {model} fehlt. Vorhanden: {available}": "Model {model} is missing. Available: {available}",
    "OK - {model} verfügbar": "OK - {model} available",
    "Beschreibungen gespeichert": "Descriptions saved",
    ", {n} vom Sprachmodell ausformuliert": ", {n} written out by the language model",
    "Sprachmodell fehlgeschlagen für: {names} – Eingabe wurde unverändert übernommen":
        "Language model failed for: {names} – input was saved unchanged",
}
