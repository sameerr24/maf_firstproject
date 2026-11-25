"""Phone number parsing and normalization for customer authentication."""

from __future__ import annotations

import phonenumbers
from phonenumbers import NumberParseException


class PhoneNumberError(ValueError):
    """The supplied phone number is not a valid number for the selected region."""


def normalize_phone_number(phone_number: str, region: str) -> str:
    if not isinstance(phone_number, str) or not phone_number.strip():
        raise PhoneNumberError("Enter a phone number.")
    if not isinstance(region, str) or len(region) != 2 or not region.isalpha():
        raise PhoneNumberError("Choose a valid country.")
    try:
        parsed = phonenumbers.parse(phone_number.strip(), region.upper())
    except NumberParseException:
        raise PhoneNumberError("Enter a valid phone number.") from None
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneNumberError("Enter a valid phone number.")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def verified_phone_matches(verified_identifier: str, submitted_e164: str, region: str) -> bool:
    """Compare MSG91's verified identifier with the normalized number submitted by the user."""
    try:
        verified_e164 = normalize_phone_number(verified_identifier, region)
    except PhoneNumberError:
        digits = "".join(character for character in verified_identifier if character.isdigit())
        verified_e164 = f"+{digits}" if digits else ""
    return verified_e164 == submitted_e164
