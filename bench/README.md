# Benchmark

Unabhängiger Vergleich der Klassifizierer mit 32 **erfundenen** Dokumenten (`dokumente.py`) – ohne eigene
Daten. Jedes Modell bekommt genau die Fragen, die paperless-jev stellt; verglichen wird mit der bekannten
richtigen Antwort.

```bash
pip install -e .
TYPESAFE_API_KEY=… python -m bench.run --jev
python -m bench.run --ollama http://192.168.0.16:11434 --model clef
python -m bench.run --jev --ollama http://192.168.0.16:11434 --json bench.json   # beide nebeneinander
```

Die Fälle sind absichtlich gemischt: ähnliche Typen (Rechnung, Mahnung, Gutschrift, Quittung), mehrere Daten
im Text (Fälligkeit, Lieferung, Periode), ein Kündigungsschreiben an statt von einer Firma, Dokumente ohne
passenden Korrespondenten und mehr als 26 Korrespondenten (für Modelle mit Kandidaten-Limit).
Gemessen werden Trefferquote, Confidence bei richtigen und falschen Antworten, wie viele Werte mit den
Standard-Schwellen automatisch gesetzt würden (und wie viele davon falsch wären), Tags sowie Zeit pro Dokument.

**Grenzen:** Der Benchmark vergleicht Trefferquoten, aber keine Kalibrierung. Pro Feld gibt es nur ein bis zwei
falsche Antworten und bei 10 Tags kaum Raum für Fehlvorschläge – ob die Confidence auch bei Fehlern steigt, lässt
sich damit nicht beantworten. Für Schwellwerte, Auto-Quoten und die Präzision von Tag-Vorschlägen braucht es echte,
geprüfte Dokumente. Bei clef lagen Benchmark und echte Dokumente dreimal auseinander: Beispiel-Titel, Tag-Schwelle
und Sprache der Fragen (deutsche Fragen hoben die Confidence auch falscher Antworten – mehr automatisch gesetzte
Werte, aber auch Fehler). Details in der [Dokumentation](../DOKUMENTATION.md#lokale-modelle-ollama-clef).
