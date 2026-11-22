"""陶片清洗改号后的双时间轴核对。

每条业务记录同时携带两个日期：

* ``date`` —— 业务生效日（valid time，登记、改号、移袋、确认等实际发生的日子）；
* ``received_date`` —— 资料组收到该记录的系统日（transaction time，决定某一出具日
  能够看到哪些资料）。

后来补录或更正的记录可能拥有较早的 ``date`` 和较晚的 ``received_date``，因此：

* 按出具当日所收资料的「原认定」只纳入 ``received_date <= 出具日`` 的记录；
* 按盘点截止日的「最新认定」纳入当前收到的全部记录，再按 ``date`` 回放至截止日。

物理陶片以首次登记产生的 ``sherd_id`` 为稳定身份；改号只增加同一身份的编号别名，
所以同一陶片的两个编号不会重复入数。移袋只改保管位置，不改变出土单位（context）。
器物组跨越出土单位时整批只计一个组，每个涉及单位仍保留本单位实物证据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .contract import Row, read_rows, text

REGISTRATIONS = "registrations"
RENUMBERS = "renumbers"
BAG_MOVES = "bag_moves"
GROUP_CONFIRMATIONS = "group_confirmations"
GROUP_WITHDRAWALS = "group_withdrawals"
REVIEWS = "reviews"

EVENT_KINDS = (
    REGISTRATIONS,
    RENUMBERS,
    BAG_MOVES,
    GROUP_CONFIRMATIONS,
    GROUP_WITHDRAWALS,
    REVIEWS,
)

_CONTEXT_METRICS = (
    "sherd_count",
    "rim_count",
    "rim_sherd_ids",
    "confirmed_group_count",
    "group_evidence",
    "pending_sherd_ids",
    "pending_count",
    "in_review_sherd_ids",
    "bag_index",
    "current_numbers",
)
_BATCH_METRICS = (
    "sherd_count",
    "rim_count",
    "confirmed_group_count",
    "cross_context_group_ids",
    "pending_sherd_ids",
    "in_review_sherd_ids",
    "contexts",
)


def _iso_date(value: Any, *, field_name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO date string (YYYY-MM-DD)")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO date string (YYYY-MM-DD)") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} must use canonical YYYY-MM-DD form")
    return parsed


def _boolean(row: Row, field_name: str) -> bool:
    value = row[field_name]
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be true or false")
    return value


def _record_list(data: Row, field_name: str) -> list[Row]:
    if field_name not in data:
        return []
    return read_rows(data, field_name)


@dataclass(frozen=True)
class Event:
    kind: str
    event_id: str
    date: date
    received: date
    payload: Row


@dataclass
class Sherd:
    sherd_id: str
    context: str
    rim: bool
    initial_number: str
    initial_bag: str
    registration_id: str
    registered_on: date
    held_by: str | None = None  # 最近一次仍生效的 hold 复核事件 id
    group_id: str | None = None  # 最近一次（含已撤回）确认组


@dataclass
class Group:
    group_id: str
    confirmation: Event
    member_ids: list[str]
    withdrawn: bool = False
    withdrawal_id: str | None = None


@dataclass
class State:
    horizon: date  # 有效时间截止日（盘点截止日）
    system_horizon: date  # 系统时间截止日（资料收到日）
    sherds: dict[str, Sherd]
    groups: dict[str, Group]
    events: list[Event]
    query_id: str
    mode: str

    def active_groups(self) -> dict[str, Group]:
        return {gid: group for gid, group in self.groups.items() if not group.withdrawn}

    def renumber_events(self, sherd_id: str) -> list[Event]:
        return sorted(
            (
                event
                for event in self.events
                if event.kind == RENUMBERS and text(event.payload, "sherd_id") == sherd_id
            ),
            key=lambda event: (event.date, event.received, event.event_id),
        )

    def move_events(self, sherd_id: str) -> list[Event]:
        return sorted(
            (
                event
                for event in self.events
                if event.kind == BAG_MOVES and text(event.payload, "sherd_id") == sherd_id
            ),
            key=lambda event: (event.date, event.received, event.event_id),
        )

    def number_on(self, sherd: Sherd, on_date: date) -> str:
        """按编号时间线返回该日有效的登记号（不依赖事件回放顺序）。"""
        current = sherd.initial_number
        for event in self.renumber_events(sherd.sherd_id):
            if event.date <= on_date:
                current = text(event.payload, "new_number")
        return current

    def bag_on(self, sherd: Sherd, on_date: date) -> str:
        current = sherd.initial_bag
        for event in self.move_events(sherd.sherd_id):
            if event.date <= on_date:
                current = text(event.payload, "bag_id")
        return current

    def held_sherd_ids(self) -> list[str]:
        return sorted(sherd.sherd_id for sherd in self.sherds.values() if sherd.held_by)


def _build_ledger(data: Row) -> list[Event]:
    if not isinstance(data, dict):
        raise ValueError("input must be a JSON object")
    events: list[Event] = []
    seen: dict[str, str] = {}

    for kind in EVENT_KINDS:
        for row in _record_list(data, kind):
            event_id = text(row, "id")
            if event_id in seen:
                raise ValueError(f"duplicate event id: {event_id}")
            seen[event_id] = kind
            on_date = _iso_date(row.get("date"), field_name="date")
            received = _iso_date(row.get("received_date"), field_name="received_date")
            events.append(Event(kind, event_id, on_date, received, row))

    registrations = [e for e in events if e.kind == REGISTRATIONS]
    by_sherd = {text(e.payload, "sherd_id"): e for e in registrations}
    if len(by_sherd) != len(registrations):
        raise ValueError("duplicate sherd_id in registrations")

    # 引用完整性与字段校验（在完整资料集上检查，任一截取视图都是它的前缀）。
    for event in events:
        row = event.payload
        if event.kind == REGISTRATIONS:
            text(row, "sherd_id")
            text(row, "context")
            text(row, "initial_number")
            text(row, "bag_id")
            _boolean(row, "rim")
        elif event.kind in (RENUMBERS, BAG_MOVES):
            if text(row, "sherd_id") not in by_sherd:
                raise ValueError(f"{event.kind} event {event.event_id} references unknown sherd")
            text(row, "new_number" if event.kind == RENUMBERS else "bag_id")
        elif event.kind == GROUP_CONFIRMATIONS:
            members = row["member_numbers"]
            if not isinstance(members, list) or not members or any(
                not isinstance(member, str) or not member.strip() for member in members
            ):
                raise ValueError(f"group {event.event_id} requires nonempty member_numbers")
            if len(set(members)) != len(members):
                raise ValueError(f"group {event.event_id} lists a member number twice")
        elif event.kind == GROUP_WITHDRAWALS:
            text(row, "group_id")
        elif event.kind == REVIEWS:
            text(row, "group_id")
            text(row, "sherd_number")
            action = text(row, "action")
            if action not in ("hold", "release"):
                raise ValueError(f"review {event.event_id} action must be hold or release")
            if action == "release":
                text(row, "review_id")

    confirmation_ids = {e.event_id for e in events if e.kind == GROUP_CONFIRMATIONS}
    for event in events:
        if event.kind in (GROUP_WITHDRAWALS, REVIEWS):
            target = text(event.payload, "group_id")
            if target not in confirmation_ids:
                raise ValueError(f"{event.kind} event {event.event_id} references unknown group {target}")

    # 撤回与复核必须不早于它所针对的确认被收到。
    received_order = {event.event_id: event.received for event in events}
    for event in events:
        if event.kind in (GROUP_WITHDRAWALS, REVIEWS):
            target = text(event.payload, "group_id")
            if event.received < received_order[target]:
                raise ValueError(f"event {event.event_id} received before its group confirmation")

    release_refs = {
        text(e.payload, "review_id")
        for e in events
        if e.kind == REVIEWS and text(e.payload, "action") == "release"
    }
    hold_ids = {
        e.event_id
        for e in events
        if e.kind == REVIEWS and text(e.payload, "action") == "hold"
    }
    unknown_holds = release_refs - hold_ids
    if unknown_holds:
        raise ValueError(f"release references unknown review: {sorted(unknown_holds)[0]}")
    return events


def _build_state(events: list[Event], query: Row) -> State:
    query_id = text(query, "id")
    mode = text(query, "mode")
    if mode not in ("original", "latest"):
        raise ValueError(f"query {query_id} mode must be original or latest")
    query_date = _iso_date(query.get("date"), field_name="date")
    if mode == "original":
        system_horizon = _iso_date(query.get("received_date"), field_name="received_date")
    else:
        if query.get("received_date") is not None:
            raise ValueError("latest-mode queries must not pin received_date")
        system_horizon = max((event.received for event in events), default=query_date)

    visible = [event for event in events if event.received <= system_horizon]
    effective = sorted(
        (event for event in visible if event.date <= query_date),
        key=lambda event: (event.date, event.received, event.event_id),
    )

    sherds: dict[str, Sherd] = {}
    for event in effective:
        if event.kind != REGISTRATIONS:
            continue
        row = event.payload
        sherds[text(row, "sherd_id")] = Sherd(
            sherd_id=text(row, "sherd_id"),
            context=text(row, "context"),
            rim=_boolean(row, "rim"),
            initial_number=text(row, "initial_number"),
            initial_bag=text(row, "bag_id"),
            registration_id=event.event_id,
            registered_on=event.date,
        )

    groups: dict[str, Group] = {}
    state = State(
        horizon=query_date,
        system_horizon=system_horizon,
        sherds=sherds,
        groups=groups,
        events=effective,
        query_id=query_id,
        mode=mode,
    )

    def lookup_number(number: str, on_date: date, *, what: str) -> str:
        matches = [
            sherd.sherd_id for sherd in sherds.values()
            if state.number_on(sherd, on_date) == number
        ]
        if not matches:
            raise ValueError(f"{what} resolves number {number} that is not registered as of {on_date}")
        if len(matches) > 1:
            raise ValueError(f"number {number} is ambiguous as of {on_date}: {sorted(matches)}")
        return matches[0]

    for event in effective:
        row = event.payload
        if event.kind == GROUP_CONFIRMATIONS:
            member_ids = [
                lookup_number(number, event.date, what=f"group {event.event_id}")
                for number in row["member_numbers"]
            ]
            groups[event.event_id] = Group(event.event_id, event, member_ids)
            for sherd_id in member_ids:
                sherds[sherd_id].group_id = event.event_id
        elif event.kind == GROUP_WITHDRAWALS:
            target = text(row, "group_id")
            if target not in groups:
                raise ValueError(
                    f"withdrawal {event.event_id} arrives before its group confirmation is effective"
                )
            groups[target].withdrawn = True
            groups[target].withdrawal_id = event.event_id
        elif event.kind == REVIEWS:
            target = text(row, "group_id")
            if target not in groups:
                raise ValueError(
                    f"review {event.event_id} arrives before its group confirmation is effective"
                )
            sherd_id = lookup_number(
                text(row, "sherd_number"), event.date, what=f"review {event.event_id}"
            )
            if text(row, "action") == "hold":
                sherds[sherd_id].held_by = event.event_id
            else:
                sherds[sherd_id].held_by = None

    # 同一物理陶片不得同时落在两个有效器物组里。
    placement: dict[str, str] = {}
    for group in groups.values():
        if group.withdrawn:
            continue
        for sherd_id in group.member_ids:
            if sherd_id in placement:
                raise ValueError(
                    f"sherd {sherd_id} belongs to two active groups: {placement[sherd_id]} and {group.group_id}"
                )
            placement[sherd_id] = group.group_id
    return state


def _context_inventory_map(state: State) -> dict[str, Row]:
    active = state.active_groups()
    result: dict[str, Row] = {}
    contexts = sorted({sherd.context for sherd in state.sherds.values()})
    for context in contexts:
        members = sorted(
            (sherd for sherd in state.sherds.values() if sherd.context == context),
            key=lambda sherd: sherd.sherd_id,
        )
        rim = [sherd for sherd in members if sherd.rim]

        grouped: set[str] = set()
        group_evidence = []
        for group_id in sorted(active):
            group = active[group_id]
            local = sorted(sid for sid in group.member_ids if state.sherds[sid].context == context)
            if not local:
                continue
            grouped.update(local)
            held_local = sorted(sid for sid in local if state.sherds[sid].held_by)
            group_evidence.append({
                "group_id": group_id,
                "member_sherd_ids": local,
                "held_sherd_ids": held_local,
                "effective_member_count": len(local) - len(held_local),
                "cross_context": len({state.sherds[sid].context for sid in group.member_ids}) > 1,
            })

        pending = sorted(sherd.sherd_id for sherd in members if sherd.sherd_id not in grouped)
        in_review = sorted(sherd.sherd_id for sherd in members if sherd.held_by)
        bag_index: dict[str, list[str]] = {}
        for sherd in members:
            bag_index.setdefault(state.bag_on(sherd, state.horizon), []).append(sherd.sherd_id)

        result[context] = {
            "fact_id": f"{state.query_id}:context:{context}",
            "query_id": state.query_id,
            "mode": state.mode,
            "context": context,
            "sherd_count": len(members),
            "rim_count": len(rim),
            "rim_sherd_ids": [sherd.sherd_id for sherd in rim],
            "confirmed_group_count": len(group_evidence),
            "group_evidence": group_evidence,
            "pending_sherd_ids": pending,
            "pending_count": len(pending),
            "in_review_sherd_ids": in_review,
            "bag_index": {bag: sorted(ids) for bag, ids in sorted(bag_index.items())},
            "current_numbers": {
                sherd.sherd_id: state.number_on(sherd, state.horizon) for sherd in members
            },
        }
    return result


def _group_fact(state: State, group: Group) -> Row:
    contexts = sorted({state.sherds[sid].context for sid in group.member_ids})
    held = sorted(sid for sid in group.member_ids if state.sherds[sid].held_by)
    return {
        "fact_id": f"{state.query_id}:group:{group.group_id}",
        "query_id": state.query_id,
        "mode": state.mode,
        "group_id": group.group_id,
        "status": "withdrawn" if group.withdrawn else "confirmed",
        "contexts": contexts,
        "cross_context": len(contexts) > 1,
        "member_sherd_ids": sorted(group.member_ids),
        "held_sherd_ids": held,
        "effective_member_count": len(group.member_ids) - len(held),
        "confirmation_id": group.confirmation.event_id,
        "withdrawal_id": group.withdrawal_id,
    }


def _batch_fact(state: State) -> Row:
    active = state.active_groups()
    grouped = {sid for group in active.values() for sid in group.member_ids}
    contexts = sorted({sherd.context for sherd in state.sherds.values()})
    return {
        "fact_id": f"{state.query_id}:batch",
        "query_id": state.query_id,
        "mode": state.mode,
        "contexts": contexts,
        "sherd_count": len(state.sherds),
        "rim_count": sum(1 for sherd in state.sherds.values() if sherd.rim),
        "confirmed_group_count": len(active),
        "cross_context_group_ids": sorted(
            gid for gid, group in active.items()
            if len({state.sherds[sid].context for sid in group.member_ids}) > 1
        ),
        "pending_sherd_ids": sorted(
            sherd.sherd_id for sherd in state.sherds.values() if sherd.sherd_id not in grouped
        ),
        "in_review_sherd_ids": state.held_sherd_ids(),
    }


def _ledger_fact(state: State) -> Row:
    return {
        "fact_id": f"{state.query_id}:ledger",
        "query_id": state.query_id,
        "mode": state.mode,
        "valid_horizon": state.horizon.isoformat(),
        "system_horizon": state.system_horizon.isoformat(),
        "record_ids": [event.event_id for event in state.events],
        "records": [
            {
                "id": event.event_id,
                "kind": event.kind,
                "date": event.date.isoformat(),
                "received_date": event.received.isoformat(),
            }
            for event in state.events
        ],
    }


def _query_facts(state: State) -> list[Row]:
    facts = [_ledger_fact(state), _batch_fact(state)]
    facts.extend(_context_inventory_map(state).values())
    facts.extend(_group_fact(state, group) for _, group in sorted(state.groups.items()))
    return facts


def _causal_records(base: State, target: State, sherd_ids: set[str], group_ids: set[str]) -> list[str]:
    """找出目标视图中导致差异的新增登记/复核记录（原认定视图里尚不存在）。"""
    base_ids = {event.event_id for event in base.events}
    touched = set()
    for event in target.events:
        if event.event_id in base_ids:
            continue
        row = event.payload
        if event.kind in (REGISTRATIONS, RENUMBERS, BAG_MOVES):
            if text(row, "sherd_id") in sherd_ids:
                touched.add(event.event_id)
        elif event.kind == GROUP_CONFIRMATIONS:
            members = set(target.groups[event.event_id].member_ids)
            if event.event_id in group_ids or members & sherd_ids:
                touched.add(event.event_id)
        elif event.kind in (GROUP_WITHDRAWALS, REVIEWS):
            target_group = text(row, "group_id")
            members = set(target.groups[target_group].member_ids) if target_group in target.groups else set()
            if target_group in group_ids or members & sherd_ids:
                touched.add(event.event_id)
    return sorted(touched)


def _diff_facts(base: State, target: State, pair: Row) -> list[Row]:
    pair_id = text(pair, "id")
    base_batch = _batch_fact(base)
    target_batch = _batch_fact(target)
    facts: list[Row] = []

    changed_batch = [m for m in _BATCH_METRICS if base_batch[m] != target_batch[m]]
    if changed_batch:
        changed_sherds = set(base_batch["pending_sherd_ids"]) ^ set(target_batch["pending_sherd_ids"])
        changed_sherds |= set(base_batch["in_review_sherd_ids"]) ^ set(target_batch["in_review_sherd_ids"])
        changed_groups = set(base_batch["cross_context_group_ids"]) ^ set(
            target_batch["cross_context_group_ids"]
        )
        facts.append({
            "fact_id": f"{pair_id}:diff:batch",
            "diff_id": pair_id,
            "scope": "batch",
            "base_query_id": base.query_id,
            "target_query_id": target.query_id,
            "changed_fields": changed_batch,
            "before": {m: base_batch[m] for m in changed_batch},
            "after": {m: target_batch[m] for m in changed_batch},
            "causal_record_ids": _causal_records(base, target, changed_sherds, changed_groups),
        })

    base_contexts = _context_inventory_map(base)
    target_contexts = _context_inventory_map(target)
    for context in sorted(set(base_contexts) | set(target_contexts)):
        before = base_contexts.get(context)
        after = target_contexts.get(context)
        changed = [
            metric
            for metric in _CONTEXT_METRICS
            if before is None or after is None or before[metric] != after[metric]
        ]
        if not changed:
            continue
        changed_sherds: set[str] = set()
        changed_groups: set[str] = set()
        if before is not None and after is not None:
            for list_metric in ("rim_sherd_ids", "pending_sherd_ids", "in_review_sherd_ids"):
                changed_sherds |= set(before[list_metric]) ^ set(after[list_metric])
            before_evidence = {g["group_id"]: g for g in before["group_evidence"]}
            after_evidence = {g["group_id"]: g for g in after["group_evidence"]}
            changed_groups |= set(before_evidence) ^ set(after_evidence)
            for group_id in set(before_evidence) & set(after_evidence):
                changed_sherds |= set(before_evidence[group_id]["member_sherd_ids"]) ^ set(
                    after_evidence[group_id]["member_sherd_ids"]
                )
            changed_sherds |= set(before["current_numbers"]) ^ set(after["current_numbers"])
            changed_sherds |= {
                sherd_id
                for sherd_id in set(before["current_numbers"]) & set(after["current_numbers"])
                if before["current_numbers"][sherd_id] != after["current_numbers"][sherd_id]
            }

            def _bag_members(index: Row) -> dict[str, str]:
                return {sherd_id: bag for bag, ids in index.items() for sherd_id in ids}

            before_bags = _bag_members(before["bag_index"])
            after_bags = _bag_members(after["bag_index"])
            changed_sherds |= {
                sherd_id
                for sherd_id in set(before_bags) & set(after_bags)
                if before_bags[sherd_id] != after_bags[sherd_id]
            }
        elif after is not None:
            changed_sherds |= set(after["rim_sherd_ids"]) | set(after["pending_sherd_ids"])
            changed_groups |= {g["group_id"] for g in after["group_evidence"]}
        facts.append({
            "fact_id": f"{pair_id}:diff:context:{context}",
            "diff_id": pair_id,
            "scope": "context",
            "context": context,
            "base_query_id": base.query_id,
            "target_query_id": target.query_id,
            "changed_fields": changed,
            "before": None if before is None else {m: before[m] for m in changed},
            "after": None if after is None else {m: after[m] for m in changed},
            "causal_record_ids": _causal_records(base, target, changed_sherds, changed_groups),
        })
    return facts


def build_facts(data: Row) -> list[Row]:
    events = _build_ledger(data)
    queries = _record_list(data, "queries")
    if not queries:
        raise ValueError("at least one query is required")
    states: dict[str, State] = {}
    for query in queries:
        query_id = text(query, "id")
        if query_id in states:
            raise ValueError(f"duplicate query id: {query_id}")
        states[query_id] = _build_state(events, query)

    facts: list[Row] = []
    for query_id in sorted(states):
        facts.extend(_query_facts(states[query_id]))

    for pair in _record_list(data, "diff_pairs"):
        pair_id = text(pair, "id")
        base_id = text(pair, "base_query_id")
        target_id = text(pair, "target_query_id")
        if base_id not in states or target_id not in states:
            raise ValueError(f"diff pair {pair_id} references unknown query")
        facts.extend(_diff_facts(states[base_id], states[target_id], pair))

    facts.sort(key=lambda fact: fact["fact_id"])
    return facts
