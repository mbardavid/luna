"""Tests for a2a.ctf_delegate — CTF delegation via TaskSpec.

Replaces the old test_ctf_adapter.py. Instead of testing on-chain
transactions, we now test that the correct TaskSpec payloads are
generated for merge/split/bridge operations.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from a2a.ctf_delegate import (
    CTFDelegate,
    DEFAULT_CTF_EXCHANGE_ADDRESS,
    DEFAULT_NEG_RISK_CTF_ADDRESS,
    DEFAULT_USDC_ADDRESS,
    TOPIC_BRIDGE_RESULT,
    TOPIC_MERGE_RESULT,
    TOPIC_SPLIT_RESULT,
    USDC_DECIMALS,
)
from a2a.task_spec import RiskClassification, TaskSpec


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def delegate() -> CTFDelegate:
    """Create a CTFDelegate with default settings."""
    return CTFDelegate(default_gas_ceiling_gwei=100)


# ── TaskSpec Schema Tests ────────────────────────────────────────────


class TestTaskSpec:
    """Tests for TaskSpec schema."""

    def test_default_values(self) -> None:
        """TaskSpec should have sensible defaults."""
        spec = TaskSpec(
            operation="ctf.merge",
            params={"amount": "100"},
            callback_topic="test.topic",
        )
        assert spec.version == "1.0"
        assert spec.operation == "ctf.merge"
        assert spec.source_agent == "polymarket-mm"
        assert spec.handoff_id  # Should be auto-generated UUID

    def test_to_json_roundtrip(self) -> None:
        """Should serialise to JSON and back."""
        spec = TaskSpec(
            operation="ctf.split",
            params={"amount_usdc": "50"},
            callback_topic="result.topic",
        )
        json_str = spec.to_json()
        assert '"ctf.split"' in json_str
        assert '"result.topic"' in json_str

    def test_to_dict(self) -> None:
        """Should convert to dict with all fields."""
        spec = TaskSpec(
            operation="bridge.deposit",
            params={"chain": "polygon"},
            callback_topic="bridge.result",
        )
        d = spec.to_dict()
        assert d["operation"] == "bridge.deposit"
        assert d["params"]["chain"] == "polygon"
        assert d["callback_topic"] == "bridge.result"
        assert "handoff_id" in d
        assert "version" in d

    def test_custom_risk_classification(self) -> None:
        """Should accept custom risk parameters."""
        spec = TaskSpec(
            operation="ctf.merge",
            params={},
            callback_topic="test",
            risk=RiskClassification(
                classification="high",
                requires_confirmation=True,
                max_gas_gwei=200,
                deadline_seconds=60,
            ),
        )
        assert spec.risk.classification == "high"
        assert spec.risk.requires_confirmation is True
        assert spec.risk.max_gas_gwei == 200
        assert spec.risk.deadline_seconds == 60


class TestRiskClassification:
    """Tests for RiskClassification defaults."""

    def test_defaults(self) -> None:
        """Default risk should be medium, no confirmation required."""
        risk = RiskClassification()
        assert risk.classification == "medium"
        assert risk.requires_confirmation is False
        assert risk.max_gas_gwei is None
        assert risk.deadline_seconds is None


# ── CTFDelegate Amount Conversion ────────────────────────────────────


class TestCTFDelegateAmountConversion:
    """Tests for USDC amount conversion."""

    def test_to_raw_amount(self, delegate: CTFDelegate) -> None:
        """100 USDC → 100_000_000 raw (6 decimals)."""
        raw = delegate._to_raw_amount(Decimal("100"))
        assert raw == 100_000_000

    def test_to_raw_amount_fractional(self, delegate: CTFDelegate) -> None:
        """0.50 USDC → 500_000 raw."""
        raw = delegate._to_raw_amount(Decimal("0.50"))
        assert raw == 500_000

    def test_to_raw_amount_zero(self, delegate: CTFDelegate) -> None:
        """0 USDC → 0 raw."""
        raw = delegate._to_raw_amount(Decimal("0"))
        assert raw == 0


# ── Merge Request Tests ──────────────────────────────────────────────


class TestRequestMerge:
    """Tests for request_merge method."""

    @pytest.mark.asyncio
    async def test_merge_generates_valid_task_spec(
        self, delegate: CTFDelegate
    ) -> None:
        """request_merge should generate a valid TaskSpec dict."""
        result = await delegate.request_merge(
            market_id="market-123",
            condition_id="0x" + "aa" * 32,
            qty=Decimal("100"),
            token_id_yes="tok_yes",
            token_id_no="tok_no",
        )

        assert result["operation"] == "ctf.merge"
        assert result["version"] == "1.0"
        assert result["callback_topic"] == TOPIC_MERGE_RESULT
        assert result["source_agent"] == "polymarket-mm"
        assert result["handoff_id"]  # UUID string

    @pytest.mark.asyncio
    async def test_merge_params_correct(self, delegate: CTFDelegate) -> None:
        """Merge params should contain all required fields."""
        result = await delegate.request_merge(
            market_id="mkt-1",
            condition_id="0xdef",
            qty=Decimal("200"),
            token_id_yes="yes_tok",
            token_id_no="no_tok",
        )

        params = result["params"]
        assert params["market_id"] == "mkt-1"
        assert params["condition_id"] == "0xdef"
        assert params["amount_usdc"] == "200"
        assert params["amount_raw"] == 200_000_000
        assert params["token_id_yes"] == "yes_tok"
        assert params["token_id_no"] == "no_tok"
        assert params["neg_risk"] is False
        assert params["ctf_address"] == DEFAULT_CTF_EXCHANGE_ADDRESS
        assert params["usdc_address"] == DEFAULT_USDC_ADDRESS

    @pytest.mark.asyncio
    async def test_merge_neg_risk_uses_correct_address(
        self, delegate: CTFDelegate
    ) -> None:
        """neg_risk=True should use the neg-risk CTF address."""
        result = await delegate.request_merge(
            market_id="mkt-2",
            condition_id="0xabc",
            qty=Decimal("50"),
            token_id_yes="y",
            token_id_no="n",
            neg_risk=True,
        )

        assert result["params"]["neg_risk"] is True
        assert result["params"]["ctf_address"] == DEFAULT_NEG_RISK_CTF_ADDRESS

    @pytest.mark.asyncio
    async def test_merge_risk_classification(
        self, delegate: CTFDelegate
    ) -> None:
        """Merge should have medium risk by default."""
        result = await delegate.request_merge(
            market_id="mkt-3",
            condition_id="0x123",
            qty=Decimal("100"),
            token_id_yes="y",
            token_id_no="n",
        )

        risk = result["risk"]
        assert risk["classification"] == "medium"
        assert risk["requires_confirmation"] is False
        assert risk["max_gas_gwei"] == 100

    @pytest.mark.asyncio
    async def test_merge_with_confirmation(
        self, delegate: CTFDelegate
    ) -> None:
        """requires_confirmation should propagate to risk."""
        result = await delegate.request_merge(
            market_id="mkt-4",
            condition_id="0x456",
            qty=Decimal("100"),
            token_id_yes="y",
            token_id_no="n",
            requires_confirmation=True,
        )

        assert result["risk"]["requires_confirmation"] is True


# ── Split Request Tests ──────────────────────────────────────────────


class TestRequestSplit:
    """Tests for request_split method."""

    @pytest.mark.asyncio
    async def test_split_generates_valid_task_spec(
        self, delegate: CTFDelegate
    ) -> None:
        """request_split should generate a valid TaskSpec dict."""
        result = await delegate.request_split(
            market_id="market-456",
            condition_id="0x" + "bb" * 32,
            qty_usd=Decimal("200"),
        )

        assert result["operation"] == "ctf.split"
        assert result["callback_topic"] == TOPIC_SPLIT_RESULT
        assert result["handoff_id"]

    @pytest.mark.asyncio
    async def test_split_params_correct(self, delegate: CTFDelegate) -> None:
        """Split params should contain all required fields."""
        result = await delegate.request_split(
            market_id="mkt-5",
            condition_id="0xfed",
            qty_usd=Decimal("300"),
        )

        params = result["params"]
        assert params["market_id"] == "mkt-5"
        assert params["condition_id"] == "0xfed"
        assert params["amount_usdc"] == "300"
        assert params["amount_raw"] == 300_000_000
        assert params["neg_risk"] is False
        assert params["ctf_address"] == DEFAULT_CTF_EXCHANGE_ADDRESS

    @pytest.mark.asyncio
    async def test_split_neg_risk(self, delegate: CTFDelegate) -> None:
        """neg_risk=True should use the neg-risk address."""
        result = await delegate.request_split(
            market_id="mkt-6",
            condition_id="0xabc",
            qty_usd=Decimal("100"),
            neg_risk=True,
        )

        assert result["params"]["ctf_address"] == DEFAULT_NEG_RISK_CTF_ADDRESS


# ── Bridge Request Tests ─────────────────────────────────────────────


class TestRequestBridge:
    """Tests for request_bridge_deposit method."""

    @pytest.mark.asyncio
    async def test_bridge_generates_valid_task_spec(
        self, delegate: CTFDelegate
    ) -> None:
        """request_bridge_deposit should generate a valid TaskSpec."""
        result = await delegate.request_bridge_deposit(
            amount_usd=Decimal("1000"),
        )

        assert result["operation"] == "bridge.deposit"
        assert result["callback_topic"] == TOPIC_BRIDGE_RESULT

    @pytest.mark.asyncio
    async def test_bridge_params_correct(
        self, delegate: CTFDelegate
    ) -> None:
        """Bridge params should contain chain info."""
        result = await delegate.request_bridge_deposit(
            amount_usd=Decimal("500"),
            source_chain="ethereum",
            dest_chain="polygon",
        )

        params = result["params"]
        assert params["amount_usdc"] == "500"
        assert params["amount_raw"] == 500_000_000
        assert params["source_chain"] == "ethereum"
        assert params["dest_chain"] == "polygon"

    @pytest.mark.asyncio
    async def test_bridge_high_risk(self, delegate: CTFDelegate) -> None:
        """Bridge should have high risk classification."""
        result = await delegate.request_bridge_deposit(
            amount_usd=Decimal("100"),
        )

        assert result["risk"]["classification"] == "high"
        assert result["risk"]["requires_confirmation"] is True

    @pytest.mark.asyncio
    async def test_bridge_no_confirmation(
        self, delegate: CTFDelegate
    ) -> None:
        """Should allow overriding requires_confirmation."""
        result = await delegate.request_bridge_deposit(
            amount_usd=Decimal("100"),
            requires_confirmation=False,
        )

        assert result["risk"]["requires_confirmation"] is False


# ── Unique Handoff IDs ───────────────────────────────────────────────


class TestHandoffIds:
    """Tests for unique handoff ID generation."""

    @pytest.mark.asyncio
    async def test_unique_handoff_ids(self, delegate: CTFDelegate) -> None:
        """Each request should generate a unique handoff_id."""
        results = []
        for _ in range(5):
            r = await delegate.request_merge(
                market_id="mkt",
                condition_id="0xabc",
                qty=Decimal("100"),
                token_id_yes="y",
                token_id_no="n",
            )
            results.append(r)

        handoff_ids = {r["handoff_id"] for r in results}
        assert len(handoff_ids) == 5  # All unique


# ── Gas Ceiling ──────────────────────────────────────────────────────


class TestGasCeiling:
    """Tests for gas ceiling configuration."""

    @pytest.mark.asyncio
    async def test_custom_gas_ceiling(self) -> None:
        """Custom gas ceiling should propagate to TaskSpec."""
        delegate = CTFDelegate(default_gas_ceiling_gwei=200)
        result = await delegate.request_merge(
            market_id="mkt",
            condition_id="0xabc",
            qty=Decimal("100"),
            token_id_yes="y",
            token_id_no="n",
        )
        assert result["risk"]["max_gas_gwei"] == 200
