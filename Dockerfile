FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY radcast ./radcast
RUN pip install --no-cache-dir . \
    && mkdir -p /data/recordings /config \
    && chown -R 1000:1000 /data

ENV RECORDINGS_DIR=/data/recordings \
    SHOWS_FILE=/config/shows.yml \
    ARTWORK=/config/artwork.jpg \
    PORT=5000 \
    PYTHONUNBUFFERED=1

USER 1000:1000
EXPOSE 5000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=4).status == 200 else 1)"

CMD ["radcast", "serve"]
