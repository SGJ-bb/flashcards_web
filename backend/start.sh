#!/bin/bash
# Run database migrations before starting the server
python manage.py migrate --noinput
# Start gunicorn
exec gunicorn flashcards_project.wsgi:application --bind 0.0.0.0:$PORT --workers 4