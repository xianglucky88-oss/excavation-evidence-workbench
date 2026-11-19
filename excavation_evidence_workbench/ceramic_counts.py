"""按出土单位统计口沿片和其他陶片。"""

from collections import defaultdict

from .contract import Row, flag, index_rows, read_rows, text


def build_facts(data: Row) -> list[Row]:
    units = defaultdict(lambda: {"rim_count": 0, "other_count": 0, "rim_ids": []})
    for key, row in index_rows(read_rows(data), "id").items():
        summary = units[text(row, "context")]
        if flag(row, "rim"):
            summary["rim_count"] += 1
            summary["rim_ids"].append(key)
        else:
            summary["other_count"] += 1
    return [{"fact_id": key, **units[key]} for key in sorted(units)]
