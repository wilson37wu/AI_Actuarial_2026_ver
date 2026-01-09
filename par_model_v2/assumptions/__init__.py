"""
Assumptions Module

Provides table-driven actuarial assumptions with hierarchical lookup.
"""

from par_model_v2.assumptions.banding import (
    get_premium_band,
    get_sa_band,
)
from par_model_v2.assumptions.provider import AssumptionProvider
from par_model_v2.assumptions.saa_provider import SAAProvider

__all__ = [
    "AssumptionProvider",
    "get_sa_band",
    "get_premium_band",
    "SAAProvider",
]
