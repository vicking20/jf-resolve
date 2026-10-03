FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .
RUN mkdir -p /app/data /app/experimental/data
RUN python scripts/install_torrserver.py \
    && mkdir -p /opt/jf-resolve/torrserver \
    && cp -a /app/data/torrserver/bin/. /opt/jf-resolve/torrserver/

EXPOSE 8765
VOLUME ["/app/data", "/app/experimental/data", "/app/experimental/plugins"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8765/ >/dev/null || exit 1
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "start.py", "--host", "0.0.0.0", "--port", "8765"]
