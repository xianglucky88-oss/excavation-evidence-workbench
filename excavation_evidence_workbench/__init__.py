"""考古发掘证据"""

from . import (
    ceramic_counts,
    ceramic_reconciliation,
    dating_evidence,
    fieldwalking_density,
    flotation_yield,
    stratigraphic_order,
)
from .contract import Row

PIPELINES = {
    "stratigraphic-order": stratigraphic_order.build_facts,
    "dating-evidence": dating_evidence.build_facts,
    "ceramic-counts": ceramic_counts.build_facts,
    "ceramic-reconciliation": ceramic_reconciliation.build_facts,
    "flotation-yield": flotation_yield.build_facts,
    "fieldwalking-density": fieldwalking_density.build_facts,
}


def process(pipeline: str, data: Row) -> list[Row]:
    """Validate and transform an independent input dataset into keyed facts."""
    if pipeline not in PIPELINES:
        raise ValueError(f"unknown pipeline: {pipeline}")
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    return PIPELINES[pipeline](data)
