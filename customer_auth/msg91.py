"""Server-side verification of MSG91 OTP Widget access tokens."""

from __future__ import annotations

from dataclasses import dataclass, field

import requests

from customer_auth.tokens import AuthError

VERIFY_ACCESS_TOKEN_URL = "https://control.msg91.com/api/v5/widget/verifyAccessToken"


class MSG91Error(Exception):
    """Base class for MSG91 verification failures."""


class InvalidMSG91AccessToken(AuthError, MSG91Error):
    """MSG91 rejected an OTP Widget access token."""

    code = "invalid_verification"

    def __init__(self, message: str = "MSG91 rejected the verification result."):
        super().__init__(message)


class MSG91Unavailable(MSG91Error):
    """MSG91 could not be reached or returned an unusable response."""


@dataclass(frozen=True)
class MSG91Settings:
    widget_id: str | None
    token_auth: str | None = field(repr=False)
    authkey: str | None = field(repr=False)


class MSG91Client:
    def __init__(self, settings: MSG91Settings, *, timeout_seconds: float = 5):
        self.settings = settings
        self.timeout_seconds = timeout_seconds

    def widget_config(self) -> dict[str, str]:
        if not self.settings.widget_id or not self.settings.token_auth:
            raise MSG91Unavailable("MSG91 Widget is not configured.")
        return {"widget_id": self.settings.widget_id, "token_auth": self.settings.token_auth}

    def verify_access_token(self, access_token: str) -> str:
        if not self.settings.authkey:
            raise MSG91Unavailable("MSG91 server verification is not configured.")
        try:
            response = requests.post(
                VERIFY_ACCESS_TOKEN_URL,
                headers={"authkey": self.settings.authkey, "content-type": "application/json"},
                json={"access-token": access_token},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException:
            raise MSG91Unavailable("MSG91 verification could not be reached.") from None
        if response.status_code in {400, 401, 403}:
            raise InvalidMSG91AccessToken("MSG91 rejected the verification result.")
        if not response.ok:
            raise MSG91Unavailable("MSG91 verification returned an error.")
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            raise MSG91Unavailable("MSG91 returned an invalid response.") from None
        if not isinstance(payload, dict) or str(payload.get("type", "")).lower() != "success":
            raise InvalidMSG91AccessToken("MSG91 rejected the verification result.")

        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        candidates = [payload.get("data"), payload.get("message"), payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for key in ("mobile", "phone", "identifier", "mobileNumber", "mobile_no"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        raise MSG91Unavailable("MSG91 response did not contain a verified mobile number.")
