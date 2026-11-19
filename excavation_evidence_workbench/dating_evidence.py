"""从本地参考曲线候选点中保存测年值的最近参考点和残差。"""

from .contract import Row, apply_rows, index_rows, number, read_rows


def build_facts(data: Row) -> list[Row]:
    curve = index_rows(read_rows(data, "reference_points"), "id")
    if not curve:
        raise ValueError("a reference curve is required")
    references = [(key, number(row, "reference_bp", minimum=0), number(row, "calendar_bp"))
                  for key, row in curve.items()]

    def locate(row: Row) -> Row:
        age = number(row, "measured_bp", minimum=0)
        key, expected, calendar = min(references, key=lambda point: (abs(point[1] - age), point[0]))
        return {"reference": key, "calendar_bp_at_nearest_point": calendar,
                "residual_bp": round(age - expected, 6)}

    return apply_rows(data, locate)
