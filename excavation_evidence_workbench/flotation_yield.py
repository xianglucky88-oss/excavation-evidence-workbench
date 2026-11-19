"""计算一份已完整鉴定的浮选组分每升处理土的计数。"""

from .contract import Row, apply_rows, integer, number, text


def build_facts(data: Row) -> list[Row]:
    def summarize(row: Row) -> Row:
        volume = number(row, "processed_liters", minimum=0)
        if volume == 0:
            raise ValueError("processed_liters must be positive")
        count = integer(row, "seed_count")
        return {"fraction": text(row, "fraction"), "processed_liters": volume,
                "count": count, "seeds_per_liter": round(count / volume, 6)}

    return apply_rows(data, summarize)
