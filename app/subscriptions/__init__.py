"""Subscriptions module. The blueprint's routes live in routes.py, which the customer app
imports when it registers the blueprint (app.create_app). They're not imported here, so code
that only needs the service or the scheduler (e.g. the admin app) doesn't load app.auth."""

from flask import Blueprint

bp = Blueprint("subscriptions", __name__)
