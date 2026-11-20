"""用踏查带归属与计数、各版本公开边界和已公布数字核对分区汇总发布草稿。

读者会把新表与已公布的分区汇总放在一起做差分。基线按以下口径核对：

- 约束只来自已公布数字；空白格仅在该表明确代表零时按零入账；
- 同名分区按各自发布版本的边界还原为小区集合，逐版本核对；
- 逐项给出统计项允许公开的最细区域：原分区、合并后的较大区域，或暂缓；
- 记录读者对限制公开小区仍可推断的计数区间；
- 被拿掉的细分若仍可由保留的总数与其他明细唯一确定，列入风险；
- 每项调整与风险注明共同起作用的公开资料。

读者视角的推断按非负整数精确求解，面向小区数量有限的分区汇总。
"""

from __future__ import annotations

from itertools import combinations

from .contract import Row, flag, index_rows, integer, read_rows, text

Constraint = tuple[frozenset, int]
Tagged = tuple[tuple[str, str], frozenset, int]


def _zone_refs(row: Row, field: str, known) -> list[str]:
    refs = row[field]
    if not isinstance(refs, list) or not refs:
        raise ValueError(f"{field} must be a nonempty list")
    if any(not isinstance(ref, str) or ref not in known for ref in refs):
        raise ValueError(f"{field} must list known zones")
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field} must not repeat zones")
    return refs


def _bound(constraints: list[Constraint], upper: dict, target: frozenset, maximize: bool) -> int:
    """在非负整数解中求 target 变量和的最优值；约束系统假定有解。"""
    zones = sorted(upper)
    index = {zone: pos for pos, zone in enumerate(zones)}
    members = [[index[zone] for zone in region] for region, _ in constraints]
    totals = [total for _, total in constraints]
    var_cons = [[] for _ in zones]
    for pos, group in enumerate(members):
        for var in group:
            var_cons[var].append(pos)
    ub = [upper[zone] for zone in zones]
    on_target = [zone in target for zone in zones]
    head = sorted((var for var in range(len(zones)) if on_target[var]), key=lambda var: (ub[var], zones[var]))
    tail = sorted((var for var in range(len(zones)) if not on_target[var]), key=lambda var: (ub[var], -len(var_cons[var]), zones[var]))
    order = head + tail
    suffix = [0] * (len(order) + 1)
    for pos in range(len(order) - 1, -1, -1):
        var = order[pos]
        suffix[pos] = suffix[pos + 1] + (ub[var] if on_target[var] else 0)
    assigned = [0] * len(constraints)
    remaining = [sum(ub[var] for var in group) for group in members]
    best = [None]

    def dfs(pos: int, obj: int) -> None:
        if best[0] is not None:
            if maximize and obj + suffix[pos] <= best[0]:
                return
            if not maximize and obj >= best[0]:
                return
        if pos == len(order):
            best[0] = obj
            return
        var = order[pos]
        span = ub[var]
        low, high = 0, span
        for con in var_cons[var]:
            rest = remaining[con] - span
            low = max(low, totals[con] - assigned[con] - rest)
            high = min(high, totals[con] - assigned[con])
        if on_target[var] and maximize:
            values = range(high, low - 1, -1)
        else:
            values = range(low, high + 1)
        for value in values:
            for con in var_cons[var]:
                assigned[con] += value
                remaining[con] -= span
            dfs(pos + 1, obj + (value if on_target[var] else 0))
            for con in var_cons[var]:
                assigned[con] -= value
                remaining[con] += span

    dfs(0, 0)
    if best[0] is None:
        raise ValueError("constraints are inconsistent")
    return best[0]


def _infer(constraints: list[Constraint], target) -> tuple[int, int | None]:
    """读者由约束可推断的 target 计数之和区间；上界为 None 表示无界。"""
    target = frozenset(target)
    upper = {}
    for region, total in constraints:
        for zone in region:
            upper[zone] = min(total, upper[zone]) if zone in upper else total
    free = target - upper.keys()
    if not upper:
        return 0, (None if free else 0)
    low = _bound(constraints, upper, target, maximize=False)
    high = None if free else _bound(constraints, upper, target, maximize=True)
    return low, high


def _minimal_sources(tagged: list[Tagged], target) -> list[frozenset]:
    """共同唯一确定 target 的最小来源子集集合。"""
    sources = sorted({tag for tag, _, _ in tagged})

    def determines(subset) -> bool:
        constraints = [(region, value) for tag, region, value in tagged if tag in subset]
        low, high = _infer(constraints, target)
        return high is not None and low == high

    if len(sources) > 13:
        keep = list(sources)
        for source in list(keep):
            trial = [other for other in keep if other != source]
            if determines(trial):
                keep = trial
        return [frozenset(keep)]
    found = []
    for size in range(1, len(sources) + 1):
        for combo in combinations(sources, size):
            subset = frozenset(combo)
            if any(subset >= known for known in found):
                continue
            if determines(subset):
                found.append(subset)
    return found


def build_facts(data: Row) -> list[Row]:
    zones = index_rows(read_rows(data, "zones"), "id")
    access = {}
    for zone_id, row in zones.items():
        level = text(row, "access")
        if level not in ("public", "restricted"):
            raise ValueError(f"access of {zone_id} must be public or restricted")
        access[zone_id] = level

    tally = {zone_id: 0 for zone_id in zones}
    for strip_id, row in index_rows(read_rows(data, "strips"), "id").items():
        zone = text(row, "zone")
        if zone not in zones:
            raise ValueError(f"strip {strip_id} references unknown zone {zone}")
        tally[zone] += integer(row, "finds")

    versions = {}
    for row in read_rows(data, "boundaries"):
        version = text(row, "version")
        if version in versions:
            raise ValueError(f"duplicate boundary version: {version}")
        zone_map = {}
        for zone_row in read_rows(row, "zones"):
            name = text(zone_row, "name")
            if name in zone_map:
                raise ValueError(f"duplicate zone {name} in {version}")
            zone_map[name] = frozenset(_zone_refs(zone_row, "small_zones", zones))
        group_map = {}
        groups = row.get("groups", [])
        if not isinstance(groups, list) or any(not isinstance(group, dict) for group in groups):
            raise ValueError(f"groups of {version} must contain JSON objects")
        for group_row in groups:
            name = text(group_row, "name")
            if name in zone_map or name in group_map:
                raise ValueError(f"duplicate region {name} in {version}")
            members = _zone_refs(group_row, "zones", zone_map)
            group_map[name] = frozenset().union(*(zone_map[member] for member in members))
        versions[version] = {"zones": zone_map, "groups": group_map}

    def region_sum(region) -> int:
        return sum(tally[zone] for zone in region)

    published: list[Tagged] = []
    for table_id, row in index_rows(read_rows(data, "published"), "id").items():
        version = text(row, "boundary")
        if version not in versions:
            raise ValueError(f"{table_id} references unknown boundary {version}")
        blank_zero = flag(row, "blank_means_zero")
        zone_map = versions[version]["zones"]
        seen = set()
        for entry in read_rows(row, "rows"):
            name = text(entry, "zone")
            if name in seen:
                raise ValueError(f"duplicate row {name} in {table_id}")
            seen.add(name)
            if name not in zone_map:
                raise ValueError(f"{table_id} row {name} is not a zone of {version}")
            region = zone_map[name]
            if entry["finds"] is None:
                if not blank_zero:
                    continue
                if region_sum(region) != 0:
                    raise ValueError(f"{table_id} blank row {name} conflicts with strip tally")
                published.append((("table", table_id), region, 0))
                continue
            value = integer(entry, "finds")
            if value != region_sum(region):
                raise ValueError(f"{table_id} row {name} does not match strip tally")
            published.append((("table", table_id), region, value))

    draft = data["draft"]
    if not isinstance(draft, dict):
        raise ValueError("draft must be a JSON object")
    draft_version = text(draft, "boundary")
    if draft_version not in versions:
        raise ValueError(f"draft references unknown boundary {draft_version}")
    zone_map = versions[draft_version]["zones"]
    group_map = versions[draft_version]["groups"]
    order = []
    regions = {}
    proposed = {}
    for entry in read_rows(draft, "rows"):
        name = text(entry, "zone")
        if name in proposed:
            raise ValueError(f"duplicate draft row {name}")
        if name not in zone_map:
            raise ValueError(f"draft row {name} is not a zone of {draft_version}")
        order.append(name)
        regions[name] = zone_map[name]
        if entry["finds"] is None:
            proposed[name] = None
            continue
        value = integer(entry, "finds")
        if value != region_sum(zone_map[name]):
            raise ValueError(f"draft row {name} does not match strip tally")
        proposed[name] = value

    restricted = sorted(zone for zone, level in access.items() if level == "restricted")
    restricted_set = set(restricted)
    base_constraints = [(region, value) for _, region, value in published]
    live = {
        name: {"region": regions[name], "label": name, "value": proposed[name]}
        for name in order
        if proposed[name] is not None
    }
    withheld = {name for name in order if proposed[name] is None}
    provenance = {name: {"public_sources": set(), "joint_items": set()} for name in live}
    deferred = {}
    pre_existing = set()

    def tagged_constraints() -> list[Tagged]:
        return published + [
            (("draft", name), item["region"], item["value"]) for name, item in live.items()
        ]

    while True:
        tagged = tagged_constraints()
        constraints = [(region, value) for _, region, value in tagged]
        violated = []
        for zone in restricted:
            low, high = _infer(constraints, (zone,))
            if high is None or low != high:
                continue
            base_low, base_high = _infer(base_constraints, (zone,))
            if base_high is not None and base_low == base_high:
                pre_existing.add(zone)
            else:
                violated.append(zone)
        if not violated:
            break
        candidates = {}
        for zone in violated:
            for subset in _minimal_sources(tagged, (zone,)):
                tables = {tag[1] for tag in subset if tag[0] == "table"}
                items = {tag[1] for tag in subset if tag[0] == "draft"}
                for name in items:
                    entry = candidates.setdefault(name, {"public_sources": set(), "joint_items": set()})
                    entry["public_sources"] |= tables
                    entry["joint_items"] |= items - {name}
        pick = min(candidates, key=lambda name: (
            0 if live[name]["region"] & restricted_set else 1,
            len(live[name]["region"]),
            name,
        ))
        provenance[pick]["public_sources"] |= candidates[pick]["public_sources"]
        provenance[pick]["joint_items"] |= candidates[pick]["joint_items"]
        others = {item["region"] for name, item in live.items() if name != pick}
        widened = None
        for label, region in sorted(group_map.items(), key=lambda item: (len(item[1]), item[0])):
            if region > live[pick]["region"] and region not in others:
                widened = (label, region)
                break
        if widened is None:
            deferred[pick] = provenance.pop(pick)
            del live[pick]
        else:
            label, region = widened
            live[pick] = {"region": region, "label": label, "value": region_sum(region)}

    tagged = tagged_constraints()
    constraints = [(region, value) for _, region, value in tagged]

    items = []
    for name in order:
        if name in live:
            item = live[name]
            prov = provenance[name]
            items.append({
                "fact_id": f"item:{name}",
                "item": name,
                "status": "adjusted" if item["region"] != regions[name] else "publish",
                "region_label": item["label"],
                "small_zones": sorted(item["region"]),
                "value": item["value"],
                "public_sources": sorted(prov["public_sources"]),
                "joint_items": sorted(prov["joint_items"]),
            })
        elif name in deferred:
            prov = deferred[name]
            items.append({
                "fact_id": f"item:{name}",
                "item": name,
                "status": "deferred",
                "region_label": None,
                "small_zones": None,
                "value": None,
                "public_sources": sorted(prov["public_sources"]),
                "joint_items": sorted(prov["joint_items"]),
            })
        else:
            items.append({
                "fact_id": f"item:{name}",
                "item": name,
                "status": "withheld",
                "region_label": None,
                "small_zones": None,
                "value": None,
                "public_sources": [],
                "joint_items": [],
            })

    inferences = []
    for zone in restricted:
        low, high = _infer(constraints, (zone,))
        exact = high is not None and low == high
        sources = set()
        if exact:
            for subset in _minimal_sources(tagged, (zone,)):
                sources |= {tag[1] for tag in subset if tag[0] == "table"}
        inferences.append({
            "fact_id": f"inference:{zone}",
            "zone": zone,
            "reader_range": [low, high],
            "exact": exact,
            "pre_existing": zone in pre_existing,
            "public_sources": sorted(sources),
        })

    risks = []
    for name in [name for name in order if name in withheld] + sorted(deferred):
        target = tuple(sorted(regions[name]))
        low, high = _infer(constraints, target)
        if high is None or low != high:
            continue
        tables = set()
        partners = set()
        for subset in _minimal_sources(tagged, target):
            tables |= {tag[1] for tag in subset if tag[0] == "table"}
            partners |= {tag[1] for tag in subset if tag[0] == "draft"}
        risks.append({
            "fact_id": f"risk:{name}",
            "item": name,
            "small_zones": sorted(regions[name]),
            "determined_value": low,
            "public_sources": sorted(tables),
            "joint_items": sorted(partners),
        })

    summary = {
        "fact_id": "summary",
        "publishable": [
            {"item": name, "region_label": live[name]["label"], "value": live[name]["value"]}
            for name in order
            if name in live
        ],
        "deferred": [name for name in order if name in deferred],
        "withheld": [name for name in order if name in withheld],
    }
    return items + inferences + risks + [summary]
