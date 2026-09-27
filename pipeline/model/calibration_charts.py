"""The calibrate step's chart: how often actual scores landed inside the model's central 80% interval."""

from pathlib import Path

import seaborn
from matplotlib import style
from matplotlib.figure import Figure

from pipeline import charts

NOMINAL_COVERAGE = 0.80


def coverage_chart(images_dir: Path, week: int, player_coverage: dict[str, float], team_coverage: float | None) -> str:
    """One bar per position and ALL, then a team bar when any team-week was scored, against the nominal 0.80."""
    coverage = dict(player_coverage)
    if team_coverage is not None:
        coverage["Team"] = team_coverage
    with style.context(charts.NOTEBOOK_STYLE):
        figure = Figure(figsize=(12, 6))
        ax = figure.subplots()
        bars = ax.bar(
            list(coverage),
            list(coverage.values()),
            color=seaborn.color_palette("husl", len(coverage)),
            alpha=0.85,
            width=0.6,
        )
        ax.bar_label(bars, fmt="%.2f", padding=3)
        ax.axhline(NOMINAL_COVERAGE, color="black", linestyle="--", linewidth=1.5, label="Nominal 0.80")
        ax.set_title(f"Season-to-Date 80% Interval Coverage - Week {week}", fontsize=14, fontweight="bold", pad=15)
        ax.set_ylabel("Share of actual scores inside the interval", fontsize=12)
        ax.set_ylim(0, 1.05)
        ax.legend(loc="lower right")
        figure.tight_layout()
        return charts.save(figure, images_dir, f"calibration_week_{week}.png")
