"""Production Prophet forecasting for the dam monitoring dashboard.

Fits one Prophet model per sensor column on the *entire* historical series
and projects forward past the last observed timestamp -- these are real
future predictions, not a train/test backtest against held-out history.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, Optional, Tuple

import pandas as pd

# One Prophet model is fit per column here. All are numeric sensor readings
# present in the dashboard's CSV schema.
FORECAST_COLUMNS = [
    "water_level_upstream_m",
    "water_level_downstream_m",
    "discharge_m3s",
    "disp_horiz_mm",
    "disp_vert_mm",
    "seismic_acc_g",
    "water_temp_c",
    "pH",
    "TDS_mgL",
    "DO_mgL",
    "chlorophyll_a_ugL",
]

# Forecasts are computed once per fit, out to this many days ahead; the GUI's
# horizon slider then just re-slices the cached result (cheap), rather than
# re-predicting (still fairly cheap) or re-fitting (expensive) on every drag.
MAX_HORIZON_DAYS = 180
DEFAULT_HORIZON_DAYS = 30

CHANGEPOINT_PRIOR_SCALE = 0.001


def _fit_one(df: pd.DataFrame, column: str, horizon_days: int) -> pd.DataFrame:
    from prophet import Prophet  # heavy import: only paid when forecasting runs

    prophet_df = (
        df[["timestamp", column]]
        .rename(columns={"timestamp": "ds", column: "y"})
        .dropna()
    )

    model = Prophet(
        weekly_seasonality=True,
        yearly_seasonality=True,
        changepoint_prior_scale=CHANGEPOINT_PRIOR_SCALE,
    )
    # Monthly and quarterly ("seasonal") cycles aren't built into Prophet;
    # weekly/yearly are.
    model.add_seasonality(name="monthly", period=30.5, fourier_order=5)
    model.add_seasonality(name="seasonal", period=91.25, fourier_order=5)
    model.fit(prophet_df)

    future = model.make_future_dataframe(
        periods=horizon_days * 24, freq="h", include_history=False
    )
    forecast = model.predict(future)[["ds", "yhat", "yhat_lower", "yhat_upper"]]

    # Prepend the last real observation so the forecast trace visually
    # connects to the historical line instead of leaving a gap.
    last_real = prophet_df.iloc[[-1]].rename(columns={"y": "yhat"})
    last_real = last_real.assign(
        yhat_lower=last_real["yhat"], yhat_upper=last_real["yhat"]
    )[forecast.columns]

    return pd.concat([last_real, forecast], ignore_index=True)


def forecast_all(
    df: pd.DataFrame,
    columns: Iterable[str] = FORECAST_COLUMNS,
    horizon_days: int = MAX_HORIZON_DAYS,
    max_workers: int = 4,
    on_column_done: Optional[Callable[[str, Optional[pd.DataFrame], Optional[str]], None]] = None,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, str]]:
    """Fit + predict one Prophet model per column, in parallel threads.

    Prophet's `fit()` spends most of its wall-clock time in a compiled Stan
    subprocess (via cmdstanpy), so it releases the GIL for most of that
    time -- a thread pool gives real parallelism here despite Python's GIL.

    Returns (forecasts, errors): forecasts maps column -> DataFrame with
    columns [ds, yhat, yhat_lower, yhat_upper]; errors maps column -> the
    exception message for any column whose fit failed. A failure in one
    column never prevents the others from being returned, so a single flaky
    sensor column can't blank out the whole dashboard's forecasts.

    on_column_done, if given, is called as (column, forecast_or_None,
    error_or_None) as soon as each column's fit finishes -- in actual
    completion order, not submission order -- so a caller (e.g. the GUI)
    can show results progressively instead of waiting for every column.
    """
    columns = list(columns)
    forecasts: Dict[str, pd.DataFrame] = {}
    errors: Dict[str, str] = {}

    def run(column: str):
        try:
            return column, _fit_one(df, column, horizon_days), None
        except Exception as exc:
            return column, None, str(exc)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run, column): column for column in columns}
        for future in as_completed(futures):
            column, result, error = future.result()
            if error is None:
                forecasts[column] = result
            else:
                errors[column] = error
            if on_column_done is not None:
                on_column_done(column, result, error)

    return forecasts, errors
