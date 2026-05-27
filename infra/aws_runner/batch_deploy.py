#!/usr/bin/env python3
"""
darwin-batch-runner AWS deployment orchestrator.

Idempotent boto3 script that provisions the darwin-batch-runner Batch
compute environment, job queue, and job definition in us-east-1. Does
the same work CDK or Terraform would do, but with zero new tooling deps.

Resources created (us-east-1, v3.0.0 ships single-region):
  - ECR repo:               darwin-batch-runner-python
  - S3 bucket:              darwin-batch-results-{account_id}-us-east-1
  - IAM role:               darwin-batch-runner-execution-role
                            attached: AmazonECSTaskExecutionRolePolicy, S3 write to results bucket
  - IAM role:               darwin-batch-runner-instance-role
                            attached: AmazonEC2ContainerServiceforEC2Role
  - IAM instance profile:   darwin-batch-runner-instance-profile
  - IAM role:               darwin-batch-service-role
                            attached: AWSBatchServiceRole
  - EC2 Spot Fleet role:    darwin-batch-spot-fleet-role
                            attached: AmazonEC2SpotFleetTaggingRole
  - Batch compute env:      darwin-batch-ce-ec2-spot-us-east-1
                            type=EC2 Spot, instance=m5.xlarge, minvCpus=0
  - Batch job queue:        darwin-batch-queue-us-east-1
  - Batch job definition:   darwin-batch-runner-python-us-east-1

Usage:
  AWS_PROFILE=darwin python infra/aws_runner/batch_deploy.py
  AWS_PROFILE=darwin python infra/aws_runner/batch_deploy.py --dry-run

Idempotency: every resource is checked-then-created. Re-running this
script after a partial failure is safe.

Build context: this script reads ``batch_runner.py`` and
``Dockerfile.batch-runner-python`` from the same directory. The Docker
daemon must be running locally to build the image.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# ============================================================================
# Constants
# ============================================================================

DEFAULT_REGION = "us-east-1"

# IAM role names
EXECUTION_ROLE_NAME = "darwin-batch-runner-execution-role"
INSTANCE_ROLE_NAME = "darwin-batch-runner-instance-role"
INSTANCE_PROFILE_NAME = "darwin-batch-runner-instance-profile"
SERVICE_ROLE_NAME = "darwin-batch-service-role"
SPOT_FLEET_ROLE_NAME = "darwin-batch-spot-fleet-role"

# ECR
ECR_REPO_PYTHON = "darwin-batch-runner-python"

# S3
RESULT_BUCKET_TEMPLATE = "darwin-batch-results-{account_id}-{region}"

# Batch
COMPUTE_ENVIRONMENT_NAME_TEMPLATE = "darwin-batch-ce-ec2-spot-{region}"
JOB_QUEUE_NAME_TEMPLATE = "darwin-batch-queue-{region}"
JOB_DEFINITION_NAME_TEMPLATE = "darwin-batch-runner-python-{region}"

# Compute environment config
DEFAULT_INSTANCE_TYPE = "m5.xlarge"
DEFAULT_MIN_VCPUS = 0
DEFAULT_MAX_VCPUS = 16
DEFAULT_DESIRED_VCPUS = 0
DEFAULT_BID_PERCENTAGE = 100  # willing to pay up to 100% of on-demand

# Trust policies
LAMBDA_TRUST_NEVER_USED = None  # not lambda
EC2_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}
ECS_TASK_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}
BATCH_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "batch.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}
SPOT_FLEET_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "spotfleet.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


# ============================================================================
# Logging helpers
# ============================================================================


def log(msg: str) -> None:
    sys.stdout.write(f"[deploy] {msg}\n")
    sys.stdout.flush()


def fail(msg: str) -> None:
    sys.stderr.write(f"[deploy] FATAL: {msg}\n")
    sys.exit(1)


# ============================================================================
# IAM
# ============================================================================


def ensure_role(
    iam,
    role_name: str,
    trust_policy: dict,
    description: str,
    *,
    dry_run: bool = False,
) -> str:
    """Idempotent IAM role create. Returns the role ARN."""
    try:
        resp = iam.get_role(RoleName=role_name)
        arn = resp["Role"]["Arn"]
        log(f"IAM role exists:    {role_name} -> {arn}")
        return arn
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise

    if dry_run:
        log(f"IAM role DRY-RUN create: {role_name}")
        return f"arn:aws:iam::DRY:role/{role_name}"

    resp = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description=description,
    )
    arn = resp["Role"]["Arn"]
    log(f"IAM role created:   {role_name} -> {arn}")
    return arn


def attach_managed_policy(iam, role_name: str, policy_arn: str, *, dry_run: bool = False) -> None:
    try:
        attached = iam.list_attached_role_policies(RoleName=role_name)
        already = {p["PolicyArn"] for p in attached["AttachedPolicies"]}
    except ClientError:
        already = set()

    if policy_arn in already:
        log(f"IAM policy attached: {role_name} <- {policy_arn}")
        return

    if dry_run:
        log(f"IAM policy DRY-RUN attach: {role_name} <- {policy_arn}")
        return

    iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    log(f"IAM policy attach:   {role_name} <- {policy_arn}")


def ensure_instance_profile(
    iam, profile_name: str, role_name: str, *, dry_run: bool = False
) -> str:
    """Idempotent instance profile + role association."""
    try:
        resp = iam.get_instance_profile(InstanceProfileName=profile_name)
        arn = resp["InstanceProfile"]["Arn"]
        log(f"Instance profile exists: {profile_name}")
        # Ensure role is attached
        roles = [r["RoleName"] for r in resp["InstanceProfile"]["Roles"]]
        if role_name not in roles:
            if dry_run:
                log(f"Instance profile DRY-RUN attach role: {role_name}")
            else:
                iam.add_role_to_instance_profile(
                    InstanceProfileName=profile_name, RoleName=role_name
                )
                log(f"Instance profile role added: {role_name}")
        return arn
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "NoSuchEntity":
            raise

    if dry_run:
        log(f"Instance profile DRY-RUN create: {profile_name}")
        return f"arn:aws:iam::DRY:instance-profile/{profile_name}"

    resp = iam.create_instance_profile(InstanceProfileName=profile_name)
    arn = resp["InstanceProfile"]["Arn"]
    iam.add_role_to_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    log(f"Instance profile created: {profile_name}")
    return arn


# ============================================================================
# ECR
# ============================================================================


def ensure_ecr_repo(ecr, repo_name: str, *, dry_run: bool = False) -> str:
    try:
        resp = ecr.describe_repositories(repositoryNames=[repo_name])
        uri = resp["repositories"][0]["repositoryUri"]
        log(f"ECR repo exists:    {repo_name} -> {uri}")
        return uri
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise

    if dry_run:
        log(f"ECR repo DRY-RUN create: {repo_name}")
        return f"DRY/{repo_name}"

    resp = ecr.create_repository(
        repositoryName=repo_name,
        imageScanningConfiguration={"scanOnPush": True},
    )
    uri = resp["repository"]["repositoryUri"]
    log(f"ECR repo created:   {repo_name} -> {uri}")
    return uri


def docker_login_to_ecr(ecr, region: str) -> None:
    resp = ecr.get_authorization_token()
    auth_data = resp["authorizationData"][0]
    token = base64.b64decode(auth_data["authorizationToken"]).decode("utf-8")
    username, password = token.split(":", 1)
    endpoint = auth_data["proxyEndpoint"]
    subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", endpoint],
        input=password.encode("utf-8"),
        check=True,
    )
    log(f"Docker logged in to ECR endpoint {endpoint}")


def build_and_push_image(
    *,
    dockerfile: Path,
    context: Path,
    repo_uri: str,
    tag: str,
    region: str,
    dry_run: bool = False,
) -> str:
    """Build the image from {dockerfile} in {context} and push to {repo_uri}:{tag}."""
    image_ref = f"{repo_uri}:{tag}"
    if dry_run:
        log(f"Image DRY-RUN build+push: {image_ref}")
        return image_ref

    log(f"Building image {image_ref} from {dockerfile.name}")
    subprocess.run(
        [
            "docker",
            "buildx",
            "build",
            "--platform",
            "linux/amd64",
            "-f",
            str(dockerfile),
            "-t",
            image_ref,
            "--load",
            str(context),
        ],
        check=True,
    )

    log(f"Pushing image {image_ref}")
    subprocess.run(["docker", "push", image_ref], check=True)
    log(f"Pushed image {image_ref}")
    return image_ref


# ============================================================================
# S3
# ============================================================================


def ensure_s3_bucket(s3, bucket_name: str, region: str, *, dry_run: bool = False) -> None:
    try:
        s3.head_bucket(Bucket=bucket_name)
        log(f"S3 bucket exists:   {bucket_name}")
        return
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            # 403 means the bucket exists but in another account.
            if exc.response["Error"]["Code"] == "403":
                fail(f"S3 bucket {bucket_name} exists in another account")
            raise

    if dry_run:
        log(f"S3 bucket DRY-RUN create: {bucket_name}")
        return

    kwargs = {"Bucket": bucket_name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    log(f"S3 bucket created:  {bucket_name}")


# ============================================================================
# Batch
# ============================================================================


def ensure_compute_environment(
    batch,
    name: str,
    *,
    spot_fleet_role_arn: str,
    instance_profile_arn: str,
    service_role_arn: str,
    region: str,
    subnets: list[str],
    security_groups: list[str],
    dry_run: bool = False,
) -> str:
    try:
        resp = batch.describe_compute_environments(computeEnvironments=[name])
        envs = resp.get("computeEnvironments") or []
        if envs:
            arn = envs[0]["computeEnvironmentArn"]
            log(f"Compute env exists: {name}")
            return arn
    except ClientError:
        pass

    if dry_run:
        log(f"Compute env DRY-RUN create: {name}")
        return f"arn:aws:batch:DRY:compute-environment/{name}"

    log(f"Creating compute environment {name}...")
    resp = batch.create_compute_environment(
        computeEnvironmentName=name,
        type="MANAGED",
        state="ENABLED",
        serviceRole=service_role_arn,
        computeResources={
            "type": "SPOT",
            "allocationStrategy": "SPOT_CAPACITY_OPTIMIZED",
            "minvCpus": DEFAULT_MIN_VCPUS,
            "maxvCpus": DEFAULT_MAX_VCPUS,
            "desiredvCpus": DEFAULT_DESIRED_VCPUS,
            "instanceTypes": [DEFAULT_INSTANCE_TYPE],
            "subnets": subnets,
            "securityGroupIds": security_groups,
            "instanceRole": instance_profile_arn,
            "spotIamFleetRole": spot_fleet_role_arn,
            "bidPercentage": DEFAULT_BID_PERCENTAGE,
        },
    )
    arn = resp["computeEnvironmentArn"]

    # Wait for VALID status
    log("Waiting for compute environment to become VALID...")
    for _ in range(60):
        desc = batch.describe_compute_environments(computeEnvironments=[name])
        envs = desc.get("computeEnvironments") or []
        if envs and envs[0]["status"] == "VALID":
            log(f"Compute env created: {name}")
            return arn
        if envs and envs[0]["status"] == "INVALID":
            fail(f"Compute env {name} INVALID: {envs[0].get('statusReason')}")
        time.sleep(5)
    fail(f"Compute env {name} did not reach VALID within 5 minutes")
    return arn  # unreachable, keeps mypy happy


def ensure_job_queue(
    batch,
    queue_name: str,
    compute_environment_arn: str,
    *,
    dry_run: bool = False,
) -> str:
    try:
        resp = batch.describe_job_queues(jobQueues=[queue_name])
        queues = resp.get("jobQueues") or []
        if queues:
            arn = queues[0]["jobQueueArn"]
            log(f"Job queue exists:   {queue_name}")
            return arn
    except ClientError:
        pass

    if dry_run:
        log(f"Job queue DRY-RUN create: {queue_name}")
        return f"arn:aws:batch:DRY:job-queue/{queue_name}"

    resp = batch.create_job_queue(
        jobQueueName=queue_name,
        state="ENABLED",
        priority=1,
        computeEnvironmentOrder=[
            {"order": 1, "computeEnvironment": compute_environment_arn},
        ],
    )
    arn = resp["jobQueueArn"]

    # Wait for VALID
    log("Waiting for job queue to become VALID...")
    for _ in range(30):
        desc = batch.describe_job_queues(jobQueues=[queue_name])
        queues = desc.get("jobQueues") or []
        if queues and queues[0]["status"] == "VALID":
            log(f"Job queue created:  {queue_name}")
            return arn
        time.sleep(5)
    fail(f"Job queue {queue_name} did not reach VALID")
    return arn


def register_job_definition(
    batch,
    name: str,
    *,
    image_uri: str,
    execution_role_arn: str,
    result_bucket: str,
    dry_run: bool = False,
) -> str:
    if dry_run:
        log(f"Job definition DRY-RUN register: {name}")
        return f"arn:aws:batch:DRY:job-definition/{name}:1"

    resp = batch.register_job_definition(
        jobDefinitionName=name,
        type="container",
        containerProperties={
            "image": image_uri,
            "vcpus": 1,
            "memory": 512,
            "jobRoleArn": execution_role_arn,
            "environment": [
                {"name": "DARWIN_BATCH_RESULT_BUCKET", "value": result_bucket},
            ],
        },
    )
    arn = resp["jobDefinitionArn"]
    log(f"Job definition registered: {arn}")
    return arn


# ============================================================================
# Network discovery (use default VPC)
# ============================================================================


def discover_default_vpc(ec2) -> tuple[str, list[str], list[str]]:
    """Return (vpc_id, subnet_ids, security_group_ids) for the default VPC."""
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    if not vpcs["Vpcs"]:
        fail("No default VPC in this region. Create one or pass --vpc/--subnets/--sgs.")
    vpc_id = vpcs["Vpcs"][0]["VpcId"]

    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    subnet_ids = [s["SubnetId"] for s in subnets]

    sgs = ec2.describe_security_groups(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "group-name", "Values": ["default"]},
        ]
    )["SecurityGroups"]
    sg_ids = [g["GroupId"] for g in sgs]

    log(f"Default VPC: {vpc_id}  subnets={len(subnet_ids)}  sgs={len(sg_ids)}")
    return vpc_id, subnet_ids, sg_ids


# ============================================================================
# Main
# ============================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    region = args.region
    dry_run = args.dry_run

    if dry_run:
        log("*** DRY RUN — no AWS resources will be created ***")

    session = boto3.Session(region_name=region)
    sts = session.client("sts")
    account_id = sts.get_caller_identity()["Account"]
    log(f"AWS account: {account_id}  region: {region}")

    iam = session.client("iam")
    ecr = session.client("ecr", region_name=region)
    s3 = session.client("s3", region_name=region)
    ec2 = session.client("ec2", region_name=region)
    batch = session.client("batch", region_name=region)

    # 1. IAM roles
    execution_role_arn = ensure_role(
        iam,
        EXECUTION_ROLE_NAME,
        ECS_TASK_TRUST_POLICY,
        "darwin batch runner execution role",
        dry_run=dry_run,
    )
    attach_managed_policy(
        iam,
        EXECUTION_ROLE_NAME,
        "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
        dry_run=dry_run,
    )
    # S3 result-write permission (added inline so we can scope to the bucket later)
    bucket_name = RESULT_BUCKET_TEMPLATE.format(account_id=account_id, region=region)
    s3_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:PutObject"],
                "Resource": f"arn:aws:s3:::{bucket_name}/*",
            }
        ],
    }
    if not dry_run:
        iam.put_role_policy(
            RoleName=EXECUTION_ROLE_NAME,
            PolicyName="DarwinBatchResultBucketWrite",
            PolicyDocument=json.dumps(s3_policy),
        )
        log("Inline policy attached: DarwinBatchResultBucketWrite")

    ensure_role(
        iam,
        INSTANCE_ROLE_NAME,
        EC2_TRUST_POLICY,
        "darwin batch runner EC2 instance role",
        dry_run=dry_run,
    )
    attach_managed_policy(
        iam,
        INSTANCE_ROLE_NAME,
        "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
        dry_run=dry_run,
    )
    instance_profile_arn = ensure_instance_profile(
        iam,
        INSTANCE_PROFILE_NAME,
        INSTANCE_ROLE_NAME,
        dry_run=dry_run,
    )

    service_role_arn = ensure_role(
        iam,
        SERVICE_ROLE_NAME,
        BATCH_TRUST_POLICY,
        "darwin batch service role",
        dry_run=dry_run,
    )
    attach_managed_policy(
        iam,
        SERVICE_ROLE_NAME,
        "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole",
        dry_run=dry_run,
    )

    spot_fleet_role_arn = ensure_role(
        iam,
        SPOT_FLEET_ROLE_NAME,
        SPOT_FLEET_TRUST_POLICY,
        "darwin batch spot fleet role",
        dry_run=dry_run,
    )
    attach_managed_policy(
        iam,
        SPOT_FLEET_ROLE_NAME,
        "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole",
        dry_run=dry_run,
    )

    # 2. S3 result bucket
    ensure_s3_bucket(s3, bucket_name, region, dry_run=dry_run)

    # 3. ECR repo
    repo_uri = ensure_ecr_repo(ecr, ECR_REPO_PYTHON, dry_run=dry_run)

    # 4. Docker build + push
    if not dry_run:
        docker_login_to_ecr(ecr, region)
        here = Path(__file__).parent
        image_uri = build_and_push_image(
            dockerfile=here / "Dockerfile.batch-runner-python",
            context=here,
            repo_uri=repo_uri,
            tag="latest",
            region=region,
        )
    else:
        image_uri = f"{repo_uri}:latest"

    # 5. Network discovery (default VPC)
    if dry_run:
        subnets = ["subnet-DRY"]
        security_groups = ["sg-DRY"]
    else:
        _, subnets, security_groups = discover_default_vpc(ec2)

    # Wait for IAM eventual consistency before creating Batch resources
    if not dry_run:
        log("Waiting 15s for IAM eventual consistency...")
        time.sleep(15)

    # 6. Batch compute environment
    ce_name = COMPUTE_ENVIRONMENT_NAME_TEMPLATE.format(region=region)
    ce_arn = ensure_compute_environment(
        batch,
        ce_name,
        spot_fleet_role_arn=spot_fleet_role_arn,
        instance_profile_arn=instance_profile_arn,
        service_role_arn=service_role_arn,
        region=region,
        subnets=subnets,
        security_groups=security_groups,
        dry_run=dry_run,
    )

    # 7. Batch job queue
    queue_name = JOB_QUEUE_NAME_TEMPLATE.format(region=region)
    queue_arn = ensure_job_queue(batch, queue_name, ce_arn, dry_run=dry_run)

    # 8. Batch job definition
    jd_name = JOB_DEFINITION_NAME_TEMPLATE.format(region=region)
    jd_arn = register_job_definition(
        batch,
        jd_name,
        image_uri=image_uri,
        execution_role_arn=execution_role_arn,
        result_bucket=bucket_name,
        dry_run=dry_run,
    )

    log("")
    log("Deploy complete:")
    log(f"  ECR image:        {image_uri}")
    log(f"  Result bucket:    {bucket_name}")
    log(f"  Compute env:      {ce_arn}")
    log(f"  Job queue:        {queue_arn}")
    log(f"  Job definition:   {jd_arn}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
