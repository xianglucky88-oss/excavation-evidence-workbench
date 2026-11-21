"""recovery-density 测试用的最小合成输入构造器。"""

from excavation_evidence_workbench.contract import Row


def build_sample(sample_id: str, pit: str, liters: float, fractions: list[tuple[str, list[str]]]) -> Row:
    return {
        "id": sample_id,
        "pit": pit,
        "processed_liters": liters,
        "fractions": [
            {"name": name, "material_units": [{"unit": unit} for unit in units]}
            for name, units in fractions
        ],
    }


def build_observation(
    observation_id: str,
    sample: str,
    fraction: str,
    observation_type: str,
    *,
    scope: dict[str, int] | None = None,
    proportion: float | None = None,
    sampling_method: str | None = None,
    group: str | None = None,
    recheck_of: str | None = None,
) -> Row:
    row: Row = {
        "id": observation_id,
        "sample": sample,
        "fraction": fraction,
        "type": observation_type,
    }
    if observation_type != "pending":
        row["scope"] = scope or {}
    if observation_type == "random":
        row["sampling_method"] = sampling_method or "random_split"
        row["subsample_group"] = group
        row["proportion"] = proportion
    else:
        if sampling_method is not None:
            row["sampling_method"] = sampling_method
        if group is not None:
            row["subsample_group"] = group
        if proportion is not None:
            row["proportion"] = proportion
    if recheck_of is not None:
        row["recheck_of"] = recheck_of
    return row


def build_source(samples: list[Row], observations: list[Row] | None = None) -> Row:
    if observations is None:
        observations = []
    return {"taxon": "charred_rice", "samples": samples, "observations": observations}
