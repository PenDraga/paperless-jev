FROM python:3.12-slim

# Vorgaben fuer den Containerbetrieb; alles Fachliche wird in der Web-UI
# konfiguriert und unter /data (SQLite) gespeichert.
ENV PJ_DATA_DIR=/data \
    PJ_HOST=0.0.0.0 \
    PJ_PORT=8000 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 jev \
    && mkdir -p /data && chown jev /data
USER jev
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

CMD ["paperless-jev"]
