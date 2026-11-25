#!/usr/bin/env python3
"""Run the recommendation engine from the terminal, without Flask or FastAPI.

Uses exactly the same ``RecommendationEngine`` as the FastAPI endpoint. Reads purchase
history from DATABASE_URL (.env). Read-only: nothing is written to the database.

    python scripts/run_recommendations.py --customer-id CUST-0001
    python scripts/run_recommendations.py --customer-id CUST-0001 --limit 5 --as-of 2026-11-10
    python scripts/run_recommendations.py --customer-id CUST-0001 --json

Exit codes: 0 success, 1 invalid input or unknown customer, 2 configuration/database error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from anywhere

from database.connection import DatabaseConfigError  # noqa: E402
from recommendation_engine import (  # noqa: E402
    ConfigError, CustomerNotFoundError, DataSourceError, InvalidRequestError, RecommendationEngine, load_config,
)
from recommendation_engine.output import render_table, result_to_dict  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Show replenishment recommendations for one customer.")
    ap.add_argument("--customer-id", required=True, help="e.g. CUST-0001")
    ap.add_argument("--limit", type=int, default=None, help="number of recommendations (default from config)")
    ap.add_argument("--as-of", default=None, help="evaluate as of this date (YYYY-MM-DD)")
    ap.add_argument("--config", default=None, help="alternative scoring_config.json")
    ap.add_argument("--json", action="store_true", help="print the same JSON the API returns")
    ap.add_argument("--no-explanations", action="store_true", help="table without explanation lines")
    args = ap.parse_args(argv)

    try:
        engine = RecommendationEngine(config=load_config(args.config))
        result = engine.generate_recommendations(customer_id=args.customer_id, limit=args.limit, as_of=args.as_of)
    except (InvalidRequestError, CustomerNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except (ConfigError, DatabaseConfigError, DataSourceError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result_to_dict(result), indent=2))
    else:
        print(render_table(result, explanations=not args.no_explanations))
    return 0


if __name__ == "__main__":
    sys.exit(main())
