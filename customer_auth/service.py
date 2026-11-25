"""Phone OTP exchange, customer registration and refresh-token lifecycle."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from customer_auth.msg91 import MSG91Client
from customer_auth.phones import PhoneNumberError, normalize_phone_number, verified_phone_matches
from customer_auth.storage import AuthStore, InvalidRefreshToken, RefreshTokenReuse, rate_limit_key
from customer_auth.tokens import AuthError, AuthSettings, TokenService

INDIA_TIME = ZoneInfo("Asia/Kolkata")


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Too many authentication attempts. Try again later.")
        self.retry_after = retry_after


class PhoneNotRegistered(Exception):
    pass


class InvalidRefreshSession(AuthError):
    code = "invalid_refresh_token"

    def __init__(self):
        super().__init__("Refresh token is invalid or expired.")


class RefreshSessionReuse(AuthError):
    code = "refresh_token_reuse"

    def __init__(self):
        super().__init__("Refresh token reuse detected. Sign in again.")

@dataclass(frozen=True)
class AuthSession:
    customer_id: str
    access_token: str
    refresh_token: str
    created: bool = False


class CustomerAuthService:
    def __init__(self, settings: AuthSettings, store: AuthStore, msg91: MSG91Client):
        self.settings = settings
        self.store = store
        self.msg91 = msg91
        self.tokens = TokenService(settings)

    def widget_config(self) -> dict[str, str]:
        return self.msg91.widget_config()

    def login(self, *, msg91_access_token: str, phone_number: str, region: str,
              client_address: str) -> AuthSession:
        phone = self._verify_phone(msg91_access_token, phone_number, region, "login", client_address)
        customer_id = self.store.find_customer_by_phone(phone)
        if customer_id is None:
            raise PhoneNotRegistered()
        return self._issue_session(customer_id)

    def register(
        self,
        *,
        msg91_access_token: str,
        phone_number: str,
        region: str,
        customer_name: str | None,
        household_size: int,
        preferred_weekday: str,
        client_address: str,
    ) -> AuthSession:
        phone = self._verify_phone(msg91_access_token, phone_number, region, "register", client_address)
        customer_id, created = self.store.register_customer(
            phone_e164=phone,
            customer_name=customer_name.strip() if customer_name and customer_name.strip() else None,
            household_size=household_size,
            preferred_weekday=preferred_weekday,
            signup_date=dt.datetime.now(INDIA_TIME).date(),
        )
        return self._issue_session(customer_id, created=created)

    def refresh(self, refresh_token: str, *, client_address: str) -> AuthSession:
        self._consume_limits("refresh", client_address)
        try:
            customer_id, next_refresh_token, _ = self.store.rotate_refresh_token(refresh_token)
        except RefreshTokenReuse as exc:
            raise RefreshSessionReuse() from exc
        except InvalidRefreshToken as exc:
            raise InvalidRefreshSession() from exc
        return AuthSession(
            customer_id=customer_id,
            access_token=self.tokens.issue(customer_id),
            refresh_token=next_refresh_token,
        )

    def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            self.store.revoke_refresh_token(refresh_token)

    def _verify_phone(self, msg91_access_token: str, phone_number: str, region: str,
                      action: str, client_address: str) -> str:
        try:
            submitted_phone = normalize_phone_number(phone_number, region)
        except PhoneNumberError as exc:
            raise ValueError(str(exc)) from exc
        self._consume_limits(action, client_address, submitted_phone)
        verified_identifier = self.msg91.verify_access_token(msg91_access_token)
        if not verified_phone_matches(verified_identifier, submitted_phone, region):
            from customer_auth.msg91 import InvalidMSG91AccessToken

            raise InvalidMSG91AccessToken("The verified phone number does not match the submitted number.")
        return submitted_phone

    def _consume_limits(self, action: str, client_address: str, phone: str | None = None) -> None:
        limits = [(rate_limit_key(f"{action}:ip", client_address), 20, 15 * 60)]
        if phone:
            limits.append((rate_limit_key(f"{action}:phone", phone), 5, 15 * 60))
        for key, limit, window_seconds in limits:
            allowed, retry_after = self.store.consume_rate_limit(
                key, limit=limit, window_seconds=window_seconds,
            )
            if not allowed:
                raise RateLimitExceeded(retry_after)

    def _issue_session(self, customer_id: str, *, created: bool = False) -> AuthSession:
        refresh_token, _ = self.store.create_refresh_token(
            customer_id, self.settings.refresh_token_ttl_seconds,
        )
        return AuthSession(
            customer_id=customer_id,
            access_token=self.tokens.issue(customer_id),
            refresh_token=refresh_token,
            created=created,
        )
