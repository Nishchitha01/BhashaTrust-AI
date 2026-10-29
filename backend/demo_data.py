"""Synthetic examples for local demonstration; not real licensed datasets."""

DEMO_DATASETS = [
    {
        "name": "Synthetic Hindi News Sample",
        "language": "Hindi",
        "owner": "Synthetic demo contributor",
        "description": "Fabricated metadata for demonstrating a research-only license.",
        "license_name": "Synthetic Research-Only License",
        "permitted_purposes": ["research_training"],
        "commercial_use": False,
        "geographic_restrictions": [],
        "expires_on": None,
        "revoked": False,
        "evidence_verified": True,
        "synthetic": True,
    },
    {
        "name": "Synthetic Marathi Speech Sample",
        "language": "Marathi",
        "owner": "Synthetic demo contributor",
        "description": "Fabricated metadata with unverified permission evidence.",
        "license_name": "Unverified Demonstration License",
        "permitted_purposes": ["research_training"],
        "commercial_use": None,
        "geographic_restrictions": [],
        "expires_on": None,
        "revoked": False,
        "evidence_verified": False,
        "synthetic": True,
    },
]
