FROM python:3.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    TZ=Europe/Bratislava \
    PORT=5050

WORKDIR /app

COPY docker/requirements.txt docker/requirements.txt
RUN pip install -r docker/requirements.txt

COPY calculation_config_slovakia.py ./
COPY analysismodes/ analysismodes/
COPY markets/ markets/
COPY tools/ tools/
COPY docker/wsgi.py docker/wsgi.py
# Initial data; in docker-compose these paths are named volumes seeded from the image
# on first start, the daily updater keeps them current afterwards.
COPY marketdata/ marketdata/
COPY results/ results/

RUN useradd --create-home appuser
USER appuser

EXPOSE 5050

# Hosting platforms (Render, Railway, Fly, ...) inject $PORT; locally it defaults to 5050.
CMD gunicorn --chdir /app/docker --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 wsgi:app
