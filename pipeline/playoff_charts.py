"""Futures chart for the analytics page: each team's chance of making the playoffs."""

from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib import colormaps, style
from matplotlib.figure import Figure
from matplotlib.ticker import PercentFormatter

from pipeline.charts import NOTEBOOK_STYLE, save


def playoff_probability(images_dir: Path, week: int, probabilities: pd.Series, playoff_teams: int) -> str:
    title = f"Playoff Probability - Week {week}\n(Top {playoff_teams} Make Playoffs)"
    return probability_bars(images_dir, f"playoff_probability_week_{week}.png", title, probabilities)


def probability_bars(images_dir: Path, name: str, title: str, probabilities: pd.Series) -> str:
    """One horizontal bar per owner (the index), most likely on top, shaded from red at 0% to green at 100%."""
    ranked = probabilities.sort_values(kind="stable")
    rows = np.arange(len(ranked))
    with style.context(NOTEBOOK_STYLE):
        figure = Figure(figsize=(12, 8))
        ax = figure.subplots()
        colors = colormaps["RdYlGn"](ranked.to_numpy())
        bars = ax.barh(rows, ranked.to_numpy(), color=colors, edgecolor="black", linewidth=0.8)
        ax.bar_label(bars, labels=[f"{probability:.1%}" for probability in ranked], padding=4, fontweight="bold")
        ax.set_yticks(rows, labels=list(ranked.index))
        ax.set_xlim(0, 1.12)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
        ax.xaxis.set_major_formatter(PercentFormatter(1.0))
        ax.set_title(title, fontsize=14, fontweight="bold", pad=15)
        ax.grid(axis="y", visible=False)
        figure.tight_layout()
        return save(figure, images_dir, name)
