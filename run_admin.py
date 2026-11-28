"""Run the admin panel API: python run_admin.py (127.0.0.1:ADMIN_PORT, default 5002).

Binds to 127.0.0.1 only, never 0.0.0.0: the admin API has no login by design (local-only POC).
Don't tunnel or expose this port.
"""

from app.admin import create_admin_app

app = create_admin_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=app.config["ADMIN_PORT"], debug=app.config.get("DEBUG", False))
