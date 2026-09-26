# Demo

Startet paperless-jev mit erfundenen Daten und einem nachgebauten Paperless – ohne echtes Paperless und ohne TypeSafe-Key. Gedacht für Screenshots und zum Ausprobieren der Oberfläche; Klassifizieren funktioniert damit nicht.

```bash
pip install -e .
PJ_DATA_DIR=./demo-data python -m demo.seed
uvicorn demo.mock_paperless:app --port 8001 &
PJ_DATA_DIR=./demo-data paperless-jev
# → http://localhost:8000
```
