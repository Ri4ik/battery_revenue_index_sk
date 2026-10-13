FROM python:3.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=5050

WORKDIR /app

COPY docker/requirements.txt docker/requirements.txt
RUN pip install -r docker/requirements.txt

COPY tools/slovakia_revenue_dashboard.py tools/results_db.py tools/import_results_to_db.py tools/
COPY docker/wsgi.py docker/wsgi.py
COPY results/ results/

RUN useradd --create-home appuser
USER appuser

EXPOSE 5050

# Hosting platforms (Render, Railway, Fly, ...) inject $PORT; locally it defaults to 5050.
CMD gunicorn --chdir /app/docker --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 wsgi:app
