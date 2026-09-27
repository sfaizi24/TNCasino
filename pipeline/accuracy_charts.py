"""Accuracy charts for the analytics page: projection error by position for each source, and each team's projected
total against its score."""

from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import style
from matplotlib.figure import Figure

from pipeline.charts import NOTEBOOK_STYLE, save

GROUP_WIDTH = 0.8


def mae_by_position(images_dir: Path, week: int, accuracy: list[dict], position_order: list[str]) -> str:
    """Grouped bars: a group per position in position_order, a bar per source, the most accurate overall first.
    The last entry of position_order is the all-positions group, which orders the sources."""
    mae = pd.DataFrame(accuracy).pivot(index="position", columns="source", values="mae")
    positions = [position for position in position_order if position in mae.index]
    sources = mae.loc[position_order[-1]].sort_values().index
    groups = np.arange(len(positions))
    width = GROUP_WIDTH / len(sources)
    with style.context(NOTEBOOK_STYLE):
        figure = Figure(figsize=(14, 7))
        ax = figure.subplots()
        for offset, source in enumerate(sources):
            left_edge = groups - GROUP_WIDTH / 2 + offset * width
            ax.bar(left_edge, mae.loc[positions, source], width, align="edge", label=source, edgecolor="black")
        ax.set_xticks(groups, labels=positions)
        ax.set_ylabel("Mean absolute error (points)", fontsize=12)
        ax.set_title(f"Projection Error by Position - Week {week}", fontsize=14, fontweight="bold", pad=15)
        ax.legend(title="Most accurate overall first")
        ax.grid(axis="x", visible=False)
        figure.tight_layout()
        return save(figure, images_dir, f"accuracy_mae_week_{week}.png")


def team_totals(images_dir: Path, week: int, teams: list[dict]) -> str:
    """Projected and actual points side by side for each team, highest projection first, with the simulated 10th
    to 90th percentile range drawn over the projection where the team has one."""
    ranked = pd.DataFrame(teams).sort_values("projected", ascending=False, kind="stable").reset_index(drop=True)
    positions = np.arange(len(ranked))
    width = GROUP_WIDTH / 2
    with style.context(NOTEBOOK_STYLE):
        figure = Figure(figsize=(14, 7))
        ax = figure.subplots()
        ax.bar(positions - width / 2, ranked["projected"], width, label="Projected", edgecolor="black")
        ax.bar(positions + width / 2, ranked["actual"], width, label="Actual", edgecolor="black")
        ranged = ranked.dropna(subset=["p10", "p90"])
        if not ranged.empty:
            middle = (ranged["p10"] + ranged["p90"]) / 2
            half_range = (ranged["p90"] - ranged["p10"]) / 2
            ax.errorbar(
                positions[ranged.index] - width / 2,
                middle,
                yerr=half_range,
                fmt="none",
                ecolor="black",
                capsize=5,
                label="Simulated 10th-90th percentile",
            )
        ax.set_xticks(positions, labels=ranked["owner"], rotation=45, ha="right")
        ax.set_ylabel("Points", fontsize=12)
        ax.set_title(f"Projected vs Actual Points - Week {week}", fontsize=14, fontweight="bold", pad=15)
        ax.legend()
        ax.grid(axis="x", visible=False)
        figure.tight_layout()
        return save(figure, images_dir, f"accuracy_teams_week_{week}.png")
