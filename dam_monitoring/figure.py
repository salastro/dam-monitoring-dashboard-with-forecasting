from typing import Dict, Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .data import downsample_for_plot
from .thresholds import add_threshold_overlay
from .traces import add_pair_twiny_traces, add_single_trace

# (row, secondary_y, color) per forecastable column. Must stay in sync with
# the trace-adding calls in build_figure below -- used only to place/style
# forecast overlays on the matching subplot/axis as the real trace.
COLUMN_PLOT_INFO = {
    "water_level_upstream_m": (1, False, "royalblue"),
    "water_level_downstream_m": (1, True, "deepskyblue"),
    "discharge_m3s": (2, False, "seagreen"),
    "chlorophyll_a_ugL": (2, True, "darkorchid"),
    "disp_horiz_mm": (3, False, "darkorange"),
    "disp_vert_mm": (3, True, "crimson"),
    "seismic_acc_g": (4, False, "mediumpurple"),
    "water_temp_c": (5, False, "tomato"),
    "pH": (5, True, "dodgerblue"),
    "TDS_mgL": (6, False, "saddlebrown"),
    "DO_mgL": (6, True, "steelblue"),
}

# RGB for each color name used above, so the confidence-band fill can reuse
# the trace's own color at low opacity (Plotly needs an rgba() string for
# that; it has no named-color-to-rgb conversion of its own to call into).
_COLOR_RGB = {
    "royalblue": (65, 105, 225),
    "deepskyblue": (0, 191, 255),
    "seagreen": (46, 139, 87),
    "darkorchid": (153, 50, 204),
    "darkorange": (255, 140, 0),
    "crimson": (220, 20, 60),
    "mediumpurple": (147, 112, 219),
    "tomato": (255, 99, 71),
    "dodgerblue": (30, 144, 255),
    "saddlebrown": (139, 69, 19),
    "steelblue": (70, 130, 180),
}


def _band_fill(color: str, alpha: float = 0.15) -> str:
    r, g, b = _COLOR_RGB[color]
    return f"rgba({r},{g},{b},{alpha})"


def add_forecast_traces(
    fig,
    forecasts: Dict[str, pd.DataFrame],
    horizon_days: Optional[int] = None,
):
    """Overlay each column's Prophet forecast (line + confidence band) on
    its matching subplot/axis, continuing past the last real timestamp.

    forecasts: column -> DataFrame with [ds, yhat, yhat_lower, yhat_upper],
    as produced by dam_monitoring.forecasting.forecast_all.
    horizon_days: if given, truncate each forecast to this many days past
    its first row; if None, the full precomputed forecast is shown.
    """
    for col, forecast_df in forecasts.items():
        info = COLUMN_PLOT_INFO.get(col)
        if info is None or forecast_df is None or forecast_df.empty:
            continue
        row, secondary_y, color = info

        fc = forecast_df
        if horizon_days is not None:
            cutoff = fc["ds"].iloc[0] + pd.Timedelta(days=horizon_days)
            fc = fc[fc["ds"] <= cutoff]
        if fc.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=fc["ds"], y=fc["yhat_upper"],
                mode="lines", line=dict(width=0),
                showlegend=False, hoverinfo="skip",
            ),
            row=row, col=1, secondary_y=secondary_y,
        )
        fig.add_trace(
            go.Scatter(
                x=fc["ds"], y=fc["yhat_lower"],
                mode="lines", line=dict(width=0),
                fill="tonexty", fillcolor=_band_fill(color),
                showlegend=False, hoverinfo="skip",
            ),
            row=row, col=1, secondary_y=secondary_y,
        )
        fig.add_trace(
            go.Scatter(
                x=fc["ds"], y=fc["yhat"],
                mode="lines", name=f"{col} Forecast",
                line=dict(color=color, width=1.6, dash="dot"),
            ),
            row=row, col=1, secondary_y=secondary_y,
        )


def build_figure(
    df: pd.DataFrame,
    max_points: int = 3000,
    thresholds: Optional[Dict[str, Dict[str, Optional[float]]]] = None,
    forecasts: Optional[Dict[str, pd.DataFrame]] = None,
    forecast_horizon_days: Optional[int] = None,
    show: bool = True,
):
    """
    Build the monitoring dashboard with optional threshold overlays.

    Parameters:
    - thresholds: dict mapping column names to threshold config
      Example: {
          'water_level_upstream_m': {'ceiling': 182.0, 'floor': None},
          'water_level_downstream_m': {'ceiling': 182.0, 'floor': None},
          'pH': {'ceiling': 8.5, 'floor': 6.5},
      }
    - forecasts: optional column -> Prophet forecast DataFrame, as produced
      by dam_monitoring.forecasting.forecast_all. Overlaid on each column's
      own subplot/axis, continuing past the last real timestamp.
    - forecast_horizon_days: how far into each forecast to display; None
      shows the full precomputed forecast.
    """
    if thresholds is None:
        thresholds = {}

    x = "timestamp"
    plot_df = downsample_for_plot(df, max_points=max_points)

    subplot_titles = [
        "Water Levels",
        "Discharge & Chlorophyll-a",
        "Structural Displacement",
        "Seismic Activity",
        "Water Temperature & pH",
        "TDS & Dissolved Oxygen",
    ]

    fig = make_subplots(
        rows=6,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        subplot_titles=subplot_titles,
        specs=[
            [{"secondary_y": True}],
            [{"secondary_y": True}],
            [{"secondary_y": True}],
            [{"secondary_y": False}],
            [{"secondary_y": True}],
            [{"secondary_y": True}],
        ],
    )

    # 1. Water levels (upstream vs downstream) - same unit (m), independent scales
    add_pair_twiny_traces(
        fig, plot_df, x, row=1,
        col_a="water_level_upstream_m", label_a="Upstream", color_a="royalblue",
        yaxis_title_a="Upstream Level (m)",
        col_b="water_level_downstream_m", label_b="Downstream", color_b="deepskyblue",
        yaxis_title_b="Downstream Level (m)",
    )
    # Apply thresholds for both upstream and downstream water levels
    water_level_cols = [
        col for col in ["water_level_upstream_m", "water_level_downstream_m"]
        if col in thresholds
    ]
    if water_level_cols:
        ceiling = thresholds.get(water_level_cols[0], {}).get("ceiling")
        floor = thresholds.get(water_level_cols[0], {}).get("floor")
        add_threshold_overlay(
            fig, plot_df, x, row=1,
            columns=water_level_cols,
            ceiling=ceiling,
            floor=floor,
            ceiling_emphasis_color="crimson",
            floor_emphasis_color="darkblue",
            forecasts=forecasts,
            forecast_horizon_days=forecast_horizon_days,
        )

    # 2. Discharge & Chlorophyll-a - different units, independent scales
    add_pair_twiny_traces(
        fig, plot_df, x, row=2,
        col_a="discharge_m3s", label_a="Discharge", color_a="seagreen",
        yaxis_title_a="Discharge (m^3/s)",
        col_b="chlorophyll_a_ugL", label_b="Chlorophyll-a", color_b="darkorchid",
        yaxis_title_b="Chlorophyll-a (ug/L)",
    )
    if "discharge_m3s" in thresholds:
        cfg = thresholds["discharge_m3s"]
        add_threshold_overlay(
            fig, plot_df, x, row=2,
            columns="discharge_m3s",
            ceiling=cfg.get("ceiling"),
            floor=cfg.get("floor"),
            forecasts=forecasts,
            forecast_horizon_days=forecast_horizon_days,
        )
    if "chlorophyll_a_ugL" in thresholds:
        cfg = thresholds["chlorophyll_a_ugL"]
        add_threshold_overlay(
            fig, plot_df, x, row=2,
            columns="chlorophyll_a_ugL",
            ceiling=cfg.get("ceiling"),
            floor=cfg.get("floor"),
            forecasts=forecasts,
            forecast_horizon_days=forecast_horizon_days,
        )

    # 3. Structural displacement (horizontal vs vertical) - same unit (mm), independent scales
    add_pair_twiny_traces(
        fig, plot_df, x, row=3,
        col_a="disp_horiz_mm", label_a="Horizontal", color_a="darkorange",
        yaxis_title_a="Horizontal Disp. (mm)",
        col_b="disp_vert_mm", label_b="Vertical", color_b="crimson",
        yaxis_title_b="Vertical Disp. (mm)",
    )
    disp_cols = [col for col in ["disp_horiz_mm", "disp_vert_mm"] if col in thresholds]
    if disp_cols:
        cfg = thresholds.get(disp_cols[0], {})
        add_threshold_overlay(
            fig, plot_df, x, row=3,
            columns=disp_cols,
            ceiling=cfg.get("ceiling"),
            floor=cfg.get("floor"),
            forecasts=forecasts,
            forecast_horizon_days=forecast_horizon_days,
        )

    # 4. Seismic acceleration - own units (g)
    add_single_trace(
        fig, plot_df, x, row=4,
        col="seismic_acc_g", label="Seismic Acceleration", color="mediumpurple",
        yaxis_title="Acceleration (g)",
    )
    fig.update_yaxes(type="log", row=4, col=1)
    if "seismic_acc_g" in thresholds:
        cfg = thresholds["seismic_acc_g"]
        add_threshold_overlay(
            fig, plot_df, x, row=4,
            columns="seismic_acc_g",
            ceiling=cfg.get("ceiling"),
            floor=cfg.get("floor"),
            forecasts=forecasts,
            forecast_horizon_days=forecast_horizon_days,
        )

    # 5. Water temperature & pH - different units, use secondary y-axis
    add_pair_twiny_traces(
        fig, plot_df, x, row=5,
        col_a="water_temp_c", label_a="Water Temp", color_a="tomato", yaxis_title_a="Temp (deg C)",
        col_b="pH", label_b="pH", color_b="dodgerblue", yaxis_title_b="pH",
    )
    if "pH" in thresholds:
        cfg = thresholds["pH"]
        add_threshold_overlay(
            fig, plot_df, x, row=5,
            columns="pH",
            ceiling=cfg.get("ceiling"),
            floor=cfg.get("floor"),
            forecasts=forecasts,
            forecast_horizon_days=forecast_horizon_days,
        )

    # 6. TDS & DO - both mg/L, independent scales
    add_pair_twiny_traces(
        fig, plot_df, x, row=6,
        col_a="TDS_mgL", label_a="TDS", color_a="saddlebrown",
        yaxis_title_a="TDS (mg/L)",
        col_b="DO_mgL", label_b="Dissolved Oxygen", color_b="steelblue",
        yaxis_title_b="Dissolved Oxygen (mg/L)",
    )
    tds_do_cols = [col for col in ["TDS_mgL", "DO_mgL"] if col in thresholds]
    if tds_do_cols:
        cfg = thresholds.get(tds_do_cols[0], {})
        add_threshold_overlay(
            fig, plot_df, x, row=6,
            columns=tds_do_cols,
            ceiling=cfg.get("ceiling"),
            floor=cfg.get("floor"),
            forecasts=forecasts,
            forecast_horizon_days=forecast_horizon_days,
        )

    fig.update_xaxes(title_text="Timestamp", row=6, col=1)

    if forecasts:
        add_forecast_traces(fig, forecasts, forecast_horizon_days)

    # No fig-level title here: the dashboard title is rendered as plain HTML
    # above the chart (see gui.py) instead of via Plotly's own layout, since
    # Plotly does not reliably auto-expand the top margin for a wrapping
    # horizontal legend, which lets it collide with an in-figure title when
    # the container is resized (plotly.js#7556).
    fig.update_layout(
        height=1800,
        autosize=True,
        hovermode="x",
        template="plotly_white",
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5,
        ),
        margin=dict(l=70, r=70, t=80, b=60),
        # Constant across builds: lets gui.py's in-place Plotly.react updates
        # (auto-reload on new CSV rows) preserve zoom/pan/legend state
        # instead of resetting the view every time new data arrives.
        uirevision="dam-monitoring-dashboard",
    )

    if show:
        fig.show()

    return fig
