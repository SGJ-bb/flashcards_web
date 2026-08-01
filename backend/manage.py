#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def _load_env():
    """Load variables from a local .env file if python-dotenv is installed.

    This keeps dev workflow simple (just edit .env) without making dotenv a
    hard runtime requirement in production.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    # Look for .env next to manage.py.
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))


def main():
    """Run administrative tasks."""
    _load_env()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'flashcards_project.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
