FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R app:app /app
EXPOSE 8000

USER app
ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
