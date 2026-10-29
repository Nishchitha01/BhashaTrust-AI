from decimal import Decimal

from .cost_data import DEMO_LICENSE_COSTS
from .database import connect
from .decision_service import DatasetNotFoundError
from .schemas import CostAllocationResponse, CostLine


class CostDataUnavailableError(LookupError):
    pass


def money_from_minor_units(value: int) -> Decimal:
    return (Decimal(value) / Decimal(100)).quantize(Decimal("0.01"))


def minor_units_from_money(value: Decimal) -> int:
    return int(value * 100)


def calculate_allocation(items: list[CostLine], budget: Decimal, currency: str) -> CostAllocationResponse:
    total_minor = sum(minor_units_from_money(item.cost) for item in items)
    budget_minor = minor_units_from_money(budget)
    total = money_from_minor_units(total_minor)
    remaining = money_from_minor_units(budget_minor - total_minor)
    return CostAllocationResponse(
        budget=budget,
        currency=currency,
        items=items,
        total_cost=total,
        remaining_budget=remaining,
        budget_status="WITHIN_BUDGET" if total_minor <= budget_minor else "OVER_BUDGET",
    )


def get_cost_lines(dataset_ids: list[int], currency: str) -> list[CostLine]:
    if not dataset_ids:
        return []
    placeholders = ",".join("?" for _ in dataset_ids)
    with connect() as connection:
        rows = connection.execute(
            f"SELECT id, name FROM datasets WHERE id IN ({placeholders})", dataset_ids
        ).fetchall()
    by_id = {row["id"]: row["name"] for row in rows}
    missing = [dataset_id for dataset_id in dataset_ids if dataset_id not in by_id]
    if missing:
        raise DatasetNotFoundError(missing[0])

    lines = []
    for dataset_id in dataset_ids:
        price = DEMO_LICENSE_COSTS.get(by_id[dataset_id])
        if price is None:
            raise CostDataUnavailableError(
                f"No structured licensing cost is configured for dataset {dataset_id}."
            )
        if price.currency != currency:
            raise CostDataUnavailableError(
                f"Dataset {dataset_id} is priced in {price.currency}, not requested {currency}."
            )
        lines.append(
            CostLine(
                dataset_id=dataset_id,
                dataset_name=price.dataset_name,
                cost=money_from_minor_units(price.cost_minor_units),
                currency=price.currency,
                utility_score=price.utility_score,
                synthetic_demo_cost=price.synthetic,
                cost_source=price.source,
            )
        )
    return lines
