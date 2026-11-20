"""按发掘阶段复核年代。

把已确认的地层先后、标本的出土归属、实验室给出的测值允许范围和随资料
提供的参考曲线（测值与历年对应关系）放在一起判断：

* 参考曲线回折（同一测值对应多个历年段）时，保留落在允许范围内的每一段；
* 原位标本用于限定所属发掘阶段；可能来自旧层的标本分别按两种归属给出
  各自能够支持的判断；
* 没有相对关系的探方（连通分量）保持独立，不互相施加年代约束；
* 现场判读之间矛盾（地层成环）或校准结果与可核实的先后关系无法同时成立
  （年代倒置）时，给出受影响阶段及相冲突的证据，其他阶段的结论保留；
* 复核员撤回关系或改正标本归属后，输出每条复核导致的年代范围变化及其
  证据依据。
"""

from __future__ import annotations

from .contract import Row, index_rows, number, read_rows, text

CONFIRMED = "confirmed"
WITHDRAWN = "withdrawn"
IN_SITU = "in-situ"
REWORKED = "reworked"
AMBIGUOUS = "ambiguous"
RELATION_KIND = "relation"
SPECIMEN_KIND = "specimen"


def _load(data: Row) -> dict[str, object]:
    trenches = index_rows(read_rows(data, "trenches"), "id")
    deposits = index_rows(read_rows(data, "deposits"), "id")
    for deposit_id, row in deposits.items():
        trench_id = text(row, "trench")
        if trench_id not in trenches:
            raise ValueError(f"unknown trench in deposit {deposit_id}")
    relations: dict[str, Row] = {}
    for relation_id, row in index_rows(read_rows(data, "relations"), "id").items():
        earlier, later = text(row, "earlier"), text(row, "later")
        if earlier not in deposits or later not in deposits:
            raise ValueError(f"unknown deposit in relation {relation_id}")
        if earlier == later:
            raise ValueError(f"relation {relation_id} cannot reference the same deposit twice")
        state = text(row, "state")
        if state != CONFIRMED:
            raise ValueError(
                f"relation {relation_id} must be confirmed in the field record; "
                "withdrawals are recorded as reviews"
            )
        relations[relation_id] = {"id": relation_id, "earlier": earlier,
                                  "later": later, "state": state}

    specimens: dict[str, Row] = {}
    for specimen_id, row in index_rows(read_rows(data, "specimens"), "id").items():
        deposit_id = text(row, "deposit")
        if deposit_id not in deposits:
            raise ValueError(f"unknown deposit in specimen {specimen_id}")
        attribution = text(row, "field_attribution")
        if attribution not in (IN_SITU, REWORKED, AMBIGUOUS):
            raise ValueError(f"specimen {specimen_id} has an unsupported field_attribution")
        origin = row.get("origin")
        if attribution in (REWORKED, AMBIGUOUS):
            if "origin" not in row:
                raise ValueError(f"specimen {specimen_id} requires an origin deposit")
            origin = text(row, "origin")
            if origin not in deposits:
                raise ValueError(f"unknown origin deposit in specimen {specimen_id}")
        elif origin is not None:
            if not isinstance(origin, str) or not origin.strip() or origin not in deposits:
                raise ValueError(f"invalid origin in specimen {specimen_id}")
        specimens[specimen_id] = {
            "id": specimen_id,
            "deposit": deposit_id,
            "field_attribution": attribution,
            "origin": origin,
            "measured_bp": number(row, "measured_bp", minimum=0),
            "tolerance_bp": number(row, "tolerance_bp", minimum=0),
        }

    # 曲线点按资料提供的顺序（历年次序）保留：回折时同一测值允许范围会
    # 在曲线上截出多个不相交的历年段，按测值重排会破坏这一结构。
    curve_rows = read_rows(data, "reference_points")
    if not curve_rows:
        raise ValueError("a reference curve is required")
    index_rows(curve_rows, "id")
    points = [
        {
            "id": text(row, "id"),
            "reference_bp": number(row, "reference_bp", minimum=0),
            "calendar_bp": number(row, "calendar_bp"),
        }
        for row in curve_rows
    ]

    reviews: dict[str, Row] = {}
    review_targets: set[str] = set()
    for review_id, row in index_rows(read_rows(data, "reviews") if "reviews" in data else [], "id").items():
        target_kind = text(row, "target_kind")
        target = text(row, "target")
        action = text(row, "action")
        resolution = text(row, "resolution")
        if target in review_targets:
            raise ValueError(f"duplicate review target: {target}")
        if target_kind == RELATION_KIND:
            if (target not in relations or action != "withdraw" or resolution != WITHDRAWN):
                raise ValueError(f"review {review_id} does not withdraw a confirmed relation")
        elif target_kind == SPECIMEN_KIND:
            if (target not in specimens or action != "correct-attribution"
                    or resolution not in (IN_SITU, REWORKED)
                    or specimens[target]["field_attribution"] != AMBIGUOUS):
                raise ValueError(f"review {review_id} does not resolve an ambiguous specimen")
        else:
            raise ValueError(f"review {review_id} has an unsupported target_kind")
        note = row.get("note", "")
        if not isinstance(note, str):
            raise ValueError(f"review {review_id} note must be text")
        review_targets.add(target)
        reviews[review_id] = {"id": review_id, "target_kind": target_kind, "target": target,
                              "action": action, "resolution": resolution, "note": note}
    return {"trenches": trenches, "deposits": deposits, "relations": relations,
            "specimens": specimens, "points": points, "reviews": reviews}


def _calibrate(measured_bp: float, tolerance_bp: float, points: list[Row]) -> tuple[float, float, list[Row]]:
    """返回允许的测值范围，以及参考曲线上落入该范围的各回折段。"""
    lower, upper = measured_bp - tolerance_bp, measured_bp + tolerance_bp
    segments: list[Row] = []
    current: list[Row] = []
    for point in points:
        inside = lower <= point["reference_bp"] <= upper
        if inside:
            current.append(point)
        elif current:
            segments.append(_segment(current))
            current = []
    if current:
        segments.append(_segment(current))
    return lower, upper, segments


def _segment(points: list[Row]) -> Row:
    return {
        "reference_point_ids": [point["id"] for point in points],
        "reference_bp_range": [points[0]["reference_bp"], points[-1]["reference_bp"]],
        "calendar_bp_range": [
            min(point["calendar_bp"] for point in points),
            max(point["calendar_bp"] for point in points),
        ],
    }


def _envelope(constraints: list[Row]) -> list[float] | None:
    endpoints = [
        endpoint
        for constraint in constraints
        for segment in constraint["segments"]
        for endpoint in segment["calendar_bp_range"]
    ]
    if not endpoints:
        return None
    return [min(endpoints), max(endpoints)]


def _components(deposit_ids: list[str], edges: list[Row]) -> dict[str, list[str]]:
    parent = {deposit_id: deposit_id for deposit_id in deposit_ids}

    def find(node: str) -> str:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for edge in edges:
        root_a, root_b = find(edge["earlier"]), find(edge["later"])
        if root_a != root_b:
            parent[root_b] = root_a
    grouped: dict[str, list[str]] = {}
    for deposit_id in deposit_ids:
        grouped.setdefault(find(deposit_id), []).append(deposit_id)
    return {f"component-{index}": sorted(members)
            for index, members in enumerate(
                (grouped[key] for key in sorted(grouped, key=lambda key: min(grouped[key]))), start=1)}


def _levels(deposit_ids: list[str], edges: list[Row]) -> tuple[dict[str, int], list[str]]:
    """Kahn 分层；返回各阶段层位和无法进入层序（参与成环或位于其下游）的阶段。"""
    indegree = {deposit_id: 0 for deposit_id in deposit_ids}
    forward: dict[str, list[str]] = {deposit_id: [] for deposit_id in deposit_ids}
    for edge in edges:
        indegree[edge["later"]] += 1
        forward[edge["earlier"]].append(edge["later"])
    ready = sorted(deposit_id for deposit_id in deposit_ids if indegree[deposit_id] == 0)
    levels: dict[str, int] = {}
    level = 0
    while ready:
        next_ready: list[str] = []
        for node in ready:
            levels[node] = level
        for node in ready:
            for successor in forward[node]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    next_ready.append(successor)
        ready = sorted(next_ready)
        level += 1
    cyclic = sorted(deposit_id for deposit_id in deposit_ids if deposit_id not in levels)
    return levels, cyclic


def _reach(start: str, adjacency: dict[str, list[str]]) -> set[str]:
    seen = {start}
    queue = [start]
    while queue:
        node = queue.pop()
        for neighbour in adjacency.get(node, []):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen


def _inversions(edges: list[Row], direct: dict[str, list[Row]]) -> list[Row]:
    """校准测值与可核实的先后关系无法同时成立的边。"""
    forward: dict[str, list[str]] = {deposit_id: [] for deposit_id in direct}
    reverse: dict[str, list[str]] = {deposit_id: [] for deposit_id in direct}
    for edge in edges:
        forward.setdefault(edge["earlier"], []).append(edge["later"])
        reverse.setdefault(edge["later"], []).append(edge["earlier"])
    conflicts: list[Row] = []
    for edge in edges:
        earlier_nodes = _reach(edge["earlier"], reverse)
        later_nodes = _reach(edge["later"], forward)
        earlier_envelope = _envelope([item for node in earlier_nodes for item in direct.get(node, [])])
        later_envelope = _envelope([item for node in later_nodes for item in direct.get(node, [])])
        if (
            earlier_envelope is not None
            and later_envelope is not None
            and earlier_envelope[1] < later_envelope[0]
        ):
            conflicts.append({
                "fact_id": f"conflict:inversion:{edge['id']}",
                "kind": "conflict",
                "conflict_type": "chronological-inversion",
                "relation_id": edge["id"],
                "earlier": edge["earlier"],
                "later": edge["later"],
                "deposit_ids": sorted(earlier_nodes | later_nodes),
                "detail": (
                    f"relation {edge['id']} requires {edge['earlier']} to predate "
                    f"{edge['later']}, but calibrated ranges cannot satisfy that order"
                ),
                "evidence": {
                    "relation_id": edge["id"],
                    "earlier_envelope_bp": earlier_envelope,
                    "later_envelope_bp": later_envelope,
                    "specimen_ids": sorted(
                        item["specimen_id"]
                        for node in earlier_nodes | later_nodes
                        for item in direct.get(node, [])
                    ),
                },
            })
    return conflicts


def _cycle_conflicts(cyclic: list[str], components: dict[str, list[str]], edges: list[Row]) -> list[Row]:
    conflicts = []
    for member in cyclic:
        component_id = next(key for key, members in components.items() if member in members)
        if any(conflict["component_id"] == component_id for conflict in conflicts):
            continue
        members = components[component_id]
        relation_ids = sorted(
            edge["id"] for edge in edges
            if edge["earlier"] in members and edge["later"] in members
        )
        conflicts.append({
            "fact_id": f"conflict:cycle:{component_id}",
            "kind": "conflict",
            "component_id": component_id,
            "conflict_type": "stratigraphic-cycle",
            "deposit_ids": members,
            "detail": f"field interpretations in {component_id} contain a directional cycle",
            "evidence": {"relation_ids": relation_ids},
        })
    return conflicts


def _state(loaded: dict[str, object], active_reviews: set[str]) -> Row:
    relations: dict[str, Row] = loaded["relations"]  # type: ignore[assignment]
    specimens: dict[str, Row] = loaded["specimens"]  # type: ignore[assignment]
    points: list[Row] = loaded["points"]  # type: ignore[assignment]
    reviews: dict[str, Row] = loaded["reviews"]  # type: ignore[assignment]

    withdrawn = {
        review["target"]
        for review in reviews.values()
        if review["id"] in active_reviews
        and review["target_kind"] == RELATION_KIND
    }
    corrections = {
        review["target"]: review
        for review in reviews.values()
        if review["id"] in active_reviews
        and review["target_kind"] == SPECIMEN_KIND
    }

    edges = [
        {"id": relation_id, "earlier": row["earlier"], "later": row["later"]}
        for relation_id, row in relations.items()
        if relation_id not in withdrawn
    ]

    calibrations: dict[str, Row] = {}
    direct: dict[str, list[Row]] = {}
    residual: dict[str, list[Row]] = {}
    ambiguous: list[str] = []
    for specimen_id, specimen in specimens.items():
        lower, upper, segments = _calibrate(
            specimen["measured_bp"], specimen["tolerance_bp"], points)
        correction = corrections.get(specimen_id)
        effective = correction["resolution"] if correction else specimen["field_attribution"]
        calibration = {
            "specimen": specimen,
            "lower": lower,
            "upper": upper,
            "segments": segments,
            "effective": effective,
            "resolved_by": correction["id"] if correction else None,
        }
        calibrations[specimen_id] = calibration
        if effective == IN_SITU:
            direct.setdefault(specimen["deposit"], []).append(
                {"specimen_id": specimen_id, "attribution": IN_SITU, "segments": segments})
        elif effective == REWORKED:
            direct.setdefault(specimen["origin"], []).append(
                {"specimen_id": specimen_id, "attribution": REWORKED, "segments": segments})
            residual.setdefault(specimen["deposit"], []).append(
                {"specimen_id": specimen_id, "origin": specimen["origin"], "segments": segments})
        else:
            ambiguous.append(specimen_id)

    deposit_ids = sorted(loaded["deposits"])  # type: ignore[arg-type]
    components = _components(deposit_ids, edges)
    component_of = {
        deposit_id: component_id
        for component_id, members in components.items()
        for deposit_id in members
    }
    levels, cyclic = _levels(deposit_ids, edges)
    inversions = _inversions(edges, direct)
    cycle_conflicts = _cycle_conflicts(cyclic, components, edges)
    conflicts = sorted(inversions + cycle_conflicts, key=lambda row: row["fact_id"])
    return {
        "edges": edges,
        "withdrawn": withdrawn,
        "calibrations": calibrations,
        "direct": direct,
        "residual": residual,
        "ambiguous": ambiguous,
        "components": components,
        "component_of": component_of,
        "levels": levels,
        "cyclic": cyclic,
        "conflicts": conflicts,
    }


def _scenario_compatibility(
    specimen_id: str,
    attribution: str,
    target_deposit: str,
    state: Row,
) -> Row:
    """评估某件归属未定标本按指定归属时，与已确认关系能否同时成立。"""
    segments = state["calibrations"][specimen_id]["segments"]
    direct = {
        deposit_id: [dict(item) for item in constraints]
        for deposit_id, constraints in state["direct"].items()
    }
    direct.setdefault(target_deposit, []).append(
        {"specimen_id": specimen_id, "attribution": attribution, "segments": segments})
    blocked = _inversions(state["edges"], direct)
    return {
        "attribution": attribution,
        "deposit_id": target_deposit,
        "compatible": not blocked,
        "blocked_by_relation_ids": sorted(conflict["relation_id"] for conflict in blocked),
        "supported_relation_ids": sorted(
            edge["id"] for edge in state["edges"]
            if edge["earlier"] == target_deposit or edge["later"] == target_deposit
        ),
        "calendar_bp_envelope": _envelope(direct[target_deposit]),
    }


def _specimen_facts(loaded: dict[str, object], state: Row) -> list[Row]:
    facts: list[Row] = []
    for specimen_id in sorted(loaded["specimens"]):  # type: ignore[arg-type]
        specimen = loaded["specimens"][specimen_id]  # type: ignore[index]
        calibration = state["calibrations"][specimen_id]
        fact: Row = {
            "fact_id": specimen_id,
            "kind": "specimen-calibration",
            "deposit_id": specimen["deposit"],
            "field_attribution": specimen["field_attribution"],
            "effective_attribution": calibration["effective"],
            "measured_bp": specimen["measured_bp"],
            "tolerance_bp": specimen["tolerance_bp"],
            "allowed_reference_bp": [calibration["lower"], calibration["upper"]],
            "calibration_status": (
                "calibrated" if calibration["segments"] else "no-reference-point-in-tolerance"
            ),
            "segments": calibration["segments"],
        }
        if specimen["origin"] is not None:
            fact["origin_deposit_id"] = specimen["origin"]
        if calibration["resolved_by"] is not None:
            fact["resolved_by_review_id"] = calibration["resolved_by"]
        if calibration["effective"] == AMBIGUOUS:
            fact["scenarios"] = [
                _scenario_compatibility(specimen_id, IN_SITU, specimen["deposit"], state),
                _scenario_compatibility(specimen_id, REWORKED, specimen["origin"], state),
            ]
        facts.append(fact)
    return facts


def _phase_facts(loaded: dict[str, object], state: Row) -> list[Row]:
    deposits: dict[str, Row] = loaded["deposits"]  # type: ignore[assignment]
    specimens: dict[str, Row] = loaded["specimens"]  # type: ignore[assignment]
    edges = state["edges"]
    conflict_ids_by_deposit: dict[str, list[str]] = {}
    for conflict in state["conflicts"]:
        for deposit_id in conflict["deposit_ids"]:
            conflict_ids_by_deposit.setdefault(deposit_id, []).append(conflict["fact_id"])

    facts: list[Row] = []
    for deposit_id in sorted(deposits):
        incoming = sorted(edge["id"] for edge in edges if edge["later"] == deposit_id)
        outgoing = sorted(edge["id"] for edge in edges if edge["earlier"] == deposit_id)
        direct_constraints = [
            dict(item) for item in state["direct"].get(deposit_id, [])
        ]
        residual_constraints = [
            dict(item) for item in state["residual"].get(deposit_id, [])
        ]
        ambiguous_specimen_ids = sorted(
            specimen_id for specimen_id in state["ambiguous"]
            if specimens[specimen_id]["deposit"] == deposit_id
        )
        conflict_ids = sorted(conflict_ids_by_deposit.get(deposit_id, []))
        facts.append({
            "fact_id": deposit_id,
            "kind": "phase-review",
            "trench_id": deposits[deposit_id]["trench"],
            "component_id": state["component_of"][deposit_id],
            "level": state["levels"].get(deposit_id),
            "earlier_relation_ids": incoming,
            "later_relation_ids": outgoing,
            "specimen_ids": sorted(
                specimen_id for specimen_id, specimen in specimens.items()
                if specimen["deposit"] == deposit_id or specimen["origin"] == deposit_id
            ),
            "direct_constraints": direct_constraints,
            "residual_old_layer_constraints": residual_constraints,
            "ambiguous_specimen_ids": ambiguous_specimen_ids,
            "calendar_bp_envelope": _envelope(direct_constraints),
            "conflict_ids": conflict_ids,
            "support": "not-supported" if conflict_ids else "supported",
        })
    return facts


def _conflict_facts(state: Row) -> list[Row]:
    return [dict(conflict) for conflict in state["conflicts"]]


def _deposit_snapshot(deposit_id: str, state: Row) -> Row:
    direct = state["direct"].get(deposit_id, [])
    return {
        "level": state["levels"].get(deposit_id),
        "calendar_bp_envelope": _envelope(direct),
        "conflict_ids": sorted(
            conflict["fact_id"] for conflict in state["conflicts"]
            if deposit_id in conflict["deposit_ids"]
        ),
    }


def _review_change_facts(loaded: dict[str, object], current: Row) -> list[Row]:
    reviews: dict[str, Row] = loaded["reviews"]  # type: ignore[assignment]
    all_review_ids = set(reviews)
    facts: list[Row] = []
    for review_id in sorted(reviews):
        review = reviews[review_id]
        prior = _state(loaded, all_review_ids - {review_id})
        if review["target_kind"] == RELATION_KIND:
            relation = loaded["relations"][review["target"]]  # type: ignore[index]
            affected = set(current["component_of"]) & set(
                node
                for members in prior["components"].values()
                if relation["earlier"] in members or relation["later"] in members
                for node in members
            )
            evidence = [review_id, review["target"]]
        else:
            specimen = loaded["specimens"][review["target"]]  # type: ignore[index]
            affected = {specimen["deposit"]}
            if specimen["origin"] is not None:
                affected.add(specimen["origin"])
            evidence = [review_id, review["target"]]
        changes = []
        for deposit_id in sorted(affected):
            before = _deposit_snapshot(deposit_id, prior)
            after = _deposit_snapshot(deposit_id, current)
            if before != after:
                changes.append({
                    "deposit_id": deposit_id,
                    "prior_level": before["level"],
                    "current_level": after["level"],
                    "prior_calendar_bp_envelope": before["calendar_bp_envelope"],
                    "current_calendar_bp_envelope": after["calendar_bp_envelope"],
                    "prior_conflict_ids": before["conflict_ids"],
                    "current_conflict_ids": after["conflict_ids"],
                })
        facts.append({
            "fact_id": review_id,
            "kind": "review-change",
            "target_kind": review["target_kind"],
            "target_id": review["target"],
            "action": review["action"],
            "resolution": review["resolution"],
            "note": review["note"],
            "changes": changes,
            "evidence_basis": evidence,
        })
    return facts


def build_facts(data: Row) -> list[Row]:
    loaded = _load(data)
    current = _state(loaded, set(loaded["reviews"]))  # type: ignore[arg-type]
    facts = (
        _specimen_facts(loaded, current)
        + _phase_facts(loaded, current)
        + _conflict_facts(current)
        + _review_change_facts(loaded, current)
    )
    return sorted(facts, key=lambda row: row["fact_id"])
