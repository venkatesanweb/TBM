#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py collectstatic --noinput
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 1 \
  --threads 4 \
  --access-logfile -
