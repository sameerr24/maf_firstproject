"""JWT customer authentication shared by the FastAPI and Flask apps.

Access tokens contain only the customer's internal ID and standard JWT claims.
Refresh tokens are opaque, rotated, and stored as hashes by ``customer_auth.storage``.
"""

from customer_auth.tokens import (  # noqa: F401
    AuthConfigError, AuthError, AuthSettings, ExpiredTokenError, InvalidTokenError, MissingTokenError,
    TokenService, bearer_token_from_header,
)
