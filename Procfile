# Le migrazioni girano all'avvio, non in fase di build: il database è
# raggiungibile solo a runtime (su Railway la rete privata non esiste durante la build).
web: python manage.py migrate --noinput && python manage.py ensure_superuser && python manage.py seed_demo --if-requested && gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --timeout 60 --access-logfile -
