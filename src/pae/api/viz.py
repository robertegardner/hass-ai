"""Dependency-free inline-SVG sparkbars for the proposals UI."""


def sparkbar(values: list[float], width_per_bar: int = 6, height: int = 20) -> str:
    bars = []
    for i, v in enumerate(values):
        v = max(0.0, min(1.0, v))
        h = max(1, round(v * height))
        bars.append(
            f'<rect x="{i * width_per_bar}" y="{height - h}" '
            f'width="{width_per_bar - 1}" height="{h}" />'
        )
    return (
        f'<svg class="spark" width="{len(values) * width_per_bar}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">' + "".join(bars) + "</svg>"
    )
