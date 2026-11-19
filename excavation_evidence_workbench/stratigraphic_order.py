"""从已确认的早晚方向形成地层关系与分层次序。"""

from graphlib import CycleError, TopologicalSorter

from .contract import Row, index_rows, read_rows, text


def build_facts(data: Row) -> list[Row]:
    deposits = index_rows(read_rows(data, "deposits"), "id")
    graph = TopologicalSorter({key: () for key in sorted(deposits)})
    edges = {key: [] for key in deposits}
    for reference, row in index_rows(read_rows(data), "id").items():
        earlier, later = text(row, "earlier"), text(row, "later")
        if earlier not in deposits or later not in deposits:
            raise ValueError(f"unknown deposit in {reference}")
        graph.add(later, earlier)
        edges[later].append(reference)
    try:
        graph.prepare()
    except CycleError as error:
        raise ValueError("stratigraphic relationships contain a cycle") from error
    result = []
    level = 0
    while graph.is_active():
        ready = sorted(graph.get_ready())
        result.extend({"fact_id": key, "level": level, "earlier_relation_ids": sorted(edges[key])} for key in ready)
        graph.done(*ready)
        level += 1
    return result
