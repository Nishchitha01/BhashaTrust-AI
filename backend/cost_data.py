"""Synthetic demo prices and utility values; not vendor quotes or real licenses."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DemoCost:
    dataset_name: str
    cost_minor_units: int
    currency: str
    utility_score: int
    source: str = "synthetic_demo_data"
    synthetic: bool = True


DEMO_LICENSE_COSTS = {
    "Synthetic Hindi News Sample": DemoCost(
        dataset_name="Synthetic Hindi News Sample",
        cost_minor_units=12500,
        currency="USD",
        utility_score=90,
    ),
    "Synthetic Marathi Speech Sample": DemoCost(
        dataset_name="Synthetic Marathi Speech Sample",
        cost_minor_units=8000,
        currency="USD",
        utility_score=70,
    ),
}
