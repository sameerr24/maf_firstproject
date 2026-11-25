from flask import Blueprint

bp = Blueprint("subscriptions", __name__)

from app.subscriptions import routes  # noqa: E402,F401  (registers the routes on bp)
