"""Single import point for auth in Shubham's blueprints.

Uses Sameer's app/auth.py when it exists, otherwise the temporary app/auth_fallback.py.
When his module lands: delete this file and auth_fallback.py, and change
`from app.auth_bridge import ...` to `from app.auth import ...`.
"""

try:
    from app.auth import get_current_customer_id, issue_token, login_required  # noqa: F401
except ModuleNotFoundError as e:
    if e.name != "app.auth":
        raise
    from app.auth_fallback import get_current_customer_id, issue_token, login_required  # noqa: F401
