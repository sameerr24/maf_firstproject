"""ASGI entry point for the recommendation API. Separate process from the Flask app.

    uvicorn recommendation_api.main:app --reload --port 8000
    python -m recommendation_api

Building the app reads settings (AUTH_SECRET_KEY is required) but does not connect to
the database; the first recommendation request does.
"""

from recommendation_api.factory import create_app

app = create_app()
