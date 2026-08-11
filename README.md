# Dam Monitoring Dashboard

A desktop dashboard for visualizing dam sensor telemetry (water levels,
discharge, structural displacement, seismic activity, water quality) and
projecting it forward with per-sensor Prophet forecasts. Built with PyQt5 +
Plotly, with a live auto-reload mode for streaming CSV data.

## Features

- **Six-panel dashboard**: water levels, discharge & chlorophyll-a,
  structural displacement, seismic activity, water temperature & pH, and
  TDS & dissolved oxygen -- paired series share a panel on independent
  y-axes rather than crowding separate rows.
- **Threshold overlays**: configurable ceiling/floor lines with shaded
  exceedance bands per sensor (see `DEFAULT_THRESHOLDS` in
  `dam_monitoring/thresholds.py`).
- **Live auto-reload**: watches the open CSV and refreshes the chart as new
  rows are appended, without a full page reload -- zoom, pan, and legend
  state are preserved across updates.
- **Future forecasting**: fits one [Prophet](https://facebook.github.io/prophet/)
  model per sensor column (weekly, monthly, quarterly, and yearly
  seasonality; `changepoint_prior_scale=0.001`) and overlays the forecast
  and its confidence band on each panel, projecting genuinely into the
  future rather than backtesting on held-out history. Fitting runs in a
  background thread and results stream in per-column as each one finishes.
  A slider controls how far into the forecast to display; a checkbox trades
  fit speed for confidence-band fidelity (see [Forecasting](#forecasting)).

## Requirements

- Python 3.10+
- See `requirements.txt`: `pandas`, `plotly`, `PyQt5`, `PyQtWebEngine`,
  `prophet`

```bash
pip install -r requirements.txt
```

## Usage

### GUI (recommended)

```bash
python3 gui.py
```

Click **Open CSV…** (or `Ctrl+O`) to load a file. The dashboard renders
immediately; a background fit then fills in the forecast for each sensor as
it completes (watch the progress indicator in the toolbar). Opening a
*different* file resets the view and forecasts; the file currently open
keeps auto-reloading and preserving your zoom/pan as it changes.

### CLI

```bash
python3 main.py
```

Builds the same dashboard from `egypt_dam_monitoring_2years_hourly (1).csv`
in the current directory and opens it in your browser (via
`plotly`'s default renderer, i.e. no forecast, no auto-reload -- that's
GUI-only). Edit `main.py` to point at a different CSV or to enable the
forecast overlay.

### CSV format

```
timestamp, water_level_upstream_m, water_level_downstream_m,
discharge_m3s, disp_horiz_mm, disp_vert_mm, seismic_acc_g,
water_temp_c, pH, TDS_mgL, DO_mgL, chlorophyll_a_ugL
```

### Simulating live data

`simulate_live_data.py` appends synthetic rows to a CSV on an interval, for
exercising the GUI's auto-reload path without waiting on real sensors:

```bash
python3 simulate_live_data.py --csv "egypt_dam_monitoring_2years_hourly (1).csv" --interval 3 --rows 20
```

Run it alongside the GUI (with that CSV already open) to watch the
dashboard update live.

## Forecasting

- One Prophet model is fit per column in `FORECAST_COLUMNS`
  (`dam_monitoring/forecasting.py`), in parallel via a thread pool.
- **Hourly precision** (toolbar checkbox, off by default): by default,
  training data is aggregated to daily means before fitting -- about 65x
  faster, since none of the requested seasonalities need hourly
  resolution -- at the cost of confidence bands that no longer reflect
  real hour-to-hour volatility (and no hour-of-day component, which
  Prophet's `daily_seasonality='auto'` only detects on sub-daily data).
  Toggling it on re-fits using the raw hourly series instead.
- The horizon slider (1 to `MAX_HORIZON_DAYS`, default `DEFAULT_HORIZON_DAYS`
  days) only re-slices the already-computed forecast; it never triggers a
  refit.
- Auto-reload triggers a background refit automatically; rapid successive
  reloads coalesce into a single follow-up fit rather than queuing one per
  reload.

## Building a Windows executable

This app can't be cross-compiled from Linux/macOS -- Prophet ships a
natively-compiled Stan binary per OS, so the build has to run on Windows
with Windows-installed packages. Two options, both using
`packaging/windows/app.spec` (PyInstaller):

- **GitHub Actions**: `.github/workflows/build-windows.yml` builds on
  `windows-latest` on every push to main/master (or manually via
  workflow_dispatch) and uploads the result as a build artifact.
- **Local Windows build**: run `packaging\windows\build.bat` from the repo
  root on a Windows machine. It installs Python 3.12 into a dedicated
  per-user folder (no admin rights needed), installs dependencies, and
  runs PyInstaller. Output: `dist\DamMonitoringDashboard\DamMonitoringDashboard.exe`.

## Project structure

```
gui.py                      Qt desktop app (primary entry point)
main.py                     CLI entry point (no forecast/auto-reload)
simulate_live_data.py       Appends synthetic rows for testing auto-reload
dam_monitoring/
  data.py                   CSV loading, downsampling for plotting
  figure.py                 Dashboard figure construction (Plotly)
  thresholds.py             Ceiling/floor overlay logic + DEFAULT_THRESHOLDS
  traces.py                 Trace-adding helpers (paired/twin-axis/single)
  forecasting.py            Prophet forecasting (forecast_all)
  colab.py                  Google Colab file-upload detection
packaging/windows/
  app.spec                  PyInstaller build spec
  build.bat                 Local Windows build script
.github/workflows/
  build-windows.yml         CI build for the Windows executable
```
