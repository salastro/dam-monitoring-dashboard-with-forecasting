from typing import Dict, List, Optional, Union

import plotly.graph_objects as go

# Optional: only parameters listed here get a threshold overlay in the
# default dashboard; omitted parameters won't have overlays.
DEFAULT_THRESHOLDS: Dict[str, Dict[str, Optional[float]]] = {
    "water_level_upstream_m": {"ceiling": 179.0, "floor": None},
    # "pH": {"ceiling": 8.5, "floor": 6.5},
    "seismic_acc_g": {"ceiling": 0.01, "floor": 0.0001},
}


def _add_threshold_shading(
    fig, df, x, row, columns: Union[str, List[str]], threshold: float, threshold_type: str
):
    """Helper: Add shaded regions and threshold line for a single threshold."""
    if isinstance(columns, str):
        columns = [columns]

    # Determine exceedance condition based on type
    if threshold_type == "ceiling":
        exceed_mask = df[columns].max(axis=1) > threshold
        line_dash = "dash"
        line_color = "firebrick"
        fill_color = "rgba(220, 20, 60, 0.14)"
        annotation_pos = "top right"
    else:  # floor
        exceed_mask = df[columns].min(axis=1) < threshold
        line_dash = "dot"
        line_color = "steelblue"
        fill_color = "rgba(30, 144, 255, 0.14)"
        annotation_pos = "bottom right"

    # Add horizontal threshold line
    fig.add_hline(
        y=threshold,
        line_dash=line_dash,
        line_color=line_color,
        line_width=1.8,
        annotation_text=f"{threshold_type.capitalize()} ({threshold})",
        annotation_position=annotation_pos,
        row=row, col=1,
    )

    # Add shaded vertical bands for contiguous exceedance intervals
    in_interval = False
    start_idx = None
    for i, flag in enumerate(exceed_mask):
        if flag and not in_interval:
            in_interval = True
            start_idx = i
        elif not flag and in_interval:
            in_interval = False
            fig.add_vrect(
                x0=df.iloc[start_idx][x],
                x1=df.iloc[i - 1][x],
                fillcolor=fill_color,
                line_width=0,
                layer="below",
                row=row, col=1,
            )

    if in_interval and start_idx is not None:
        fig.add_vrect(
            x0=df.iloc[start_idx][x],
            x1=df.iloc[-1][x],
            fillcolor=fill_color,
            line_width=0,
            layer="below",
            row=row, col=1,
        )


def add_threshold_overlay(
    fig,
    df,
    x,
    row,
    columns: Union[str, List[str]],
    ceiling: Optional[float] = None,
    floor: Optional[float] = None,
    ceiling_emphasis_color: str = "crimson",
    floor_emphasis_color: str = "navy",
    label_suffix: str = "",
):
    """
    Add threshold ceiling/floor lines, shading, and emphasis traces to a subplot.

    Parameters:
    - columns: single column name (str) or list of column names to monitor
    - ceiling: upper threshold value (optional)
    - floor: lower threshold value (optional)
    - ceiling_emphasis_color, floor_emphasis_color: colors for exceedance traces
    - label_suffix: suffix for trace names (e.g., "Critical", "Warning")
    """
    if isinstance(columns, str):
        columns = [columns]

    if ceiling is None and floor is None:
        return  # Nothing to highlight

    # Add shading and threshold lines
    if ceiling is not None:
        _add_threshold_shading(fig, df, x, row, columns, ceiling, "ceiling")
    if floor is not None:
        _add_threshold_shading(fig, df, x, row, columns, floor, "floor")

    # Add emphasis traces for exceedances
    if ceiling is not None:
        for col in columns:
            exceed = df[col].where(df[col] > ceiling)
            fig.add_trace(
                go.Scatter(
                    x=df[x],
                    y=exceed,
                    mode="lines",
                    name=f"{col} Ceiling {label_suffix}".strip(),
                    line=dict(color=ceiling_emphasis_color, width=2.6),
                    connectgaps=False,
                    showlegend=len(columns) == 1,  # Only show legend if single column
                ),
                row=row, col=1, secondary_y=False,
            )

    if floor is not None:
        for col in columns:
            below = df[col].where(df[col] < floor)
            fig.add_trace(
                go.Scatter(
                    x=df[x],
                    y=below,
                    mode="lines",
                    name=f"{col} Floor {label_suffix}".strip(),
                    line=dict(color=floor_emphasis_color, width=2.6),
                    connectgaps=False,
                    showlegend=len(columns) == 1,  # Only show legend if single column
                ),
                row=row, col=1, secondary_y=False,
            )
