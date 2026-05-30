"""
darwin.agenticcloud.substrate.aws_batch
========================================

COST MODEL NOTE: this adapter's ``preflight()`` returns the WHOLESALE
cost — what Darwin pays AWS for the job. NOT the price Darwin charges
the end customer. Customer-facing markup (per Phase 6 hosted tier) is
applied in the billing layer, never in the substrate. This separation
lets the router (Phase 2) ``pick_by_cost()`` on real wholesale costs
while the billing layer (Phase 6) handles tiered pricing, marketplace
cuts, and per-tenant rate cards independently.

AWS Batch (EC2 Spot) substrate adapter for Phase 2 v3.0.0.

Submits jobs to a pre-deployed Batch compute environment + job queue
+ job definition in the target region. The runner is deployed via
``infra/aws_runner/batch_deploy.py`` — this adapter only submits
jobs against it, never creates or destroys infrastructure.

Architecture::

    BatchSubstrate                              (this file, client side)
        |
        |  aiobotocore.batch.submit_job(
        |      jobName, jobQueue, jobDefinition,
        |      containerOverrides={environment=[
        |          {"name": "DARWIN_BATCH_EVENT_B64", "value": ...}
        |      ]}
        |  )
        v
    darwin-batch-runner-{lang}-{region}          (Dockerfile + batch_runner.py)
        |
        |  exec() workload in container, write response to S3
        v
    Substrate polls describe_jobs() until SUCCEEDED/FAILED
        |
        |  Substrate reads result JSON from S3
        v
    BatchRunnerResponse -> evidence dict -> RunResult

Substrate id format: ``aws-batch-ec2-spot-v0-{region}`` (e.g.
``aws-batch-ec2-spot-v0-us-east-1``).

Evidence schema URI: ``darwin.cloud/evidence/aws-batch/v1``. Required
fields:

- job_id                AWS Batch job ID
- job_queue             Job queue ARN
- job_definition        Job definition ARN (versioned)
- region                AWS region
- instance_type         EC2 instance type (e.g. m5.xlarge)
- availability_zone     AZ the workload ran in
- spot_price_per_hour   Live Spot price at submit time (USD)
- ondemand_price_per_hour  On-demand baseline at submit time (USD)
- savings_pct           Spot discount vs On-Demand (0-100)
- pricing_quoted_at     ISO timestamp from EC2 SpotPriceHistory
- pricing_source        "ec2:DescribeSpotPriceHistory" | "unavailable"
- log_group             CloudWatch log group
- log_stream            CloudWatch log stream
- container_status      "ok" | "error" | "timeout" | "oom" | "spot-reclaimed"
- exit_code             Container exit code (from describe_jobs)
- stdout_hash           sha256 of stdout (from runner response)
- stderr_hash           sha256 of stderr (from runner response)
- wall_time_sec         wall-clock seconds (from runner response)
- vcpus                 vCPUs requested
- memory_mb             memory MB requested

Identity signing: per spec, every substrate signs its own identity
declaration. ``identity_signer()`` resolves via
``resolve_identity_signer()`` which routes to ``RemoteClassKeySigner``
(hosted) or ``OperatorFallbackSigner`` (self-hosted).

Polling model: synchronous ``run()`` contract (per build decision Q2=A).
``submit_job`` → poll ``describe_jobs`` with exponential backoff until
job reaches SUCCEEDED, FAILED, or substrate timeout. Polling stays
inside ``run()`` so the agent gets a single call/single attestation.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from darwin.agenticcloud.hashing import content_hash
from darwin.agenticcloud.substrate.aws_batch_event import (
    DEFAULT_INSTANCE_TYPE,
    EVENT_SCHEMA_URI,
    SUPPORTED_INSTANCE_TYPES,
    SUPPORTED_LANGUAGES,
    BatchRunnerEvent,
    BatchRunnerEventError,
    BatchRunnerResponse,
)
from darwin.agenticcloud.substrate.aws_pricing import (
    AWSPricingClient,
    OnDemandQuote,
    PricingLookupError,
    SpotPriceQuote,
    compute_ec2_spot_cost,
    compute_savings_pct,
)
from darwin.agenticcloud.substrate.base import (
    EVIDENCE_REGISTRY,
    CostEstimate,
    EvidenceSchema,
    EvidenceSchemaError,
    PreflightRejected,
    RunResult,
    Substrate,
    SubstrateError,
    SubstrateExecutionError,
    SubstrateIdentitySigner,
)
from darwin.agenticcloud.substrate.identity import resolve_identity_signer
from darwin.agenticcloud.types import WorkloadSpec

# ============================================================================
# Constants
# ============================================================================

#: Substrate-id prefix. Full id is `aws-batch-ec2-spot-v0-{region}`.
SUBSTRATE_ID_PREFIX: str = "aws-batch-ec2-spot-v0"

#: Adapter version. Bumped when behavior changes in a way that would
#: change produced evidence shape or content.
SUBSTRATE_VERSION: str = "0.1.0"

#: Evidence schema URI for AWS Batch.
EVIDENCE_SCHEMA_ID: str = "darwin.cloud/evidence/aws-batch/v1"

#: Required evidence fields. EVIDENCE_REGISTRY.validate() enforces these.
EVIDENCE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "job_id",
        "job_queue",
        "job_definition",
        "region",
        "instance_type",
        "availability_zone",
        "spot_price_per_hour",
        "ondemand_price_per_hour",
        "savings_pct",
        "pricing_quoted_at",
        "pricing_source",
        "log_group",
        "log_stream",
        "container_status",
        "exit_code",
        "stdout_hash",
        "stderr_hash",
        "wall_time_sec",
        "vcpus",
        "memory_mb",
    }
)

#: Job-name template for darwin-batch-runner submissions. Each job
#: name is unique per submission (request_id suffix).
JOB_NAME_TEMPLATE: str = "darwin-batch-{language}-{request_id}"

#: Job-definition name pattern that batch_deploy.py creates.
JOB_DEFINITION_NAME_TEMPLATE: str = "darwin-batch-runner-{language}-{region}"

#: Job-queue name pattern that batch_deploy.py creates.
JOB_QUEUE_NAME_TEMPLATE: str = "darwin-batch-queue-{region}"

#: AWS regions Darwin supports for v3.0.0. v3.0.0 ships with us-east-1
#: only; additional regions follow in v3.1.x patches.
SUPPORTED_REGIONS: frozenset[str] = frozenset({"us-east-1"})

#: Region → Pricing API location name mapping. The Pricing API uses
#: human-readable region names, not the region codes that EC2/Batch use.
REGION_TO_LOCATION_NAME: dict[str, str] = {
    "us-east-1": "US East (N. Virginia)",
}

#: Default poll interval (seconds) between describe_jobs calls.
POLL_INTERVAL_SEC_DEFAULT: float = 5.0

#: Maximum poll interval (seconds). Exponential backoff caps here.
POLL_INTERVAL_SEC_MAX: float = 30.0

#: Default overall timeout for run() in seconds. v3.0.0 conservative
#: 30-minute ceiling on synchronous batch jobs. Future versions add
#: async-completion for longer jobs.
RUN_TIMEOUT_SEC_DEFAULT: int = 30 * 60

#: Job states returned by describe_jobs. SUCCEEDED + FAILED are terminal.
TERMINAL_JOB_STATES: frozenset[str] = frozenset({"SUCCEEDED", "FAILED"})
NONTERMINAL_JOB_STATES: frozenset[str] = frozenset(
    {"SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING"}
)


# ============================================================================
# Errors
# ============================================================================


class BatchSubstrateError(SubstrateError):
    """Base class for BatchSubstrate errors."""


class BatchUnreachable(BatchSubstrateError):
    """The Batch service couldn't be reached (network, throttling, IAM)."""


class BatchSubmissionFailed(BatchSubstrateError):
    """submit_job failed before the job was accepted."""


class BatchJobFailed(BatchSubstrateError):
    """The job ran but ended in FAILED state with no usable response."""


class BatchResultUnavailable(BatchSubstrateError):
    """Job succeeded but the result JSON could not be read from S3."""


# ============================================================================
# Evidence schema registration
# ============================================================================


def _validate_evidence(evidence: Any) -> None:
    """Substrate-specific evidence validator."""
    cs = evidence.get("container_status")
    if cs not in {"ok", "error", "timeout", "oom", "spot-reclaimed"}:
        raise EvidenceSchemaError(f"aws-batch evidence.container_status invalid: {cs!r}")

    region = evidence.get("region")
    if region not in SUPPORTED_REGIONS:
        raise EvidenceSchemaError(f"aws-batch evidence.region not supported: {region!r}")

    it = evidence.get("instance_type")
    if it not in SUPPORTED_INSTANCE_TYPES:
        raise EvidenceSchemaError(f"aws-batch evidence.instance_type not supported: {it!r}")

    ps = evidence.get("pricing_source")
    if ps not in {"ec2:DescribeSpotPriceHistory", "unavailable"}:
        raise EvidenceSchemaError(f"aws-batch evidence.pricing_source invalid: {ps!r}")

    wt = evidence.get("wall_time_sec")
    if not isinstance(wt, int | float) or wt < 0:
        raise EvidenceSchemaError(
            f"aws-batch evidence.wall_time_sec must be a non-negative number, got {wt!r}"
        )

    vcpus = evidence.get("vcpus")
    if not isinstance(vcpus, int) or vcpus < 1:
        raise EvidenceSchemaError(f"aws-batch evidence.vcpus must be a positive int, got {vcpus!r}")

    mem = evidence.get("memory_mb")
    if not isinstance(mem, int) or mem < 1:
        raise EvidenceSchemaError(
            f"aws-batch evidence.memory_mb must be a positive int, got {mem!r}"
        )


EVIDENCE_REGISTRY.register(
    EvidenceSchema(
        schema_id=EVIDENCE_SCHEMA_ID,
        required_fields=EVIDENCE_REQUIRED_FIELDS,
        validator=_validate_evidence,
    )
)


# ============================================================================
# Pricing (thin wrapper over AWSPricingClient)
# ============================================================================


@dataclass
class BatchPricing:
    """Live pricing snapshot for one (region, instance_type) at one moment.

    Captured at submit_job time so the attestation records the exact
    Spot price the job was quoted against, not an averaged or stale
    number.
    """

    region: str
    instance_type: str
    spot: SpotPriceQuote | None
    ondemand: OnDemandQuote | None
    source: str = "ec2:DescribeSpotPriceHistory"

    @property
    def is_available(self) -> bool:
        return self.spot is not None and self.ondemand is not None

    def cost_for(self, *, wall_time_sec: float) -> Decimal | None:
        """Compute wholesale cost for the workload's actual wall time.

        Returns None if pricing was unavailable (B2 soft-fail policy);
        the substrate then records cost_usd=null on the attestation.
        """
        if self.spot is None:
            return None
        return compute_ec2_spot_cost(
            spot_price_per_hour=self.spot.price_per_hour_usd,
            wall_time_sec=wall_time_sec,
        )

    @property
    def savings_pct(self) -> Decimal | None:
        if self.spot is None or self.ondemand is None:
            return None
        return compute_savings_pct(
            spot_price=self.spot.price_per_hour_usd,
            ondemand_price=self.ondemand.price_per_hour_usd,
        )


# ============================================================================
# AWS credentials (reuses pattern from aws_lambda.py)
# ============================================================================


@dataclass
class AwsCredentials:
    """Resolved AWS credentials passed to aiobotocore."""

    access_key_id: str | None = None
    secret_access_key: str | None = None
    session_token: str | None = None

    def to_session_kwargs(self) -> dict[str, str]:
        kwargs: dict[str, str] = {}
        if self.access_key_id:
            kwargs["aws_access_key_id"] = self.access_key_id
        if self.secret_access_key:
            kwargs["aws_secret_access_key"] = self.secret_access_key
        if self.session_token:
            kwargs["aws_session_token"] = self.session_token
        return kwargs


class AwsCredentialProvider:
    """Same shape as aws_lambda.AwsCredentialProvider.

    v3.0.0 reads ``DARWIN_AWS_*`` env vars and falls back to the AWS
    default credential chain. Phase 6 swaps in per-tenant credentials.
    """

    def resolve(self) -> AwsCredentials:
        import os

        return AwsCredentials(
            access_key_id=os.environ.get("DARWIN_AWS_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("DARWIN_AWS_SECRET_ACCESS_KEY"),
            session_token=os.environ.get("DARWIN_AWS_SESSION_TOKEN"),
        )


# ============================================================================
# BatchSubstrate
# ============================================================================


class BatchSubstrate(Substrate):
    """AWS Batch substrate. One instance per region.

    Submits jobs to a pre-deployed darwin-batch-runner-{language}-{region}
    job definition on a darwin-batch-queue-{region} job queue. The
    Dockerfile + batch_runner.py handle execution; this adapter handles
    submission, polling, response parsing, evidence shaping, and live
    cost calculation against ``ec2:DescribeSpotPriceHistory``.

    Tests inject ``batch_client_factory``, ``s3_client_factory``, and
    ``pricing_client`` to avoid real AWS calls. Production passes
    nothing.
    """

    def __init__(
        self,
        region: str,
        *,
        result_bucket: str | None = None,
        credentials_provider: AwsCredentialProvider | None = None,
        pricing_client: AWSPricingClient | None = None,
        identity_signer: SubstrateIdentitySigner | None = None,
        batch_client_factory: Any = None,
        s3_client_factory: Any = None,
        poll_interval_sec: float = POLL_INTERVAL_SEC_DEFAULT,
        run_timeout_sec: int = RUN_TIMEOUT_SEC_DEFAULT,
        instance_type: str = DEFAULT_INSTANCE_TYPE,
    ) -> None:
        if region not in SUPPORTED_REGIONS:
            raise BatchSubstrateError(
                f"Region {region!r} not in supported set: {sorted(SUPPORTED_REGIONS)}"
            )
        if instance_type not in SUPPORTED_INSTANCE_TYPES:
            raise BatchSubstrateError(
                f"instance_type {instance_type!r} not in supported set: "
                f"{sorted(SUPPORTED_INSTANCE_TYPES)}"
            )

        self._region = region
        self._result_bucket = result_bucket
        self._credentials = credentials_provider or AwsCredentialProvider()
        self._pricing_client = pricing_client or AWSPricingClient()
        self._explicit_signer = identity_signer
        self._batch_client_factory = batch_client_factory
        self._s3_client_factory = s3_client_factory
        self._poll_interval_sec = poll_interval_sec
        self._run_timeout_sec = run_timeout_sec
        self._instance_type = instance_type

    # --- Substrate ABC: metadata --------------------------------------------

    @property
    def substrate_id(self) -> str:
        return f"{SUBSTRATE_ID_PREFIX}-{self._region}"

    @property
    def substrate_version(self) -> str:
        return SUBSTRATE_VERSION

    @property
    def evidence_schema_id(self) -> str:
        return EVIDENCE_SCHEMA_ID

    @property
    def region(self) -> str:
        return self._region

    @property
    def instance_type(self) -> str:
        return self._instance_type

    # --- Substrate ABC: behavior --------------------------------------------

    def preflight(self, workload: WorkloadSpec) -> CostEstimate:
        """Estimate maximum cost for a Batch job of this workload.

        EC2 Spot bills per second with a 60s minimum. The upper bound is:

            cost_max = spot_price_per_hour * (timeout_sec / 3600)

        If the live Pricing API is unreachable we soft-fail (B2 policy):
        the estimate returns ``cost_usd_max=None`` and the substrate
        will record ``pricing_source=unavailable`` on the attestation.
        """
        if workload.language not in SUPPORTED_LANGUAGES:
            raise PreflightRejected(
                f"language {workload.language!r} not supported by "
                f"aws-batch. Supported: {sorted(SUPPORTED_LANGUAGES)}"
            )

        from darwin.agenticcloud.substrate.aws_batch_event import (
            MAX_CODE_BYTES,
            MAX_MEMORY_MB,
            MIN_MEMORY_MB,
        )

        code_bytes = len(workload.code.encode("utf-8"))
        if code_bytes > MAX_CODE_BYTES:
            raise PreflightRejected(
                f"workload code too large: {code_bytes} bytes (max {MAX_CODE_BYTES})"
            )
        if workload.memory_mb < MIN_MEMORY_MB or workload.memory_mb > MAX_MEMORY_MB:
            raise PreflightRejected(
                f"memory_mb {workload.memory_mb} out of range [{MIN_MEMORY_MB}, {MAX_MEMORY_MB}]"
            )

        pricing = asyncio.run(self._fetch_pricing_snapshot())

        if not pricing.is_available:
            return CostEstimate(
                cost_usd_max=float("inf"),
                cost_breakdown={
                    "pricing_source": "unavailable",  # type: ignore[dict-item]
                    "note": (  # type: ignore[dict-item]
                        "Pricing API unreachable; substrate will record "
                        "pricing_source=unavailable on attestation."
                    ),
                },
                notes=f"Batch preflight for {self.substrate_id} (degraded)",
            )

        # Upper bound: workload runs the full timeout.
        spot_quote = pricing.spot
        assert spot_quote is not None  # mypy
        cost_max_decimal = compute_ec2_spot_cost(
            spot_price_per_hour=spot_quote.price_per_hour_usd,
            wall_time_sec=workload.timeout_sec,
        )
        cost_max = float(cost_max_decimal)

        if cost_max > workload.cost_cap_usd:
            raise PreflightRejected(
                f"projected max cost ${cost_max:.8f} exceeds cap "
                f"${workload.cost_cap_usd:.8f} (memory={workload.memory_mb}MB, "
                f"timeout={workload.timeout_sec}s, region={self._region}, "
                f"instance_type={self._instance_type})"
            )

        ondemand_quote = pricing.ondemand
        assert ondemand_quote is not None  # mypy
        return CostEstimate(
            cost_usd_max=cost_max,
            cost_breakdown={
                "spot_price_per_hour_usd": float(spot_quote.price_per_hour_usd),
                "ondemand_price_per_hour_usd": float(ondemand_quote.price_per_hour_usd),
                "savings_pct": float(pricing.savings_pct or Decimal("0")),
                "pricing_source": pricing.source,  # type: ignore[dict-item]
                "instance_type": self._instance_type,  # type: ignore[dict-item]
                "availability_zone": spot_quote.availability_zone,  # type: ignore[dict-item]
                "pricing_quoted_at": spot_quote.quoted_at_iso,  # type: ignore[dict-item]
            },
            notes=f"Batch preflight for {self.substrate_id}",
        )

    def run(self, workload: WorkloadSpec) -> RunResult:
        """Synchronous entry point — wraps ``_run_async()``."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._run_async(workload))
        raise BatchSubstrateError(
            "BatchSubstrate.run() must not be called from inside an "
            "event loop. Use _run_async() directly instead."
        )

    async def _run_async(self, workload: WorkloadSpec) -> RunResult:
        """Async core: submit_job → poll → fetch S3 result → assemble."""
        workload_id = f"wl-{uuid.uuid4().hex[:12]}"
        request_id = str(uuid.uuid4())

        event = BatchRunnerEvent(
            schema=EVENT_SCHEMA_URI,
            request_id=request_id,
            workload_id=workload_id,
            language=workload.language,
            code=workload.code,
            timeout_sec=workload.timeout_sec,
            memory_mb=workload.memory_mb,
            vcpus=min(workload.memory_mb // 4096 or 1, 4),
            instance_type=self._instance_type,
            inputs=workload.inputs,
        )
        event_b64 = base64.b64encode(json.dumps(event.to_dict()).encode("utf-8")).decode("ascii")

        pricing = await self._fetch_pricing_snapshot()

        job_name = JOB_NAME_TEMPLATE.format(
            language=workload.language,
            request_id=request_id.replace("-", "")[:24],
        )
        job_queue = JOB_QUEUE_NAME_TEMPLATE.format(region=self._region)
        job_definition = JOB_DEFINITION_NAME_TEMPLATE.format(
            language=workload.language,
            region=self._region,
        )

        async with self._batch_ctx() as batch:
            try:
                submit_resp = await batch.submit_job(
                    jobName=job_name,
                    jobQueue=job_queue,
                    jobDefinition=job_definition,
                    containerOverrides={
                        "vcpus": event.vcpus,
                        "memory": event.memory_mb,
                        "environment": [
                            {
                                "name": "DARWIN_BATCH_EVENT_B64",
                                "value": event_b64,
                            },
                            {
                                "name": "DARWIN_BATCH_RESULT_BUCKET",
                                "value": self._result_bucket or "",
                            },
                        ],
                    },
                )
            except Exception as exc:
                raise BatchUnreachable(f"submit_job failed for {job_definition}: {exc}") from exc

            job_id = submit_resp.get("jobId")
            if not job_id:
                raise BatchSubmissionFailed(f"submit_job did not return jobId: {submit_resp!r}")

            job_detail = await self._poll_until_terminal(batch, job_id)

        runner_resp = await self._fetch_runner_response(
            request_id=request_id,
        )

        cost_decimal = pricing.cost_for(wall_time_sec=runner_resp.wall_time_sec)
        cost_usd: float | None = float(cost_decimal) if cost_decimal is not None else None

        spot_quote = pricing.spot
        ondemand_quote = pricing.ondemand
        savings = pricing.savings_pct

        container_info = job_detail.get("container") or {}
        log_stream = container_info.get("logStreamName") or "(unavailable)"
        log_group = "/aws/batch/job"
        exit_code = container_info.get("exitCode")
        if exit_code is None:
            exit_code = runner_resp.exit_code

        evidence: dict[str, Any] = {
            "job_id": job_id,
            "job_queue": job_detail.get("jobQueue", job_queue),
            "job_definition": job_detail.get("jobDefinition", job_definition),
            "region": self._region,
            "instance_type": self._instance_type,
            "availability_zone": (spot_quote.availability_zone if spot_quote else "(unavailable)"),
            "spot_price_per_hour": (float(spot_quote.price_per_hour_usd) if spot_quote else 0.0),
            "ondemand_price_per_hour": (
                float(ondemand_quote.price_per_hour_usd) if ondemand_quote else 0.0
            ),
            "savings_pct": float(savings) if savings is not None else 0.0,
            "pricing_quoted_at": (spot_quote.quoted_at_iso if spot_quote else "(unavailable)"),
            "pricing_source": (pricing.source if pricing.is_available else "unavailable"),
            "log_group": log_group,
            "log_stream": log_stream,
            "container_status": runner_resp.status,
            "exit_code": exit_code,
            "stdout_hash": runner_resp.output_hash,
            "stderr_hash": runner_resp.stderr_hash,
            "wall_time_sec": runner_resp.wall_time_sec,
            "vcpus": event.vcpus,
            "memory_mb": event.memory_mb,
        }

        partial = {
            **evidence,
            "_partial_run_result": {
                "substrate_id": self.substrate_id,
                "substrate_version": SUBSTRATE_VERSION,
                "workload_spec_hash": content_hash(asdict(workload)),
                "stdout": runner_resp.stdout,
                "stderr": runner_resp.stderr,
                "output_hash": runner_resp.output_hash,
                "cost_usd": cost_usd,
                "evidence_schema_id": EVIDENCE_SCHEMA_ID,
                "evidence": evidence,
                "extensions": {},
                "tee_required": False,
            },
        }

        if runner_resp.status != "ok":
            raise SubstrateExecutionError(
                f"aws-batch workload status={runner_resp.status}: "
                f"{runner_resp.error or 'see stderr'}",
                partial_evidence=partial,
            )

        return RunResult(
            substrate_id=self.substrate_id,
            substrate_version=SUBSTRATE_VERSION,
            workload_spec_hash=content_hash(asdict(workload)),
            stdout=runner_resp.stdout,
            stderr=runner_resp.stderr,
            output_hash=runner_resp.output_hash,
            cost_usd=cost_usd if cost_usd is not None else 0.0,
            evidence_schema_id=EVIDENCE_SCHEMA_ID,
            evidence=evidence,
            extensions={},
            tee_required=False,
            issued_at="",
        )

    def identity_signer(self) -> SubstrateIdentitySigner:
        if self._explicit_signer is not None:
            return self._explicit_signer
        return resolve_identity_signer(self.substrate_id)

    def cleanup(self) -> None:
        """No-op: Batch jobs are ephemeral by submission. No leases to
        close, no compute environments to tear down. Compute env stays
        live across submissions."""
        return None

    # --- Internal: pricing snapshot -----------------------------------------

    async def _fetch_pricing_snapshot(self) -> BatchPricing:
        """Capture live (spot, ondemand) at this moment.

        B2 soft-fail: any pricing-API error returns a snapshot with
        both quotes set to None. The substrate then records
        ``pricing_source=unavailable`` and ``cost_usd=null`` on the
        attestation, preserving audit honesty.
        """
        spot_quote: SpotPriceQuote | None = None
        ondemand_quote: OnDemandQuote | None = None
        try:
            spot_quote = await self._pricing_client.get_spot_price(
                region=self._region,
                instance_type=self._instance_type,
            )
        except PricingLookupError:
            spot_quote = None

        try:
            ondemand_quote = await self._pricing_client.get_ondemand_price(
                region=self._region,
                instance_type=self._instance_type,
                location_name=REGION_TO_LOCATION_NAME[self._region],
            )
        except PricingLookupError:
            ondemand_quote = None

        return BatchPricing(
            region=self._region,
            instance_type=self._instance_type,
            spot=spot_quote,
            ondemand=ondemand_quote,
        )

    # --- Internal: polling --------------------------------------------------

    async def _poll_until_terminal(self, batch_client: Any, job_id: str) -> dict[str, Any]:
        """Poll describe_jobs until job reaches a terminal state.

        Exponential backoff: poll_interval doubles on each iteration up
        to POLL_INTERVAL_SEC_MAX. Overall run timeout enforced.
        """
        elapsed = 0.0
        interval = self._poll_interval_sec
        while elapsed < self._run_timeout_sec:
            try:
                resp = await batch_client.describe_jobs(jobs=[job_id])
            except Exception as exc:
                raise BatchUnreachable(f"describe_jobs failed for {job_id}: {exc}") from exc

            jobs = resp.get("jobs") or []
            if not jobs:
                raise BatchSubmissionFailed(f"describe_jobs returned no entry for {job_id}")
            job = jobs[0]
            status = job.get("status")
            if status in TERMINAL_JOB_STATES:
                if status == "FAILED" and not (job.get("container") or {}).get("logStreamName"):
                    raise BatchJobFailed(
                        f"job {job_id} FAILED with no log stream: "
                        f"reason={job.get('statusReason')!r}"
                    )
                return job  # type: ignore[no-any-return]

            await asyncio.sleep(interval)
            elapsed += interval
            interval = min(interval * 1.5, POLL_INTERVAL_SEC_MAX)

        raise BatchJobFailed(
            f"job {job_id} did not reach terminal state within {self._run_timeout_sec}s"
        )

    # --- Internal: S3 result fetch ------------------------------------------

    async def _fetch_runner_response(self, *, request_id: str) -> BatchRunnerResponse:
        """Read the runner's JSON response from S3 and parse it.

        If ``result_bucket`` is unset (developer-mode operation), the
        runner only printed to stdout. Without an S3 result we cannot
        reconstruct the response, so raise.
        """
        if not self._result_bucket:
            raise BatchResultUnavailable(
                "BatchSubstrate.result_bucket is unset; cannot fetch "
                "runner response. Pass result_bucket=... when instantiating."
            )

        async with self._s3_ctx() as s3:
            try:
                obj = await s3.get_object(
                    Bucket=self._result_bucket,
                    Key=f"{request_id}.json",
                )
                body = await obj["Body"].read()
            except Exception as exc:
                raise BatchResultUnavailable(
                    f"failed to read result for {request_id}: {exc}"
                ) from exc

        try:
            data = json.loads(body.decode("utf-8"))
            return BatchRunnerResponse.from_dict(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BatchResultUnavailable(f"result for {request_id} not valid JSON: {exc}") from exc
        except BatchRunnerEventError as exc:
            raise BatchResultUnavailable(f"result for {request_id} malformed: {exc}") from exc

    # --- Internal: client context managers (factory pattern) ----------------

    def _batch_ctx(self):
        if self._batch_client_factory is not None:
            return self._batch_client_factory()
        return self._make_real_batch_ctx()

    def _s3_ctx(self):
        if self._s3_client_factory is not None:
            return self._s3_client_factory()
        return self._make_real_s3_ctx()

    def _make_real_batch_ctx(self):
        from contextlib import asynccontextmanager

        from aiobotocore.session import get_session

        creds = self._credentials.resolve()
        kwargs = creds.to_session_kwargs()

        @asynccontextmanager
        async def _ctx():
            session = get_session()
            async with session.create_client(
                "batch",
                region_name=self._region,
                **kwargs,
            ) as client:
                yield client

        return _ctx()

    def _make_real_s3_ctx(self):
        from contextlib import asynccontextmanager

        from aiobotocore.session import get_session

        creds = self._credentials.resolve()
        kwargs = creds.to_session_kwargs()

        @asynccontextmanager
        async def _ctx():
            session = get_session()
            async with session.create_client(
                "s3",
                region_name=self._region,
                **kwargs,
            ) as client:
                yield client

        return _ctx()


__all__ = [
    "EVIDENCE_REQUIRED_FIELDS",
    "EVIDENCE_SCHEMA_ID",
    "JOB_DEFINITION_NAME_TEMPLATE",
    "JOB_NAME_TEMPLATE",
    "JOB_QUEUE_NAME_TEMPLATE",
    "POLL_INTERVAL_SEC_DEFAULT",
    "POLL_INTERVAL_SEC_MAX",
    "REGION_TO_LOCATION_NAME",
    "RUN_TIMEOUT_SEC_DEFAULT",
    "SUBSTRATE_ID_PREFIX",
    "SUBSTRATE_VERSION",
    "SUPPORTED_REGIONS",
    "TERMINAL_JOB_STATES",
    "AwsCredentialProvider",
    "AwsCredentials",
    "BatchJobFailed",
    "BatchPricing",
    "BatchResultUnavailable",
    "BatchSubmissionFailed",
    "BatchSubstrate",
    "BatchSubstrateError",
    "BatchUnreachable",
]
