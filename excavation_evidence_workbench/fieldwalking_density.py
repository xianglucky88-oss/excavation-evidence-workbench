"""用标称样带长度和宽度计算已完整踏查样带的发现密度。"""

from .contract import Row, apply_rows, integer, number, text


def build_facts(data: Row) -> list[Row]:
    def summarize(row: Row) -> Row:
        area = number(row, "length_m", minimum=0) * number(row, "width_m", minimum=0)
        if area == 0:
            raise ValueError("transect area must be positive")
        finds = integer(row, "finds")
        return {"tract": text(row, "tract"), "survey_m2": area,
                "finds": finds, "finds_per_hectare": round(finds * 10000 / area, 6)}

    return apply_rows(data, summarize)
