"""PNG charts for the Flask analytics page, drawn the way the notebooks drew them.

Figures are built with the object API and rendered by Agg, so no display or pyplot state is involved.
"""

from pathlib import Path

import pandas as pd
import seaborn
from matplotlib import cycler, style
from matplotlib.figure import Figure

NOTEBOOK_STYLE = [
    "seaborn-v0_8-darkgrid",
    {
        "axes.prop_cycle": cycler(color=seaborn.color_palette("husl")),
        "figure.figsize": (14, 8),
        "font.size": 11,
        "axes.labelsize": 12,
        "axes.titlesize": 14,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
    },
]


def score_distributions_overlay(images_dir: Path, week: int, draws: pd.DataFrame, densities: pd.DataFrame) -> str:
    """Every team's score density on one axis, highest mean first: six colours, solid for the top six, then dashed.

    `draws` and `densities` have one column per owner; `densities` is indexed by its x grid.
    """
    colors = seaborn.color_palette("husl", 6)
    owners = draws.mean().sort_values(ascending=False).index
    with style.context(NOTEBOOK_STYLE):
        figure = Figure(figsize=(12, 6))
        ax = figure.subplots()
        for rank, owner in enumerate(owners):
            ax.plot(
                densities.index,
                densities[owner],
                label=f"Team {owner}",
                linewidth=2.5,
                alpha=0.85,
                color=colors[rank % 6],
                linestyle="-" if rank < 6 else "--",
            )
        ax.set_title(f"Team Score Distributions - Week {week}", fontsize=14, fontweight="bold", pad=15)
        ax.set_xlabel("Total Points", fontsize=12)
        ax.set_xlim(0, 300)
        ax.legend(loc="upper right", fontsize=11, ncol=2)
        ax.grid(alpha=0.3)
        figure.tight_layout()
        return save(figure, images_dir, f"simulation_distributions_overlay_week_{week}.png")


def score_boxplot(images_dir: Path, week: int, draws: pd.DataFrame) -> str:
    """One box per team (columns of `draws` are owners), lowest median on the left."""
    owners = draws.median().sort_values().index
    with style.context(NOTEBOOK_STYLE):
        figure = Figure(figsize=(16, 8))
        ax = figure.subplots()
        boxplot = ax.boxplot(
            [draws[owner].to_numpy() for owner in owners],
            tick_labels=list(owners),
            patch_artist=True,
            showmeans=True,
            meanprops=dict(marker="D", markerfacecolor="red", markersize=8),
            medianprops=dict(color="darkblue", linewidth=2),
            whiskerprops=dict(linewidth=1.5),
            capprops=dict(linewidth=1.5),
            flierprops=dict(marker="o", markerfacecolor="gray", markersize=3, alpha=0.3),
            widths=0.6,
        )
        for box, color in zip(boxplot["boxes"], seaborn.color_palette("husl", len(owners)), strict=True):
            box.set_facecolor(color)
            box.set_alpha(0.7)
        ax.set_ylabel("Total Points", fontsize=12, fontweight="bold")
        ax.set_title(f"Monte Carlo Simulations - Week {week}", fontsize=14, fontweight="bold", pad=15)
        ax.set_ylim(0, 300)
        ax.grid(axis="y", alpha=0.3)
        figure.tight_layout()
        return save(figure, images_dir, f"simulation_boxplot_week_{week}.png")


def save(figure: Figure, images_dir: Path, name: str) -> str:
    images_dir.mkdir(parents=True, exist_ok=True)
    figure.savefig(images_dir / name, dpi=300, bbox_inches="tight")
    return name
