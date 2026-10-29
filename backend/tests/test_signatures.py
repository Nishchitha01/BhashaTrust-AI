import base64
from datetime import date, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.schemas import RightsPayload
from backend.signatures import canonicalize_rights, verify_rights_signature


def signed_payload():
    payload = RightsPayload(
        dataset_id=1,
        grantor="Synthetic demo contributor",
        license_name="Synthetic research permission",
        permitted_purposes=["research_training"],
        commercial_use=False,
        expires_on=date.today() + timedelta(days=30),
    )
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    signature = private_key.sign(canonicalize_rights(payload))
    return payload, base64.b64encode(public_key).decode(), base64.b64encode(signature).decode()


def test_valid_ed25519_signature_verifies():
    payload, public_key, signature = signed_payload()
    valid, _ = verify_rights_signature(payload, public_key, signature)
    assert valid


def test_invalid_signature_is_rejected():
    payload, public_key, _ = signed_payload()
    wrong_signature = base64.b64encode(b"not a valid Ed25519 signature".ljust(64, b"!")).decode()
    valid, _ = verify_rights_signature(payload, public_key, wrong_signature)
    assert not valid


def test_modified_record_does_not_verify():
    payload, public_key, signature = signed_payload()
    modified = payload.model_copy(update={"commercial_use": True})
    valid, _ = verify_rights_signature(modified, public_key, signature)
    assert not valid


def test_canonicalization_is_stable_and_sorted():
    payload, _, _ = signed_payload()
    canonical = canonicalize_rights(payload)
    assert canonical == canonicalize_rights(payload)
    assert canonical.startswith(b'{"commercial_use":false,"dataset_id":1')
