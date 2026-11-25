"""Pydantic response schemas for the recommendation API."""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RecommendationItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rank: int = Field(ge=1)
    product_id: str
    product_name: str
    brand: str | None = None
    product_type: str | None = None
    category: str | None = None
    product_image: str | None = Field(None, description="Image URL from products.image_url, if any")
    pack_size: str | None = Field(None, description="What one unit of suggested_quantity is, e.g. '500 ml'")
    price: float | None = Field(None, ge=0, description="Regular price per pack (products.price)")
    discount_price: float | None = Field(None, ge=0, description="Subscriber price per pack (products.discount_price)")
    purchase_count: int = Field(ge=1, description="Distinct purchase occasions")
    first_purchase_date: dt.date
    last_purchase_date: dt.date
    days_since_last_purchase: int | None = Field(None, ge=0)
    expected_purchase_interval: float | None = Field(None, description="Recency-weighted median days between purchases")
    due_ratio: float | None = Field(None, ge=0, description="days_since_last_purchase / expected_purchase_interval")
    due_score: float | None = Field(None, ge=0, le=1, description="null = not measurable (fewer than 2 purchases)")
    habit_score: float = Field(ge=0, le=1)
    habit_category: str | None = None
    habit_threshold: float = Field(gt=0)
    consistency_score: float | None = Field(None, ge=0, le=1, description="null = not measurable")
    consistency_status: str = Field(description="sufficient_history | insufficient_history")
    final_score: float = Field(ge=0, le=1, description="Weighted score multiplied by lapse_factor")
    lapse_factor: float = Field(ge=0, le=1, description="1 = habit active; < 1 = habit lapsed, ranked lower")
    fallbacks_applied: list[str] = Field(description="Scores replaced by configured fallbacks inside final_score")
    replenishment_status: str = Field(
        description="Not Due Yet | Due Soon | Approaching Due | Due | Overdue | Lapsed | Insufficient History")
    suggested_quantity: int | None = Field(None, ge=1, description="Units of pack_size; never used for ranking")
    explanation: str


class RecommendationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    reference_date: dt.date = Field(description="The 'today' used for days-since and due calculations")
    total_recommendations: int = Field(ge=0)
    total_eligible_products: int = Field(ge=0, description="Eligible products ranked before applying the limit")
    recommendations: list[RecommendationItem]


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class PhoneAuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    msg91_access_token: str = Field(min_length=1, max_length=4096)
    phone_number: str = Field(min_length=1, max_length=64)
    region: str = Field(min_length=2, max_length=2, pattern="^[A-Za-z]{2}$")


class RegistrationRequest(PhoneAuthRequest):
    customer_name: str | None = Field(None, max_length=100)
    household_size: int = Field(default=1, ge=1, le=50)
    preferred_weekday: Literal["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the token expires")
    customer_id: str
    created: bool = False


class WidgetConfig(BaseModel):
    widget_id: str
    token_auth: str


class CurrentCustomer(BaseModel):
    customer_id: str


class HealthResponse(BaseModel):
    status: str
    service: str
