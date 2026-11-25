"""Data types and repositories. ``SqlPurchaseRepository`` is imported from
``recommendation_engine.data.sql_repository`` on demand so the engine can be used
without a database driver."""

from recommendation_engine.data.purchase_data import ProductInfo, PurchaseOccasion, PurchaseRecord  # noqa: F401
from recommendation_engine.data.repository import InMemoryPurchaseRepository, PurchaseRepository  # noqa: F401
