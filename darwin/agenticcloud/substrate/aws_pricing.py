"""Live AWS pricing lookup for substrate cost calculations.

Two pricing sources:

1. ``ec2:DescribeSpotPriceHistory`` — real-time EC2 Spot prices per AZ.
   The Spot price at submit-time becomes the ``cost_usd_per_hour`` in the
   substrate evidence block of the v0.2 attestation.

2. ``pricing:GetProducts`` (AmazonEC2 service) — On-Demand baseline.
   Captured at the same instant as the Spot lookup so the attestation can
   record both the real cost and the on-demand reference (``savings_pct``).

Caching is per-process with a 24h TTL. The Pricing API does not change
minute-to-minute; the Spot price does, so the Spot client has a shorter
TTL (5 min) to keep cost annotations honest while still avoiding hot loops.

Failure policy: B2 — soft-fail. If a lookup raises, the caller catches
``PricingLookupError`` and emits an attestation with ``cost_usd: null`` and
``evidence.pricing_source: "unavailable"`` so verifiers can distinguish
'cost was zero' from 'cost was unrecorded'.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import aiobotocore.session


class PricingLookupError(RuntimeError):
    """Raised when AWS pricing lookup fails for any reason."""


@dataclass(frozen=True)
class SpotPriceQuote:
    """A point-in-time spot price for one instance type in one AZ."""

    instance_type: str
    availability_zone: str
    region: str
    price_per_hour_usd: Decimal
    quoted_at_iso: str  # ISO 8601, from AWS Timestamp field
    pricing_source: str = "ec2:DescribeSpotPriceHistory"


@dataclass(frozen=True)
class OnDemandQuote:
    """The on-demand list price for one instance type in one region."""

    instance_type: str
    region: str
    price_per_hour_usd: Decimal
    sku: str
    pricing_source: str = "pricing:GetProducts"


_SPOT_TTL_SEC = 300
_ONDEMAND_TTL_SEC = 86400


class AWSPricingClient:
    """Async pricing client. Caches per-process. Soft-fail by design."""

    def __init__(self, profile_name: str | None = None) -> None:
        self._session = aiobotocore.session.get_session()
        if profile_name:
            self._session.set_config_variable("profile", profile_name)
        self._spot_cache: dict[tuple[str, str], tuple[float, SpotPriceQuote]] = {}
        self._ondemand_cache: dict[tuple[str, str], tuple[float, OnDemandQuote]] = {}
        self._lock = asyncio.Lock()

    async def get_spot_price(self, *, region: str, instance_type: str) -> SpotPriceQuote:
        """Most recent Spot price for ``instance_type`` in ``region``.

        Returns the first (most recent) entry from
        DescribeSpotPriceHistory across all AZs. Caller is responsible for
        AZ pinning if needed.
        """
        key = (region, instance_type)
        async with self._lock:
            cached = self._spot_cache.get(key)
            if cached and (time.time() - cached[0]) < _SPOT_TTL_SEC:
                return cached[1]

        try:
            async with self._session.create_client("ec2", region_name=region) as ec2:
                resp = await ec2.describe_spot_price_history(
                    InstanceTypes=[instance_type],
                    ProductDescriptions=["Linux/UNIX"],
                    MaxResults=1,
                )
        except Exception as exc:
            raise PricingLookupError(f"describe_spot_price_history failed: {exc}") from exc

        history = resp.get("SpotPriceHistory") or []
        if not history:
            raise PricingLookupError(f"No spot price history for {instance_type} in {region}")

        entry = history[0]
        quote = SpotPriceQuote(
            instance_type=instance_type,
            availability_zone=entry["AvailabilityZone"],
            region=region,
            price_per_hour_usd=Decimal(entry["SpotPrice"]),
            quoted_at_iso=entry["Timestamp"].isoformat(),
        )
        async with self._lock:
            self._spot_cache[key] = (time.time(), quote)
        return quote

    async def get_ondemand_price(
        self, *, region: str, instance_type: str, location_name: str
    ) -> OnDemandQuote:
        """On-Demand m5.xlarge-class hourly rate, for the savings annotation.

        ``location_name`` is the human-readable AWS region name as it
        appears in the Pricing API (e.g. ``"US East (N. Virginia)"``).
        """
        key = (region, instance_type)
        async with self._lock:
            cached = self._ondemand_cache.get(key)
            if cached and (time.time() - cached[0]) < _ONDEMAND_TTL_SEC:
                return cached[1]

        try:
            async with self._session.create_client("pricing", region_name="us-east-1") as pricing:
                resp = await pricing.get_products(
                    ServiceCode="AmazonEC2",
                    Filters=[
                        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                        {"Type": "TERM_MATCH", "Field": "location", "Value": location_name},
                        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
                    ],
                    MaxResults=1,
                )
        except Exception as exc:
            raise PricingLookupError(f"pricing:get_products failed: {exc}") from exc

        price_list = resp.get("PriceList") or []
        if not price_list:
            raise PricingLookupError(f"No on-demand pricing for {instance_type} in {location_name}")

        sku_data = _parse_ondemand_sku(price_list[0])
        quote = OnDemandQuote(
            instance_type=instance_type,
            region=region,
            price_per_hour_usd=sku_data["price"],
            sku=sku_data["sku"],
        )
        async with self._lock:
            self._ondemand_cache[key] = (time.time(), quote)
        return quote


def _parse_ondemand_sku(price_list_entry: str | dict[str, Any]) -> dict[str, Any]:
    """Extract sku + USD price from a Pricing API PriceList entry.

    The entry is a JSON-encoded string OR a dict (the API returns strings,
    captured fixtures may be deserialized). Handle both for testability.
    """
    sku = json.loads(price_list_entry) if isinstance(price_list_entry, str) else price_list_entry
    terms = sku["terms"]["OnDemand"]
    first_term = next(iter(terms.values()))
    price_dim = next(iter(first_term["priceDimensions"].values()))
    return {
        "sku": sku["product"]["sku"],
        "price": Decimal(price_dim["pricePerUnit"]["USD"]),
    }


def compute_ec2_spot_cost(
    *,
    spot_price_per_hour: Decimal,
    wall_time_sec: float,
) -> Decimal:
    """Cost for ``wall_time_sec`` of EC2 Spot at the given hourly rate.

    EC2 billing is per-second with a 60-second minimum. Same rule for
    Spot. Returns Decimal USD.
    """
    billed_seconds = max(60, wall_time_sec)
    hours = Decimal(str(billed_seconds)) / Decimal("3600")
    return (spot_price_per_hour * hours).quantize(Decimal("0.00000001"))


def compute_savings_pct(*, spot_price: Decimal, ondemand_price: Decimal) -> Decimal:
    """Spot discount vs On-Demand, as a percentage (0-100)."""
    if ondemand_price == 0:
        return Decimal("0")
    saved = ondemand_price - spot_price
    return (saved / ondemand_price * Decimal("100")).quantize(Decimal("0.01"))
