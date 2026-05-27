"""
tests.substrate.test_aws_batch
==============================

Tests for darwin.agenticcloud.substrate.aws_batch. Fully mocked, zero AWS calls.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import replace as dataclass_replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from darwin.agenticcloud.signing import verify_signature
from darwin.agenticcloud.substrate.aws_batch import (
    EVIDENCE_SCHEMA_ID,
    SUBSTRATE_VERSION,
    AwsCredentialProvider,
    AwsCredentials,
    BatchJobFailed,
    BatchPricing,
    BatchSubstrate,
    BatchSubstrateError,
    BatchUnreachable,
)
from darwin.agenticcloud.substrate.aws_batch_event import (
    EVENT_SCHEMA_URI,
    MAX_CODE_BYTES,
    MAX_MEMORY_MB,
)
from darwin.agenticcloud.substrate.aws_pricing import (
    AWSPricingClient,
    OnDemandQuote,
    SpotPriceQuote,
)
from darwin.agenticcloud.substrate.base import (
    EVIDENCE_REGISTRY,
    PreflightRejected,
    RunResult,
    Substrate,
    build_attestation_dict,
    build_identity_payload,
    iso8601_now,
    sign_identity,
)
from darwin.agenticcloud.substrate.identity import OperatorFallbackSigner
from darwin.agenticcloud.types import WorkloadSpec

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture
def spot_quote() -> SpotPriceQuote:
    return SpotPriceQuote(
        instance_type="m5.xlarge",
        availability_zone="us-east-1a",
        region="us-east-1",
        price_per_hour_usd=Decimal("0.0734"),
        quoted_at_iso="2026-05-27T04:00:06+00:00",
    )


@pytest.fixture
def ondemand_quote() -> OnDemandQuote:
    return OnDemandQuote(
        instance_type="m5.xlarge",
        region="us-east-1",
        price_per_hour_usd=Decimal("0.192"),
        sku="5G4TA8Z4MUKE6MJB",
    )


@pytest.fixture
def healthy_pricing(spot_quote, ondemand_quote) -> BatchPricing:
    return BatchPricing(
        region="us-east-1",
        instance_type="m5.xlarge",
        spot=spot_quote,
        ondemand=ondemand_quote,
    )


@pytest.fixture
def hello_workload() -> WorkloadSpec:
    return WorkloadSpec(
        code="print('Hello, agent.')",
        language="python",
        cost_cap_usd=1.0,
        timeout_sec=60,
        memory_mb=512,
    )


def _async_ctx(target):
    @asynccontextmanager
    async def _ctx():
        yield target

    return _ctx()


def make_pricing_client_mock(*, spot, ondemand, spot_error=None, ondemand_error=None):
    mock = MagicMock(spec=AWSPricingClient)
    if spot_error is not None:
        mock.get_spot_price = AsyncMock(side_effect=spot_error)
    else:
        mock.get_spot_price = AsyncMock(return_value=spot)
    if ondemand_error is not None:
        mock.get_ondemand_price = AsyncMock(side_effect=ondemand_error)
    else:
        mock.get_ondemand_price = AsyncMock(return_value=ondemand)
    return mock


def make_batch_substrate(
    *,
    region="us-east-1",
    result_bucket="darwin-batch-results-test",
    pricing_client=None,
    batch_client=None,
    s3_client=None,
    identity_signer=None,
    poll_interval_sec=0.001,
    run_timeout_sec=10,
):
    return BatchSubstrate(
        region=region,
        result_bucket=result_bucket,
        pricing_client=pricing_client,
        batch_client_factory=(lambda: _async_ctx(batch_client)) if batch_client else None,
        s3_client_factory=(lambda: _async_ctx(s3_client)) if s3_client else None,
        identity_signer=identity_signer,
        poll_interval_sec=poll_interval_sec,
        run_timeout_sec=run_timeout_sec,
    )


def make_successful_runner_response_bytes(*, request_id, workload_id):
    resp = {
        "schema": EVENT_SCHEMA_URI,
        "request_id": request_id,
        "workload_id": workload_id,
        "status": "ok",
        "stdout": "Hello, agent.\n",
        "stderr": "",
        "exit_code": 0,
        "started_at": 1700000000.0,
        "ended_at": 1700000000.4,
        "wall_time_sec": 0.4,
        "output_hash": "sha256:" + "a" * 64,
        "stderr_hash": "sha256:" + "b" * 64,
    }
    return json.dumps(resp).encode("utf-8")


def make_terminal_job_detail(*, job_id, job_queue, job_definition, status="SUCCEEDED", exit_code=0):
    return {
        "jobs": [
            {
                "jobId": job_id,
                "jobName": "darwin-batch-python-test",
                "jobQueue": job_queue,
                "jobDefinition": job_definition,
                "status": status,
                "container": {
                    "exitCode": exit_code,
                    "logStreamName": f"darwin-batch-runner-python-us-east-1/default/{job_id}",
                },
            }
        ]
    }


def make_s3_client_returning(payload):
    s3 = MagicMock()
    body_stream = MagicMock()
    body_stream.read = AsyncMock(return_value=payload)
    s3.get_object = AsyncMock(return_value={"Body": body_stream})
    return s3


def make_batch_client_succeeding(
    *,
    job_id="j-test",
    job_queue="darwin-batch-queue-us-east-1",
    job_definition="darwin-batch-runner-python-us-east-1",
    exit_code=0,
):
    bc = MagicMock()
    bc.submit_job = AsyncMock(return_value={"jobId": job_id})
    bc.describe_jobs = AsyncMock(
        return_value=make_terminal_job_detail(
            job_id=job_id, job_queue=job_queue, job_definition=job_definition, exit_code=exit_code
        )
    )
    return bc


class TestABCConformance:
    def test_substrate_id_includes_region(self):
        s = BatchSubstrate(region="us-east-1", result_bucket="x")
        assert s.substrate_id == "aws-batch-ec2-spot-v0-us-east-1"

    def test_substrate_version_is_semver(self):
        parts = SUBSTRATE_VERSION.split(".")
        assert len(parts) == 3
        assert all(p.isdigit() for p in parts)

    def test_evidence_schema_id_is_versioned(self):
        assert EVIDENCE_SCHEMA_ID == "darwin.cloud/evidence/aws-batch/v1"

    def test_subclass_of_substrate(self):
        s = BatchSubstrate(region="us-east-1", result_bucket="x")
        assert isinstance(s, Substrate)

    def test_evidence_schema_registered_at_import(self):
        assert EVIDENCE_SCHEMA_ID in EVIDENCE_REGISTRY.known_ids()

    def test_region_not_supported_rejected(self):
        with pytest.raises(BatchSubstrateError, match="not in supported set"):
            BatchSubstrate(region="us-west-99", result_bucket="x")

    def test_instance_type_not_supported_rejected(self):
        with pytest.raises(BatchSubstrateError, match="instance_type"):
            BatchSubstrate(region="us-east-1", result_bucket="x", instance_type="r5.16xlarge")


class TestPricing:
    def test_cost_for_one_minute_minimum(self, healthy_pricing):
        assert healthy_pricing.cost_for(wall_time_sec=10) == Decimal("0.00122333")

    def test_cost_for_one_hour(self, healthy_pricing):
        assert healthy_pricing.cost_for(wall_time_sec=3600) == Decimal("0.07340000")

    def test_savings_pct_against_ondemand(self, healthy_pricing):
        assert healthy_pricing.savings_pct == Decimal("61.77")

    def test_is_available_true_when_both_quotes_present(self, healthy_pricing):
        assert healthy_pricing.is_available


class TestPreflight:
    def test_unsupported_language_rejected(self, healthy_pricing):
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            )
        )
        with pytest.raises(PreflightRejected, match="ruby"):
            s.preflight(WorkloadSpec(code="puts 1", language="ruby", cost_cap_usd=1.0))

    def test_oversize_code_rejected(self, healthy_pricing):
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            )
        )
        with pytest.raises(PreflightRejected, match="code too large"):
            s.preflight(WorkloadSpec(code="x" * (MAX_CODE_BYTES + 1), cost_cap_usd=1.0))

    def test_memory_below_minimum_rejected(self, healthy_pricing):
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            )
        )
        with pytest.raises(PreflightRejected, match="memory_mb"):
            s.preflight(WorkloadSpec(code="print(1)", memory_mb=128, cost_cap_usd=1.0))

    def test_memory_above_maximum_rejected(self, healthy_pricing):
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            )
        )
        with pytest.raises(PreflightRejected, match="memory_mb"):
            s.preflight(
                WorkloadSpec(code="print(1)", memory_mb=MAX_MEMORY_MB + 1, cost_cap_usd=1.0)
            )

    def test_cost_cap_rejection(self, healthy_pricing):
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            )
        )
        with pytest.raises(PreflightRejected, match="exceeds cap"):
            s.preflight(
                WorkloadSpec(code="print(1)", memory_mb=512, timeout_sec=3600, cost_cap_usd=0.01)
            )

    def test_happy_path_returns_cost_estimate(self, healthy_pricing, hello_workload):
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            )
        )
        est = s.preflight(hello_workload)
        assert est.cost_usd_max > 0
        assert est.cost_breakdown["spot_price_per_hour_usd"] == 0.0734
        assert est.cost_breakdown["ondemand_price_per_hour_usd"] == 0.192


class TestCredentialProvider:
    def test_env_var_resolution(self, monkeypatch):
        monkeypatch.setenv("DARWIN_AWS_ACCESS_KEY_ID", "AKIA-test")
        monkeypatch.setenv("DARWIN_AWS_SECRET_ACCESS_KEY", "secret")
        creds = AwsCredentialProvider().resolve()
        assert creds.access_key_id == "AKIA-test"

    def test_session_kwargs_excludes_none(self):
        creds = AwsCredentials(access_key_id="k", secret_access_key="s")
        kw = creds.to_session_kwargs()
        assert "aws_access_key_id" in kw
        assert "aws_session_token" not in kw


class TestRunHappyPath:
    def test_run_returns_run_result(self, healthy_pricing, hello_workload):
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            ),
            batch_client=make_batch_client_succeeding(job_id="j-1"),
            s3_client=make_s3_client_returning(
                make_successful_runner_response_bytes(request_id="any", workload_id="any")
            ),
        )
        result = s.run(hello_workload)
        assert isinstance(result, RunResult)
        assert result.substrate_id == "aws-batch-ec2-spot-v0-us-east-1"
        assert result.evidence["job_id"] == "j-1"
        assert result.evidence["container_status"] == "ok"
        assert result.cost_usd > 0


class TestFailureModes:
    def test_submit_job_failure_raises_unreachable(self, healthy_pricing, hello_workload):
        bc = MagicMock()
        bc.submit_job = AsyncMock(side_effect=RuntimeError("network broke"))
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            ),
            batch_client=bc,
        )
        with pytest.raises(BatchUnreachable, match="submit_job failed"):
            s.run(hello_workload)

    def test_failed_state_with_no_log_stream_raises(self, healthy_pricing, hello_workload):
        bc = MagicMock()
        bc.submit_job = AsyncMock(return_value={"jobId": "j-3"})
        bc.describe_jobs = AsyncMock(
            return_value={"jobs": [{"jobId": "j-3", "status": "FAILED", "container": {}}]}
        )
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            ),
            batch_client=bc,
        )
        with pytest.raises(BatchJobFailed, match="FAILED with no log stream"):
            s.run(hello_workload)


class TestIdentitySignerWiring:
    def test_explicit_signer_override_used(self):
        signer = OperatorFallbackSigner()
        s = BatchSubstrate(region="us-east-1", result_bucket="x", identity_signer=signer)
        assert s.identity_signer() is signer


class TestEndToEnd:
    def test_run_result_to_attestation_dict_and_verify(self, healthy_pricing, hello_workload):
        signer = OperatorFallbackSigner()
        s = make_batch_substrate(
            pricing_client=make_pricing_client_mock(
                spot=healthy_pricing.spot, ondemand=healthy_pricing.ondemand
            ),
            batch_client=make_batch_client_succeeding(job_id="j-e2e"),
            s3_client=make_s3_client_returning(
                make_successful_runner_response_bytes(request_id="any", workload_id="any")
            ),
            identity_signer=signer,
        )
        result = s.run(hello_workload)
        result = dataclass_replace(result, issued_at=iso8601_now())
        identity = sign_identity(result=result, signer=signer)
        identity_payload = build_identity_payload(
            substrate_id=result.substrate_id,
            substrate_version=result.substrate_version,
            workload_spec_hash=result.workload_spec_hash,
            output_hash=result.output_hash,
            evidence_schema_id=result.evidence_schema_id,
            issued_at=result.issued_at,
        )
        attestation = build_attestation_dict(
            attestation_id="att_test_e2e", result=result, identity=identity
        )
        from darwin.agenticcloud.hashing import canonical_json as _canonical_json

        payload_bytes = _canonical_json(identity_payload)
        assert verify_signature(
            payload_bytes,
            identity.identity_signature,
            signer.public_key_b64,
        )
        sub = attestation["execution_result"]["substrate"]
        assert sub["id"] == "aws-batch-ec2-spot-v0-us-east-1"
        assert sub["evidence"]["job_id"] == "j-e2e"
