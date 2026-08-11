#!/usr/bin/env python
# coding: utf-8
"""CLI entry point for the dam monitoring dashboard.

Reads a CSV with the following columns and produces a grouped
multi-panel time-series visualization:

timestamp, water_level_upstream_m, water_level_downstream_m,
discharge_m3s, disp_horiz_mm, disp_vert_mm, seismic_acc_g,
water_temp_c, pH, TDS_mgL, DO_mgL, chlorophyll_a_ugL
"""

from dam_monitoring import DEFAULT_THRESHOLDS, build_figure, load_data
from dam_monitoring.colab import IS_COLAB, colab_files

if __name__ == "__main__":
    if IS_COLAB:
        uploaded = colab_files.upload()
        csv_path = next(iter(uploaded))
    else:
        csv_path = "egypt_dam_monitoring_2years_hourly (1).csv"
    df = load_data(csv_path)

    # Build and display the figure with the default thresholds.
    build_figure(df, thresholds=DEFAULT_THRESHOLDS)

    # Future forecast overlay (requires: pip install prophet). Slow -- fits
    # one Prophet model per sensor column; the GUI (gui.py) runs this in a
    # background thread instead.
    # from dam_monitoring import forecast_all, DEFAULT_HORIZON_DAYS
    # forecasts, errors = forecast_all(df)
    # build_figure(df, thresholds=DEFAULT_THRESHOLDS, forecasts=forecasts,
    #              forecast_horizon_days=DEFAULT_HORIZON_DAYS)
