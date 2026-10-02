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
