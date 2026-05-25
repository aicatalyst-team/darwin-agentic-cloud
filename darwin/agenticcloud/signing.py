"""Ed25519 signing for DAC attestations.

Stores a local keypair at ~/.darwin/agenticcloud/keys/signing.pem. In production this
gets swapped for Sigstore + OIDC, but the Signer interface stays the
same so callers don't change.
"""

from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from darwin.agenticcloud.hashing import sha256_hex


def _default_key_dir() -> Path:
    """Default keys directory, honoring DARWIN_STATE_DIR env var."""
    import os

    state_dir = os.environ.get("DARWIN_STATE_DIR")
    if state_dir:
        return Path(state_dir) / "keys"
    return Path.home() / ".darwin" / "agenticcloud" / "keys"


DEFAULT_KEY_DIR = _default_key_dir()
DEFAULT_KEY_PATH = DEFAULT_KEY_DIR / "signing.pem"


class Signer:
    """Local Ed25519 signer."""

    def __init__(self, key_path: Path | None = None) -> None:
        self.key_path = key_path or DEFAULT_KEY_PATH
        self._private_key = self._load_or_create()
        self._public_key = self._private_key.public_key()

    def _load_or_create(self) -> Ed25519PrivateKey:
        if self.key_path.exists():
            return self._load()
        return self._create()

    def _load(self) -> Ed25519PrivateKey:
        with self.key_path.open("rb") as f:
            key = serialization.load_pem_private_key(f.read(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError(f"Expected Ed25519 key at {self.key_path}, got {type(key).__name__}")
        return key

    def _create(self) -> Ed25519PrivateKey:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.key_path.write_bytes(pem)
        self.key_path.chmod(0o600)
        return key

    def _public_key_raw(self) -> bytes:
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def public_key_b64(self) -> str:
        """Base64-encoded raw Ed25519 public key (32 bytes -> 44 chars)."""
        return base64.b64encode(self._public_key_raw()).decode()

    def key_id(self) -> str:
        """Stable identifier for this signing key.

        Format: dac-local-<first 16 hex chars of sha256(public_key)>
        """
        return "dac-local-" + sha256_hex(self._public_key_raw())[:16]

    def sign(self, data: bytes) -> str:
        """Sign data, return base64-encoded signature."""
        sig = self._private_key.sign(data)
        return base64.b64encode(sig).decode()


def verify_signature(data: bytes, signature_b64: str, public_key_b64: str) -> bool:
    """Verify a base64-encoded signature against base64 public key."""
    try:
        sig = base64.b64decode(signature_b64)
        pub_raw = base64.b64decode(public_key_b64)
        pub = Ed25519PublicKey.from_public_bytes(pub_raw)
        pub.verify(sig, data)
    except Exception:
        return False
    return True
