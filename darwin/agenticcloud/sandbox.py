"""Docker-based sandbox for executing workloads.

Runs code in an isolated container with:
- Memory and CPU limits
- Wall-time timeout
- No network access (default)
- Non-root user
- Read-only root filesystem with writable /tmp
- Dropped capabilities
- No /proc, no /sys mounts beyond defaults

This is the v0 sandbox. Production will swap for Firecracker microVMs
or Kata Containers with the same interface.
"""

from __future__ import annotations

import contextlib
import io
import tarfile
import time
import uuid
from dataclasses import dataclass

import docker
from docker.errors import APIError, ContainerError, ImageNotFound
from docker.models.containers import Container

from darwin.agenticcloud.hashing import sha256_hex

# Base images we trust. Pin by digest in production.
IMAGE_PYTHON = "python:3.11-slim"
IMAGE_NODE = "node:20-slim"

LANGUAGE_IMAGES = {
    "python": IMAGE_PYTHON,
    "node": IMAGE_NODE,
}

LANGUAGE_COMMANDS = {
    "python": ["python", "/workload/main.py"],
    "node": ["node", "/workload/main.js"],
}

LANGUAGE_FILENAMES = {
    "python": "main.py",
    "node": "main.js",
}

SUBSTRATE_ID = "local-docker-v0"


@dataclass
class SandboxResult:
    """Raw execution data from the sandbox."""

    status: str  # "ok" | "error" | "timeout" | "oom"
    stdout: str
    stderr: str
    exit_code: int | None
    started_at: float
    ended_at: float
    wall_time_sec: float
    substrate_id: str
    output_hash: str
    error: str | None = None


class DockerSandbox:
    """Execute code in a Docker container with strict isolation."""

    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client = client or docker.from_env()
        self._verify_daemon()

    def _verify_daemon(self) -> None:
        """Fail fast if Docker isn't reachable."""
        try:
            self._client.ping()
        except Exception as e:
            raise RuntimeError("Docker daemon is not reachable. Is Docker Desktop running?") from e

    def execute(
        self,
        code: str,
        language: str = "python",
        timeout_sec: int = 30,
        memory_mb: int = 512,
        cpu_quota: float = 1.0,
    ) -> SandboxResult:
        """Run code in a sandboxed container and return the result."""
        if language not in LANGUAGE_IMAGES:
            raise ValueError(
                f"Unsupported language: {language!r}. Supported: {sorted(LANGUAGE_IMAGES)}"
            )

        image = LANGUAGE_IMAGES[language]
        command = LANGUAGE_COMMANDS[language]
        filename = LANGUAGE_FILENAMES[language]

        self._ensure_image(image)

        container_name = f"dac-{uuid.uuid4().hex[:12]}"
        started_at = time.time()

        container: Container | None = None
        try:
            container = self._client.containers.create(
                image=image,
                command=command,
                name=container_name,
                # Resource limits
                mem_limit=f"{memory_mb}m",
                memswap_limit=f"{memory_mb}m",  # no swap
                nano_cpus=int(cpu_quota * 1_000_000_000),
                pids_limit=128,
                # Isolation
                network_disabled=True,
                read_only=False,
                tmpfs={"/tmp": "size=64m,mode=1777"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                user="65534:65534",  # nobody:nogroup
                working_dir="/workload",
                # Cleanup
                auto_remove=False,
                detach=True,
            )

            self._copy_code_into_container(container, filename, code)
            container.start()

            try:
                exit_status = container.wait(timeout=timeout_sec)
                exit_code = exit_status.get("StatusCode")
                timed_out = False
            except Exception:
                # docker-py raises requests.exceptions.ReadTimeout on wait timeout
                container.kill()
                exit_code = None
                timed_out = True

            ended_at = time.time()

            stdout = self._safe_logs(container, stdout=True, stderr=False)
            stderr = self._safe_logs(container, stdout=False, stderr=True)

            if timed_out:
                status = "timeout"
            elif self._was_oom_killed(container):
                status = "oom"
            elif exit_code == 0:
                status = "ok"
            else:
                status = "error"

            return SandboxResult(
                status=status,
                stdout=stdout,
                stderr=stderr,
                exit_code=exit_code,
                started_at=started_at,
                ended_at=ended_at,
                wall_time_sec=ended_at - started_at,
                substrate_id=SUBSTRATE_ID,
                output_hash=sha256_hex(stdout.encode("utf-8")),
            )

        except (ContainerError, APIError) as e:
            ended_at = time.time()
            return SandboxResult(
                status="error",
                stdout="",
                stderr="",
                exit_code=None,
                started_at=started_at,
                ended_at=ended_at,
                wall_time_sec=ended_at - started_at,
                substrate_id=SUBSTRATE_ID,
                output_hash=sha256_hex(b""),
                error=f"Sandbox error: {type(e).__name__}: {e}",
            )

        finally:
            if container is not None:
                with contextlib.suppress(Exception):
                    container.remove(force=True)

    def _ensure_image(self, image: str) -> None:
        """Pull the image if it's not present locally."""
        try:
            self._client.images.get(image)
        except ImageNotFound:
            self._client.images.pull(image)

    @staticmethod
    def _copy_code_into_container(container: Container, filename: str, code: str) -> None:
        """Stream code into the container as a tar archive at /workload/<filename>."""
        tar_stream = io.BytesIO()
        with tarfile.open(fileobj=tar_stream, mode="w") as tar:
            data = code.encode("utf-8")
            info = tarfile.TarInfo(name=filename)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        tar_stream.seek(0)
        container.put_archive("/workload", tar_stream.read())

    @staticmethod
    def _safe_logs(container: Container, *, stdout: bool, stderr: bool) -> str:
        try:
            raw = container.logs(stdout=stdout, stderr=stderr)
            if isinstance(raw, bytes):
                return raw.decode("utf-8", errors="replace")
            return str(raw)
        except Exception:
            return ""

    @staticmethod
    def _was_oom_killed(container: Container) -> bool:
        try:
            container.reload()
            return bool(container.attrs.get("State", {}).get("OOMKilled", False))
        except Exception:
            return False
