"""Unit tests for darwin.agenticcloud.substrate.aws_pricing.

All AWS calls are mocked with aiobotocore stubs. The captured real-API
fixtures in tests/fixtures/ are the source of truth for response shapes.
"""

from __future__ import annotations

import datetime
import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from darwin.agenticcloud.substrate.aws_pricing import (
    AWSPricingClient,
    OnDemandQuote,
    PricingLookupError,
    SpotPriceQuote,
    _parse_ondemand_sku,
    compute_ec2_spot_cost,
    compute_savings_pct,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def spot_fixture() -> dict:
    return json.loads((FIXTURES / "ec2_spot_history_m5xlarge.json").read_text())


@pytest.fixture
def ondemand_fixture() -> dict:
    return json.loads((FIXTURES / "ec2_ondemand_m5xlarge.json").read_text())


# ----- compute_ec2_spot_cost -----


class TestComputeEC2SpotCost:
    def test_one_minute_minimum_applies(self) -> None:
        # 10s of compute is billed as 60s minimum.
        cost = compute_ec2_spot_cost(
            spot_price_per_hour=Decimal("0.0734"),
            wall_time_sec=10,
        )
        # 0.0734 * (60 / 3600) = 0.00122333...
        assert cost == Decimal("0.00122333")

    def test_one_hour_real_data_workload(self) -> None:
        cost = compute_ec2_spot_cost(
            spot_price_per_hour=Decimal("0.0734"),
            wall_time_sec=3600,
        )
        assert cost == Decimal("0.07340000")

    def test_partial_hour_billed_per_second(self) -> None:
        # 90 seconds at $0.10/hr = 0.0025
        cost = compute_ec2_spot_cost(
            spot_price_per_hour=Decimal("0.10"),
            wall_time_sec=90,
        )
        assert cost == Decimal("0.00250000")

    def test_zero_wall_time_still_pays_minimum(self) -> None:
        cost = compute_ec2_spot_cost(
            spot_price_per_hour=Decimal("0.10"),
            wall_time_sec=0,
        )
        # 60s minimum: 0.10 * (60 / 3600) = 0.00166666...
        assert cost == Decimal("0.00166667")

    def test_uses_decimal_not_float(self) -> None:
        # Money math must never use IEEE 754. Result type must be Decimal.
        cost = compute_ec2_spot_cost(
            spot_price_per_hour=Decimal("0.073400"),
            wall_time_sec=3600,
        )
        assert isinstance(cost, Decimal)


# ----- compute_savings_pct -----


class TestComputeSavingsPct:
    def test_spot_cheaper_than_ondemand(self) -> None:
        savings = compute_savings_pct(
            spot_price=Decimal("0.0734"),
            ondemand_price=Decimal("0.192"),
        )
        # (0.192 - 0.0734) / 0.192 * 100 = 61.77%
        assert savings == Decimal("61.77")

    def test_spot_equal_to_ondemand_zero_savings(self) -> None:
        savings = compute_savings_pct(
            spot_price=Decimal("0.192"),
            ondemand_price=Decimal("0.192"),
        )
        assert savings == Decimal("0.00")

    def test_ondemand_zero_no_division_error(self) -> None:
        savings = compute_savings_pct(
            spot_price=Decimal("0"),
            ondemand_price=Decimal("0"),
        )
        assert savings == Decimal("0")


# ----- _parse_ondemand_sku -----


class TestParseOndemandSKU:
    def test_parses_real_fixture(self, ondemand_fixture: dict) -> None:
        # First entry of the captured PriceList.
        entry = ondemand_fixture["PriceList"][0]
        result = _parse_ondemand_sku(entry)
        assert result["sku"] == "5G4TA8Z4MUKE6MJB"
        assert result["price"] == Decimal("0.1920000000")

    def test_accepts_dict_input(self, ondemand_fixture: dict) -> None:
        # Tests pass dicts directly without re-encoding to JSON.
        entry = ondemand_fixture["PriceList"][0]
        as_dict = json.loads(entry)
        result = _parse_ondemand_sku(as_dict)
        assert result["sku"] == "5G4TA8Z4MUKE6MJB"

    def test_accepts_json_string_input(self, ondemand_fixture: dict) -> None:
        # Real API responses come as JSON strings.
        entry = ondemand_fixture["PriceList"][0]
        assert isinstance(entry, str)
        result = _parse_ondemand_sku(entry)
        assert result["price"] == Decimal("0.1920000000")


# ----- AWSPricingClient.get_spot_price -----


class TestGetSpotPrice:
    @pytest.mark.asyncio
    async def test_returns_first_history_entry(self, spot_fixture: dict) -> None:
        mock_ec2 = AsyncMock()
        mock_ec2.describe_spot_price_history = AsyncMock(
            return_value={
                "SpotPriceHistory": [
                    {
                        "InstanceType": "m5.xlarge",
                        "AvailabilityZone": "us-east-1a",
                        "SpotPrice": "0.0734",
                        "Timestamp": datetime.datetime(2026, 5, 27, 4, 0, 6),
                    }
                ]
            }
        )

        mock_session = MagicMock()
        mock_session.create_client = MagicMock(return_value=_async_context(mock_ec2))

        client = AWSPricingClient()
        client._session = mock_session

        quote = await client.get_spot_price(region="us-east-1", instance_type="m5.xlarge")
        assert isinstance(quote, SpotPriceQuote)
        assert quote.price_per_hour_usd == Decimal("0.0734")
        assert quote.availability_zone == "us-east-1a"
        assert quote.pricing_source == "ec2:DescribeSpotPriceHistory"

    @pytest.mark.asyncio
    async def test_empty_history_raises_pricing_lookup_error(self) -> None:
        mock_ec2 = AsyncMock()
        mock_ec2.describe_spot_price_history = AsyncMock(return_value={"SpotPriceHistory": []})
        mock_session = MagicMock()
        mock_session.create_client = MagicMock(return_value=_async_context(mock_ec2))

        client = AWSPricingClient()
        client._session = mock_session

        with pytest.raises(PricingLookupError, match="No spot price history"):
            await client.get_spot_price(region="us-east-1", instance_type="m5.xlarge")

    @pytest.mark.asyncio
    async def test_api_failure_raises_pricing_lookup_error(self) -> None:
        mock_ec2 = AsyncMock()
        mock_ec2.describe_spot_price_history = AsyncMock(side_effect=RuntimeError("network broke"))
        mock_session = MagicMock()
        mock_session.create_client = MagicMock(return_value=_async_context(mock_ec2))

        client = AWSPricingClient()
        client._session = mock_session

        with pytest.raises(PricingLookupError, match="describe_spot_price_history failed"):
            await client.get_spot_price(region="us-east-1", instance_type="m5.xlarge")

    @pytest.mark.asyncio
    async def test_cache_hit_avoids_second_api_call(self) -> None:
        mock_ec2 = AsyncMock()
        mock_ec2.describe_spot_price_history = AsyncMock(
            return_value={
                "SpotPriceHistory": [
                    {
                        "InstanceType": "m5.xlarge",
                        "AvailabilityZone": "us-east-1a",
                        "SpotPrice": "0.05",
                        "Timestamp": datetime.datetime(2026, 5, 27, 4, 0, 6),
                    }
                ]
            }
        )
        mock_session = MagicMock()
        mock_session.create_client = MagicMock(return_value=_async_context(mock_ec2))

        client = AWSPricingClient()
        client._session = mock_session

        await client.get_spot_price(region="us-east-1", instance_type="m5.xlarge")
        await client.get_spot_price(region="us-east-1", instance_type="m5.xlarge")

        # Cache should suppress the second call.
        assert mock_ec2.describe_spot_price_history.call_count == 1


# ----- AWSPricingClient.get_ondemand_price -----


class TestGetOndemandPrice:
    @pytest.mark.asyncio
    async def test_parses_real_fixture_via_api(self, ondemand_fixture: dict) -> None:
        mock_pricing = AsyncMock()
        mock_pricing.get_products = AsyncMock(return_value=ondemand_fixture)
        mock_session = MagicMock()
        mock_session.create_client = MagicMock(return_value=_async_context(mock_pricing))

        client = AWSPricingClient()
        client._session = mock_session

        quote = await client.get_ondemand_price(
            region="us-east-1",
            instance_type="m5.xlarge",
            location_name="US East (N. Virginia)",
        )
        assert isinstance(quote, OnDemandQuote)
        assert quote.price_per_hour_usd == Decimal("0.1920000000")
        assert quote.sku == "5G4TA8Z4MUKE6MJB"

    @pytest.mark.asyncio
    async def test_empty_pricelist_raises(self) -> None:
        mock_pricing = AsyncMock()
        mock_pricing.get_products = AsyncMock(return_value={"PriceList": []})
        mock_session = MagicMock()
        mock_session.create_client = MagicMock(return_value=_async_context(mock_pricing))

        client = AWSPricingClient()
        client._session = mock_session

        with pytest.raises(PricingLookupError, match="No on-demand pricing"):
            await client.get_ondemand_price(
                region="us-east-1",
                instance_type="m5.xlarge",
                location_name="US East (N. Virginia)",
            )


# ----- async context manager helper for aiobotocore mocks -----


def _async_context(target: AsyncMock):
    """Wraps an AsyncMock so create_client(...).__aenter__ returns it.

    aiobotocore.session.AioSession.create_client returns an async context
    manager, not the client directly. This helper mimics that.
    """
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=target)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx
