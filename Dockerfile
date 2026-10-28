FROM python:3.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    TZ=Europe/Bratislava \
    PORT=5050

ARG RCLONE_VERSION=v1.68.2
ARG SUPERCRONIC_VERSION=v0.2.33

# tzdata: local delivery days and the 16:00 Europe/Bratislava schedule;
# rclone: OneDrive inbox; supercronic: scheduler inside the worker container.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates curl unzip \
 && curl -fsSL -o /tmp/rclone.zip \
      "https://downloads.rclone.org/${RCLONE_VERSION}/rclone-${RCLONE_VERSION}-linux-amd64.zip" \
 && unzip -q /tmp/rclone.zip -d /tmp \
 && install -m 0755 /tmp/rclone-${RCLONE_VERSION}-linux-amd64/rclone /usr/local/bin/rclone \
 && curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64" \
 && chmod 0755 /usr/local/bin/supercronic \
 && apt-get purge -y curl unzip && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/* /tmp/rclone*

WORKDIR /app

COPY docker/requirements.txt docker/requirements.txt
RUN pip install -r docker/requirements.txt

COPY calculation_config_slovakia.py ./
COPY analysismodes/ analysismodes/
COPY markets/ markets/
COPY tools/ tools/
COPY bri/ bri/
COPY tests/ tests/
COPY docker/wsgi.py docker/about.html docker/crontab docker/
COPY LICENSE.txt ./
# Legacy file data, used once by `python -m bri.migrate` (named volumes in docker-compose).
COPY marketdata/ marketdata/
COPY results/ results/

ARG CODE_VERSION=unknown
ENV BRI_CODE_VERSION=${CODE_VERSION}

RUN useradd --create-home --uid 1000 appuser \
 && mkdir -p /data/raw /config/rclone \
 && chown -R appuser:appuser /data /config
USER appuser

EXPOSE 5050

# Dashboard by default; the worker service overrides the command (see docker-compose.yml).
CMD gunicorn --chdir /app/docker --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 wsgi:app
