"""The calibrate step's chart: how often actual scores landed inside the model's central 80% interval."""

from pathlib import Path

import numpy as np
from matplotlib import style
from matplotlib.figure import Figure

from pipeline import charts

NOMINAL_COVERAGE = 0.80
GROUP_WIDTH = 0.8


def coverage_chart(
    images_dir: Path,
    week: int,
    player_coverage: dict[str, float],
    starter_coverage: dict[str, float],
    team_coverage: float | None,
) -> str:
    """Two bars per position and ALL, every eligible player-week and the starters among them, then a team bar when
    any team-week was scored, against the nominal 0.80. A position without starters has no starter bar."""
    groups = list(player_coverage)
    positions = np.arange(len(groups))
    starters = [starter_coverage.get(group, np.nan) for group in groups]
    width = GROUP_WIDTH / 2
    with style.context(charts.NOTEBOOK_STYLE):
        figure = Figure(figsize=(12, 6))
        ax = figure.subplots()
        bars = [
            ax.bar(positions - width / 2, list(player_coverage.values()), width, label="Every eligible player-week"),
            ax.bar(positions + width / 2, starters, width, label="Starters"),
        ]
        labels = groups
        if team_coverage is not None:
            bars.append(ax.bar([len(groups)], [team_coverage], width, label="Team totals"))
            labels = [*groups, "Team"]
        for container in bars:
            ax.bar_label(container, fmt="%.2f", padding=3)
        ax.axhline(NOMINAL_COVERAGE, color="black", linestyle="--", linewidth=1.5, label="Nominal 0.80")
        ax.set_xticks(np.arange(len(labels)), labels=labels)
        ax.set_title(f"Season-to-Date 80% Interval Coverage - Week {week}", fontsize=14, fontweight="bold", pad=15)
        ax.set_ylabel("Share of actual scores inside the interval", fontsize=12)
        ax.set_ylim(0, 1.05)
        # Below the axis, clear of the bars.
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.07), ncol=len(bars) + 1)
        ax.grid(axis="x", visible=False)
        figure.tight_layout()
        return charts.save(figure, images_dir, f"calibration_week_{week}.png")
