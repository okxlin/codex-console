FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN python -m venv "$VIRTUAL_ENV"

RUN mkdir -p /ms-playwright

COPY requirements.txt .

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
    && pip install --upgrade pip \
    && pip install -r requirements.txt \
    && apt-get purge -y --auto-remove gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

FROM python:3.11-slim

WORKDIR /app

ARG TARGETARCH
ARG TARGETPLATFORM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    WEBUI_HOST=0.0.0.0 \
    WEBUI_PORT=1455 \
    DISPLAY=:99 \
    ENABLE_VNC=1 \
    VNC_PORT=5900 \
    NOVNC_PORT=6080 \
    LOG_LEVEL=info \
    DEBUG=0

COPY --from=builder /opt/venv /opt/venv

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        fluxbox \
        novnc \
        websockify \
        x11vnc \
        xvfb \
    && if [ "$TARGETARCH" = "amd64" ] || [ "$TARGETARCH" = "arm64" ]; then python -m playwright install --with-deps chromium; else mkdir -p /ms-playwright && printf 'Playwright Chromium is not bundled for %s.\n' "$TARGETPLATFORM" > /ms-playwright/ARCHITECTURE-NOTICE.txt; fi \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN chmod +x /app/scripts/docker/start-webui.sh

EXPOSE 1455 6080 5900

CMD ["/app/scripts/docker/start-webui.sh"]
