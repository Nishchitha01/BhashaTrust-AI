import base64
import binascii
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .schemas import RightsPayload


def canonicalize_rights(payload: RightsPayload) -> bytes:
    """Return the stable UTF-8 representation covered by the signature."""
    permission_fields = {"redistribution_allowed", "derivative_work_allowed"}
    omit_unset_fields = {
        field for field in permission_fields
        if field not in payload.model_fields_set or getattr(payload, field) is None
    }
    serialized = json.dumps(
        payload.model_dump(mode="json", exclude=omit_unset_fields),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return serialized.encode("utf-8")


def verify_rights_signature(payload: RightsPayload, public_key_b64: str, signature_b64: str) -> tuple[bool, str]:
    try:
        public_key_bytes = base64.b64decode(public_key_b64, validate=True)
        signature_bytes = base64.b64decode(signature_b64, validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        public_key.verify(signature_bytes, canonicalize_rights(payload))
    except (InvalidSignature, ValueError, binascii.Error):
        return False, "Signature verification failed."
    return True, "Signature is valid for the submitted public key and canonical rights payload."
