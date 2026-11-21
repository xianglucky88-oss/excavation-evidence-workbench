"""按原始取土样本比较指定类群的回收密度。

一份取土样本浮选后可拆成多个鉴定组分（如轻、重组），各组分的鉴定作业
又可能是完整鉴定、登记过抽样范围与比例的随机子样、只挑易辨认材料的
局部作业，或尚未鉴定。本数据链在取土样本（灰坑）层面汇总：

* 同一份土的多个组分共同提供计数，但处理体积在样本层面只承担一次；
* 完整鉴定给出确定结果；随机子样按已登记的有效覆盖比例外推到其抽样
  框，给出带不确定范围的估计（泊松计数区间，零计数采用“3 法则”上界）；
  抽查范围重叠时按去重后的材料单元计算有效覆盖比例；
* 尚未鉴定的材料不在任何抽样框内、只挑易辨认材料的计数有偏，二者都会
  阻断整份组分/整份土的推断并保留原因；局部挑样仍作为有效观察列出；
* 真实未检出（已鉴定且计数为零）与尚未鉴定分别归属；
* 复核计数不重复计入，其与原观察不一致时标注分歧；复核范围超出原观察
  时，重复单元与新增单元分开列示；
* 资料不足以推断整份土时保留原因；灰坑比较标明支撑样本、鉴定范围，
  以及差异能否解释为整份土的回收差异。
"""

from __future__ import annotations

from collections import defaultdict

from .contract import (
    Row,
    index_rows,
    number,
    read_rows,
    text,
)

PROPORTION_TOLERANCE = 1e-6
ZERO_UPPER_FACTOR = 3.0


def _optional_text(row: Row, field: str) -> str | None:
    if field not in row or row[field] is None:
        return None
    value = row[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value


def _unit_counts(row: Row, field: str) -> dict[str, int]:
    if field not in row or row[field] is None:
        return {}
    value = row[field]
    if not isinstance(value, dict) or not value:
        raise ValueError(f"{field} must be a nonempty JSON object")
    result = {}
    for unit, count in value.items():
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"{field} keys must be nonempty text")
        if type(count) is not int or count < 0:
            raise ValueError(f"{field} counts must be nonnegative integers")
        result[unit] = count
    return result


def _parse_input(data: Row) -> tuple[str, dict[str, Row], dict[str, dict]]:
    taxon = text(data, "taxon")
    samples = index_rows(read_rows(data, "samples"), "id")
    parsed_samples = {}
    for key, row in samples.items():
        liters = number(row, "processed_liters", minimum=0)
        if liters == 0:
            raise ValueError("processed_liters must be positive")
        fractions = index_rows(read_rows(row, "fractions"), "name")
        fraction_units = {}
        for name, fraction in fractions.items():
            units = read_rows(fraction, "material_units")
            unit_ids = [text(unit_row, "unit") for unit_row in units]
            if len(set(unit_ids)) != len(unit_ids):
                raise ValueError(f"duplicate material unit in fraction {name}")
            if not unit_ids:
                raise ValueError(f"fraction {name} needs at least one material unit")
            fraction_units[name] = unit_ids
        if not fraction_units:
            raise ValueError(f"sample {key} needs at least one fraction")
        parsed_samples[key] = {
            "pit": text(row, "pit"),
            "processed_liters": liters,
            "units": fraction_units,
        }

    observations = index_rows(read_rows(data, "observations"), "id")
    parsed_observations = {}
    group_members = defaultdict(list)
    for key, row in observations.items():
        sample_id = text(row, "sample")
        if sample_id not in parsed_samples:
            raise ValueError(f"unknown sample in observation {key}")
        fraction = text(row, "fraction")
        known_units = set(parsed_samples[sample_id]["units"].get(fraction, ()))
        if not known_units:
            raise ValueError(f"unknown fraction in observation {key}")
        observation_type = text(row, "type")
        if observation_type not in ("complete", "random", "selective", "pending"):
            raise ValueError(f"unknown observation type: {observation_type}")
        scope = _unit_counts(row, "scope")
        unknown = set(scope) - known_units
        if unknown:
            raise ValueError(f"observation {key} references unknown material units")
        recheck_of = _optional_text(row, "recheck_of")
        if recheck_of is not None and recheck_of not in observations:
            raise ValueError(f"observation {key} rechecks an unknown observation")
        if recheck_of == key:
            raise ValueError(f"observation {key} cannot recheck itself")
        sampling_method = _optional_text(row, "sampling_method")
        proportion = None
        if "proportion" in row and row["proportion"] is not None:
            proportion = number(row, "proportion")
            if not (0 < proportion <= 1):
                raise ValueError("proportion must be greater than 0 and at most 1")
        if observation_type == "pending":
            if scope:
                raise ValueError("pending observations carry no counts")
            if proportion is not None or sampling_method is not None:
                raise ValueError("pending observations carry no sampling details")
        else:
            if not scope:
                raise ValueError(f"observation {key} needs an examined scope")
        if observation_type == "complete":
            if proportion is not None and abs(proportion - 1.0) > PROPORTION_TOLERANCE:
                raise ValueError("complete observations must cover the full fraction")
            if set(scope) != known_units:
                raise ValueError(
                    f"complete observation {key} must cover every material unit of its fraction"
                )
            proportion = 1.0
            sampling_method = None
        elif observation_type == "random":
            if not sampling_method:
                raise ValueError(f"random observation {key} needs a registered sampling_method")
            if proportion is None:
                raise ValueError(f"random observation {key} needs a registered proportion")
            implied = len(scope) / len(known_units)
            if abs(implied - proportion) > PROPORTION_TOLERANCE:
                raise ValueError(
                    f"random observation {key} proportion does not match its scope size"
                )
        elif observation_type == "selective":
            if sampling_method is not None or proportion is not None:
                raise ValueError("selective observations cannot claim random sampling coverage")
        group = _optional_text(row, "subsample_group")
        if observation_type == "random":
            if not group:
                raise ValueError(f"random observation {key} needs a subsample_group")
            group_members[(sample_id, fraction, group)].append((key, recheck_of))
        elif group is not None:
            raise ValueError(f"only random observations belong to a subsample_group ({key})")
        parsed_observations[key] = {
            "sample": sample_id,
            "fraction": fraction,
            "type": observation_type,
            "scope": scope,
            "recheck_of": recheck_of,
            "sampling_method": sampling_method,
            "proportion": proportion,
            "group": group,
        }

    for (sample_id, fraction, group), members in group_members.items():
        primary = [key for key, recheck_of in members if recheck_of is None]
        if len(primary) != 1:
            raise ValueError(
                f"subsample group {group} needs exactly one primary observation"
            )
        for key, recheck_of in members:
            if recheck_of is not None and recheck_of not in dict(members):
                raise ValueError(
                    f"recheck {key} must target an observation in subsample group {group}"
                )
        primary_key = primary[0]
        for key, recheck_of in members:
            target = primary_key if recheck_of is None else recheck_of
            target_scope = set(parsed_observations[target]["scope"])
            scope = set(parsed_observations[key]["scope"])
            if not target_scope.issubset(scope):
                raise ValueError(
                    f"recheck {key} must cover every unit examined by {target}"
                )
    for key, row in parsed_observations.items():
        if row["recheck_of"] is not None:
            target = parsed_observations[row["recheck_of"]]
            if target["recheck_of"] is not None:
                raise ValueError(f"observation {key} rechecks another recheck")
            if (target["sample"], target["fraction"]) != (row["sample"], row["fraction"]):
                raise ValueError(f"recheck {key} targets a different fraction")
    return taxon, parsed_samples, parsed_observations


def _evidence_entry(key: str, row: dict) -> Row:
    entry = {
        "observation_id": key,
        "type": row["type"],
        "fraction": row["fraction"],
        "scope_units": sorted(row["scope"]),
        "recheck_of": row["recheck_of"],
    }
    if row["type"] == "random":
        entry["sampling_method"] = row["sampling_method"]
        entry["subsample_group"] = row["group"]
        entry["registered_proportion"] = round(row["proportion"], 6)
    return entry


def _analyze_fraction(
    units: list[str],
    observations: dict[str, dict],
) -> dict:
    """把同一组分的观察按材料单元归并，区分重复观察与新增观察。"""
    complete_units: set[str] = set()
    random_units: set[str] = set()
    selective_units: set[str] = set()
    pending_present = False
    observed_count = 0
    local_count = 0
    true_absences: list[str] = []
    evidence = []
    rechecks = []

    # 每个材料单元保留首个非复核观察作为权威计数；同单元的后续观察即重叠。
    unit_owner: dict[str, str] = {}
    unit_counts: dict[str, int] = {}
    discrepancies = []

    for key in sorted(observations):
        row = observations[key]
        evidence.append(_evidence_entry(key, row))
        if row["type"] == "pending":
            pending_present = True
            continue
        if row["recheck_of"] is not None:
            target_scope = set(observations[row["recheck_of"]]["scope"])
            repeated = sorted(set(row["scope"]) & target_scope)
            added = sorted(set(row["scope"]) - target_scope)
            agrees = all(
                unit_owner.get(unit) is not None
                and unit_counts.get(unit) == row["scope"][unit]
                for unit in row["scope"]
            )
            rechecks.append(
                {
                    "observation_id": key,
                    "recheck_of": row["recheck_of"],
                    "repeated_units": repeated,
                    "new_units": added,
                    "agrees_with_primary": agrees,
                }
            )
            if not agrees:
                discrepancies.append(
                    {
                        "unit": next(
                            unit
                            for unit in row["scope"]
                            if unit_owner.get(unit) is None
                            or unit_counts.get(unit) != row["scope"][unit]
                        ),
                        "observations": sorted([key, row["recheck_of"]]),
                    }
                )
            # 复核本身不新增计数；若其范围超出原观察，新增单元按其类型补入。
            for unit, count in row["scope"].items():
                if unit not in unit_owner:
                    unit_owner[unit] = key
                    unit_counts[unit] = count
            continue
        for unit, count in row["scope"].items():
            if unit in unit_owner:
                # 抽查范围重叠：重复观察只保留首次计数，不重复累加。
                if unit_counts[unit] != count:
                    discrepancies.append(
                        {"unit": unit, "observations": sorted([unit_owner[unit], key])}
                    )
                continue
            unit_owner[unit] = key
            unit_counts[unit] = count

    for unit in sorted(unit_owner):
        owner = observations[unit_owner[unit]]
        count = unit_counts[unit]
        if owner["type"] == "complete":
            complete_units.add(unit)
            observed_count += count
            if count == 0:
                true_absences.append(unit)
        elif owner["type"] == "random":
            random_units.add(unit)
            observed_count += count
            if count == 0:
                true_absences.append(unit)
        else:
            selective_units.add(unit)
            local_count += count

    examined = complete_units | random_units | selective_units
    total_units = len(units)
    inferred_count = None
    interval_low = None
    interval_high = None
    basis = None
    unsupported_reasons = []

    # 尚未鉴定的材料不在任何抽样框内，无法代表整份组分。
    if pending_present:
        unsupported_reasons.append(
            "part_of_fraction_pending_identification"
            if examined
            else "material_not_identified"
        )
    # 只挑易辨认材料的计数有偏，不能外推到整份组分。
    if selective_units:
        unsupported_reasons.append("only_selective_picking_available")
    if not examined and not pending_present:
        unsupported_reasons.append("no_observation_recorded")

    if not unsupported_reasons:
        if complete_units == set(units):
            inferred_count = float(observed_count)
            interval_low = float(observed_count)
            interval_high = float(observed_count)
            basis = "complete"
        else:
            # 完整单元给出确定计数；其余材料处于已登记的随机抽样框内，
            # 按去重后的有效覆盖比例联合外推。
            complete_count = sum(unit_counts[u] for u in complete_units)
            random_count = sum(unit_counts[u] for u in random_units)
            frame_size = total_units - len(complete_units)
            effective_proportion = len(random_units) / frame_size
            if not 0 < effective_proportion <= 1:
                raise ValueError("random subsample coverage must be positive")
            random_estimate = random_count / effective_proportion
            inferred_count = complete_count + random_estimate
            if random_count == 0:
                interval_low = float(complete_count)
                interval_high = (
                    complete_count + ZERO_UPPER_FACTOR / effective_proportion
                )
            else:
                half = 1.96 * random_count ** 0.5 / effective_proportion
                interval_low = max(0.0, complete_count + random_estimate - half)
                interval_high = complete_count + random_estimate + half
            basis = "random_subsample"

    return {
        "evidence": evidence,
        "rechecks": rechecks,
        "discrepancies": discrepancies,
        "observed_count": observed_count,
        "local_observations_count": local_count,
        "true_absence_units": true_absences,
        "pending": pending_present,
        "inferred_count": inferred_count,
        "interval_low": interval_low,
        "interval_high": interval_high,
        "basis": basis,
        "unsupported_reasons": unsupported_reasons,
        "examined_units": sorted(examined),
        "selective_units": sorted(selective_units),
    }


def _build_sample_facts(
    samples: dict[str, dict], observations: dict[str, dict]
) -> dict[str, Row]:
    grouped = defaultdict(lambda: defaultdict(dict))
    for key, row in observations.items():
        grouped[row["sample"]][row["fraction"]][key] = row

    facts = {}
    for sample_id in sorted(samples):
        sample = samples[sample_id]
        fraction_results = {}
        for fraction in sample["units"]:
            fraction_results[fraction] = _analyze_fraction(
                sample["units"][fraction],
                grouped[sample_id].get(fraction, {}),
            )

        supported = True
        reasons = []
        inferred = 0.0
        low = 0.0
        high = 0.0
        observed = 0
        local = 0
        pending = False
        bases = set()
        for fraction in sorted(fraction_results):
            result = fraction_results[fraction]
            observed += result["observed_count"]
            local += result["local_observations_count"]
            pending = pending or result["pending"]
            if result["basis"] is None:
                supported = False
                for reason in result["unsupported_reasons"]:
                    reasons.append({"fraction": fraction, "reason": reason})
            else:
                bases.add(result["basis"])
                inferred += result["inferred_count"]
                low += result["interval_low"]
                high += result["interval_high"]

        volume = sample["processed_liters"]
        if supported:
            inference = {
                "supported": True,
                "basis": "complete" if bases == {"complete"} else "random_subsample",
                "inferred_count": round(inferred, 6),
                "count_95_interval": [round(low, 6), round(high, 6)],
                "recovered_per_liter": round(inferred / volume, 6),
                "density_95_interval": [round(low / volume, 6), round(high / volume, 6)],
                "limitations": [],
            }
        else:
            inference = {
                "supported": False,
                "basis": None,
                "inferred_count": None,
                "count_95_interval": None,
                "recovered_per_liter": None,
                "density_95_interval": None,
                "limitations": reasons,
            }

        fraction_summaries = []
        for fraction in sorted(fraction_results):
            result = fraction_results[fraction]
            fraction_summaries.append(
                {
                    "fraction": fraction,
                    "identification_basis": result["basis"],
                    "observed_count": result["observed_count"],
                    "local_observations_count": result["local_observations_count"],
                    "inferred_count": (
                        round(result["inferred_count"], 6)
                        if result["inferred_count"] is not None
                        else None
                    ),
                    "count_95_interval": (
                        [
                            round(result["interval_low"], 6),
                            round(result["interval_high"], 6),
                        ]
                        if result["interval_low"] is not None
                        else None
                    ),
                    "true_absence_units": result["true_absence_units"],
                    "pending_identification": result["pending"],
                    "examined_units": result["examined_units"],
                    "selective_only_units": result["selective_units"],
                    "unsupported_reasons": result["unsupported_reasons"],
                    "rechecks": result["rechecks"],
                    "count_discrepancies": result["discrepancies"],
                    "evidence": result["evidence"],
                }
            )

        facts[sample_id] = {
            "fact_id": f"sample:{sample_id}",
            "sample": sample_id,
            "pit": sample["pit"],
            "processed_liters": volume,
            "observed_count": observed,
            "local_observations_count": local,
            "has_pending_identification": pending,
            "whole_sample_inference": inference,
            "fractions": fraction_summaries,
        }
    return facts


def _build_pit_facts(
    samples: dict[str, dict], sample_facts: dict[str, Row]
) -> dict[str, Row]:
    pits = defaultdict(list)
    for sample_id in sorted(samples):
        pits[samples[sample_id]["pit"]].append(sample_id)

    facts = {}
    for pit in sorted(pits):
        member_ids = pits[pit]
        supported_members = [
            sample_id
            for sample_id in member_ids
            if sample_facts[sample_id]["whole_sample_inference"]["supported"]
        ]
        volume = sum(samples[sample_id]["processed_liters"] for sample_id in member_ids)
        observed = sum(sample_facts[sample_id]["observed_count"] for sample_id in member_ids)
        local = sum(
            sample_facts[sample_id]["local_observations_count"] for sample_id in member_ids
        )
        limitations = [
            {"sample": sample_id, **reason}
            for sample_id in member_ids
            for reason in sample_facts[sample_id]["whole_sample_inference"]["limitations"]
        ]
        if len(supported_members) == len(member_ids):
            inferred = low = high = 0.0
            bases = set()
            for sample_id in member_ids:
                inference = sample_facts[sample_id]["whole_sample_inference"]
                bases.add(inference["basis"])
                inferred += inference["inferred_count"]
                low += inference["count_95_interval"][0]
                high += inference["count_95_interval"][1]
            facts[pit] = {
                "fact_id": f"pit:{pit}",
                "pit": pit,
                "sample_ids": member_ids,
                "processed_liters": volume,
                "observed_count": observed,
                "local_observations_count": local,
                "whole_pit_inference": {
                    "supported": True,
                    "basis": "complete" if bases == {"complete"} else "random_subsample",
                    "inferred_count": round(inferred, 6),
                    "count_95_interval": [round(low, 6), round(high, 6)],
                    "recovered_per_liter": round(inferred / volume, 6),
                    "density_95_interval": [
                        round(low / volume, 6),
                        round(high / volume, 6),
                    ],
                    "limitations": [],
                },
            }
        else:
            facts[pit] = {
                "fact_id": f"pit:{pit}",
                "pit": pit,
                "sample_ids": member_ids,
                "processed_liters": volume,
                "observed_count": observed,
                "local_observations_count": local,
                "whole_pit_inference": {
                    "supported": False,
                    "basis": None,
                    "inferred_count": None,
                    "count_95_interval": None,
                    "recovered_per_liter": None,
                    "density_95_interval": None,
                    "limitations": limitations,
                },
            }
    return facts


def _build_comparison_facts(
    pit_facts: dict[str, Row], sample_facts: dict[str, Row]
) -> list[Row]:
    comparisons = []
    pit_names = sorted(pit_facts)
    for index, left in enumerate(pit_names):
        for right in pit_names[index + 1 :]:
            left_fact, right_fact = pit_facts[left], pit_facts[right]
            left_inference = left_fact["whole_pit_inference"]
            right_inference = right_fact["whole_pit_inference"]
            sample_breakdown = []
            for fact in (left_fact, right_fact):
                for sample_id in fact["sample_ids"]:
                    sample_fact = sample_facts[sample_id]
                    inference = sample_fact["whole_sample_inference"]
                    sample_breakdown.append(
                        {
                            "pit": fact["pit"],
                            "sample": sample_id,
                            "processed_liters": sample_fact["processed_liters"],
                            "observed_count": sample_fact["observed_count"],
                            "local_observations_count": sample_fact[
                                "local_observations_count"
                            ],
                            "identification_basis": inference["basis"],
                            "recovered_per_liter": inference["recovered_per_liter"],
                            "density_95_interval": inference["density_95_interval"],
                            "examined_scope": [
                                {
                                    "fraction": fraction["fraction"],
                                    "examined_units": fraction["examined_units"],
                                    "selective_only_units": fraction["selective_only_units"],
                                }
                                for fraction in sample_fact["fractions"]
                            ],
                            "unsupported_reasons": [
                                reason["reason"]
                                for reason in inference["limitations"]
                            ],
                        }
                    )
            if left_inference["supported"] and right_inference["supported"]:
                left_low, left_high = left_inference["density_95_interval"]
                right_low, right_high = right_inference["density_95_interval"]
                difference = round(
                    left_inference["recovered_per_liter"]
                    - right_inference["recovered_per_liter"],
                    6,
                )
                comparisons.append(
                    {
                        "fact_id": f"comparison:{left}:{right}",
                        "left_pit": left,
                        "right_pit": right,
                        "interpretable_as_whole_soil_recovery_difference": True,
                        "density_difference_per_liter": difference,
                        "density_intervals_overlap": not (
                            left_high < right_low or right_high < left_low
                        ),
                        "limitations": [],
                        "samples": sample_breakdown,
                    }
                )
            else:
                limitations = []
                for fact, inference in (
                    (left_fact, left_inference),
                    (right_fact, right_inference),
                ):
                    if not inference["supported"]:
                        for reason in inference["limitations"]:
                            limitations.append({"pit": fact["pit"], **reason})
                comparisons.append(
                    {
                        "fact_id": f"comparison:{left}:{right}",
                        "left_pit": left,
                        "right_pit": right,
                        "interpretable_as_whole_soil_recovery_difference": False,
                        "density_difference_per_liter": None,
                        "density_intervals_overlap": None,
                        "limitations": limitations,
                        "samples": sample_breakdown,
                    }
                )
    return comparisons


def build_facts(data: Row) -> list[Row]:
    taxon, samples, observations = _parse_input(data)
    sample_facts = _build_sample_facts(samples, observations)
    pit_facts = _build_pit_facts(samples, sample_facts)
    comparison_facts = _build_comparison_facts(pit_facts, sample_facts)
    facts = [
        {"fact_id": "taxon", "taxon": taxon},
        *[sample_facts[key] for key in sorted(sample_facts)],
        *[pit_facts[key] for key in sorted(pit_facts)],
        *comparison_facts,
    ]
    return facts
