"""Dev entry point: `python run.py` (port FLASK_PORT, default 5001).

Port 5000 is taken by macOS's AirPlay Receiver, so the default is 5001. With the Flask CLI:
`flask --app run run --port 5001`.
"""

import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(port=int(os.getenv("FLASK_PORT") or 5001))
