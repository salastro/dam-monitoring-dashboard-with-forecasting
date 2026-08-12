#!/usr/bin/env python
# coding: utf-8
"""Qt desktop front-end for the dam monitoring dashboard.

Lets the user pick a CSV via a native file dialog instead of hard-coding a
path, and renders the resulting Plotly figure inline (via QWebEngineView)
instead of opening it in a browser tab. Also runs a background Prophet
forecast (one model per sensor column) and overlays it on the dashboard,
with a slider to control how far into the future is displayed.
"""

import atexit
import os
import subprocess
import sys
import tempfile

import plotly
import plotly.offline as pyo

if sys.platform == "win32":
    # cmdstanpy shells out to the compiled Stan model binary (one call per
    # forecast column, in parallel) via plain subprocess.Popen with no
    # creationflags. A windowed .exe (no console of its own) has nothing
    # for that console-subsystem child to attach to, so Windows pops a
    # brand new console window for every single one -- several flashing
    # in sequence each time a CSV is imported. Patched here, once, since
    # cmdstanpy itself doesn't offer a way to suppress it.
    _orig_popen_init = subprocess.Popen.__init__

    def _popen_init_no_window(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        _orig_popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_init_no_window

from PyQt6.QtCore import QFileSystemWatcher, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSlider,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from dam_monitoring import (
    DEFAULT_HORIZON_DAYS,
    DEFAULT_THRESHOLDS,
    FORECAST_COLUMNS,
    MAX_HORIZON_DAYS,
    build_figure,
    forecast_all,
    load_data,
)


class ForecastWorker(QThread):
    """Fits one Prophet model per sensor column off the UI thread.

    Fitting (unlike the chart redraw) is genuinely slow -- seconds per
    column -- so it must never run on the Qt event-loop thread.
    """

    finished_ok = pyqtSignal(str, dict, dict)  # path, forecasts, errors
    failed = pyqtSignal(str, str)  # path, message
    # path, column, forecast-DataFrame-or-None, error-message-or-None --
    # emitted as each column's fit finishes, in completion order, so the
    # GUI can show results progressively instead of waiting for all of them.
    column_ready = pyqtSignal(str, str, object, object)

    def __init__(self, df, path, columns, horizon_days, hourly_precision=False):
        super().__init__()
        self._df = df
        self._path = path
        self._columns = columns
        self._horizon_days = horizon_days
        self._hourly_precision = hourly_precision

    def run(self):
        def on_column_done(column, result, error):
            self.column_ready.emit(self._path, column, result, error)

        try:
            forecasts, errors = forecast_all(
                self._df, self._columns, horizon_days=self._horizon_days,
                on_column_done=on_column_done,
                hourly_precision=self._hourly_precision,
            )
        except Exception as exc:
            self.failed.emit(self._path, str(exc))
            return
        self.finished_ok.emit(self._path, forecasts, errors)


class DamMonitoringWindow(QMainWindow):
    # Fixed DOM id for the chart div, so in-place updates (Plotly.react) can
    # always find it regardless of which render produced the current page.
    _CHART_DIV_ID = "dam-chart"

    # Live-reload rows arrive one hour at a time, but refitting Prophet on
    # every single one is wasteful -- the forecast a few hours out barely
    # moves. The chart itself still redraws on every reload; only the
    # (expensive) refit is throttled to roughly once per new day of data.
    _FORECAST_REFIT_INTERVAL_ROWS = 24

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Dam Monitoring Dashboard")
        self.resize(1200, 900)

        self._tmp_html_path: str | None = None
        atexit.register(self._cleanup_tmp_file)

        self._csv_path: str | None = None
        self._df = None
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_csv_changed)
        # Coalesce bursts of change events (some writers emit several per
        # save) into a single reload, shortly after they settle.
        self._reload_timer = QTimer(self)
        self._reload_timer.setSingleShot(True)
        self._reload_timer.setInterval(500)
        self._reload_timer.timeout.connect(self._reload_csv)

        # Forecast state: computed out to MAX_HORIZON_DAYS once per fit;
        # the horizon slider only re-slices this cached result, it never
        # triggers a refit by itself.
        self._forecasts: dict = {}
        self._horizon_days = DEFAULT_HORIZON_DAYS
        self._pending_horizon_days = DEFAULT_HORIZON_DAYS
        # Row count of the df as of the last time a refit was requested --
        # compared against the live-reloaded df's row count to gate
        # automatic refits (see _FORECAST_REFIT_INTERVAL_ROWS). Kept in
        # sync inside _request_forecast_refit itself, so every caller
        # (live reload, a new file, the hourly-precision toggle) resets it
        # the same way regardless of which one triggered the refit.
        self._forecast_row_count = 0
        # (column, "ceiling"/"floor") pairs already alerted on for the
        # current file -- a forecast still predicting the same breach on
        # its next routine refit shouldn't re-notify every time. Cleared
        # when a genuinely new file is opened (see load_csv), or sooner if
        # the breach itself clears (see _clear_active_breach).
        self._notified_forecast_breaches: set = set()
        # (column, "ceiling"/"floor") -> (threshold, first-breach timestamp)
        # for whichever breaches the *current* forecast still predicts --
        # drives the persistent warning banner, unlike the one-time toast.
        self._active_forecast_breaches: dict = {}
        self._tray_icon = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxWarning), self
            )
            self._tray_icon.setToolTip("Dam Monitoring Dashboard")
            self._tray_icon.show()
        # Off by default: fit on daily-aggregated data (~65x faster) since
        # none of the requested seasonalities need hourly resolution. On
        # trades that speed back for confidence bands that reflect real
        # hourly volatility instead of smoothing it away.
        self._hourly_precision = False
        # Whether a fit is currently running -- drives the business logic
        # (queue vs. start, indicator visibility). Deliberately separate
        # from the QThread objects themselves: those must stay alive until
        # each one's own `finished` signal fires (see _cleanup_forecast_thread),
        # or Qt aborts the process for destroying a QThread still winding
        # down internally, even after our own result signal has fired.
        self._fitting = False
        self._forecast_threads: list = []
        self._forecast_pending: tuple | None = None
        self._forecast_columns_done = 0
        self._forecast_columns_total = 0
        self._horizon_debounce = QTimer(self)
        self._horizon_debounce.setSingleShot(True)
        self._horizon_debounce.setInterval(200)
        self._horizon_debounce.timeout.connect(self._apply_horizon_change)

        self.web_view = QWebEngineView()
        self._chart_ready = False
        # A figure that arrived (e.g. a progressive forecast-column reveal)
        # while a full-page navigation was already in flight -- applied via
        # Plotly.react once that navigation finishes, instead of starting a
        # second, competing navigation (see _build_and_render).
        self._pending_fig = None
        self.web_view.loadFinished.connect(self._on_load_finished)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # Persistent (not auto-expiring, unlike the status bar) banner for
        # forecasts currently predicting a critical breach -- shown until
        # the forecast no longer predicts it or a different file is opened.
        self.forecast_warning_banner = QLabel("")
        self.forecast_warning_banner.setStyleSheet(
            "background-color: #b71c1c; color: white; padding: 6px 10px; font-weight: bold;"
        )
        self.forecast_warning_banner.setWordWrap(True)
        self.forecast_warning_banner.setVisible(False)
        layout.addWidget(self.forecast_warning_banner)
        layout.addWidget(self.web_view)
        self.setCentralWidget(central)

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = QAction("Open CSV…", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_csv)
        toolbar.addAction(open_action)

        toolbar.addSeparator()
        toolbar.addWidget(QLabel("Forecast horizon:"))
        self.horizon_slider = QSlider(Qt.Orientation.Horizontal)
        self.horizon_slider.setMinimum(1)
        self.horizon_slider.setMaximum(MAX_HORIZON_DAYS)
        self.horizon_slider.setValue(DEFAULT_HORIZON_DAYS)
        self.horizon_slider.setFixedWidth(160)
        self.horizon_slider.valueChanged.connect(self._on_horizon_slider_changed)
        toolbar.addWidget(self.horizon_slider)
        self.horizon_label = QLabel(f"{DEFAULT_HORIZON_DAYS}d")
        self.horizon_label.setFixedWidth(36)
        toolbar.addWidget(self.horizon_label)

        toolbar.addSeparator()
        self.hourly_precision_checkbox = QCheckBox("Hourly precision")
        self.hourly_precision_checkbox.setChecked(self._hourly_precision)
        self.hourly_precision_checkbox.setToolTip(
            "Off (default): fit forecasts on daily-aggregated data, ~65x faster.\n"
            "On: fit on raw hourly data -- much slower, but confidence bands\n"
            "reflect real hourly volatility instead of smoothing it away.\n"
            "Toggling immediately re-fits the current file."
        )
        self.hourly_precision_checkbox.toggled.connect(self._on_hourly_precision_toggled)
        toolbar.addWidget(self.hourly_precision_checkbox)

        # Loading indicator for the (slow, background) Prophet fit -- an
        # indeterminate progress bar since we don't have real progress
        # fractions, just "a fit is currently running or not". Visibility
        # must be toggled via the QAction that addWidget() returns, not the
        # widget itself -- toggling the widget's own setVisible() after
        # it's embedded in a QToolBar silently does nothing.
        toolbar.addSeparator()
        self.forecast_progress = QProgressBar()
        self.forecast_progress.setRange(0, 0)
        self.forecast_progress.setFixedWidth(80)
        self.forecast_progress.setTextVisible(False)
        self._forecast_progress_action = toolbar.addWidget(self.forecast_progress)
        self._forecast_progress_action.setVisible(False)
        self.forecast_status_label = QLabel("")
        toolbar.addWidget(self.forecast_status_label)

        self.path_label = QLabel("No file loaded")
        self.setStatusBar(QStatusBar())
        self.statusBar().addWidget(self.path_label)

        self._show_placeholder()

    def _on_load_finished(self, ok: bool):
        self._chart_ready = bool(ok)
        if self._chart_ready and self._pending_fig is not None:
            fig, self._pending_fig = self._pending_fig, None
            self._update_figure(fig)

    def _show_placeholder(self):
        self.web_view.setHtml(
            "<html><body style='font-family:sans-serif;color:#666;"
            "display:flex;align-items:center;justify-content:center;"
            "height:100vh;margin:0;'>"
            "<p>Open a CSV file to view the dashboard.</p>"
            "</body></html>"
        )

    def open_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select dam monitoring CSV",
            os.getcwd(),
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        self.load_csv(path)

    def load_csv(self, path: str, *, silent: bool = False, in_place: bool = False):
        try:
            df = load_data(path)
        except Exception as exc:
            if silent:
                # An automatic reload failing (e.g. mid-write, file briefly
                # missing) shouldn't pop a modal dialog; just report it.
                self.statusBar().showMessage(f"Reload failed: {exc}", 5000)
            else:
                QMessageBox.critical(self, "Failed to load CSV", str(exc))
            return

        # Opening a genuinely different file invalidates any forecast we
        # were showing for the old one -- clear it so it doesn't linger on
        # a new dataset, and force a full render (fresh default view)
        # rather than an in-place patch.
        is_new_file = path != self._csv_path
        if is_new_file:
            self._forecasts = {}
            self._notified_forecast_breaches = set()
            self._active_forecast_breaches = {}
            self.forecast_warning_banner.setVisible(False)

        self._df = df
        self._build_and_render(df, in_place=in_place and not is_new_file)

        self.path_label.setText(path)
        self._csv_path = path
        self._watch_path(path)

        # Chart re-renders above happen on every reload regardless; the
        # (expensive) refit itself only fires for a new file or once enough
        # new rows have piled up since the last one (see
        # _FORECAST_REFIT_INTERVAL_ROWS).
        rows_since_forecast = len(df) - self._forecast_row_count
        if is_new_file or rows_since_forecast >= self._FORECAST_REFIT_INTERVAL_ROWS:
            self._request_forecast_refit(df, path)

    def _build_and_render(self, df, *, in_place: bool):
        try:
            fig = build_figure(
                df,
                thresholds=DEFAULT_THRESHOLDS,
                forecasts=self._forecasts,
                forecast_horizon_days=self._horizon_days,
                show=False,
            )
        except Exception as exc:
            self.statusBar().showMessage(f"Render failed: {exc}", 5000)
            return

        if in_place and self._chart_ready:
            self._update_figure(fig)
        elif in_place:
            # A full-page navigation is already in flight (e.g. the initial
            # render for a large CSV can take many seconds, while forecast
            # columns keep completing in the background). Starting another
            # navigation here would cancel it mid-load -- possibly mid-way
            # through executing the inline plotly.js bundle -- leaving
            # `Plotly` undefined on whatever page ends up on screen. Just
            # remember the latest figure; _on_load_finished applies it via
            # Plotly.react once the in-flight navigation actually completes.
            self._pending_fig = fig
        else:
            self._render_figure(fig)

    def _watch_path(self, path: str):
        watched = self._watcher.files()
        if watched:
            self._watcher.removePaths(watched)
        self._watcher.addPath(path)

    def _on_csv_changed(self, changed_path: str):
        self._reload_timer.start()

    def _reload_csv(self):
        if not self._csv_path:
            return
        self.load_csv(self._csv_path, silent=True, in_place=True)

    # --- Forecast horizon slider -------------------------------------

    def _on_horizon_slider_changed(self, value: int):
        self.horizon_label.setText(f"{value}d")
        self._pending_horizon_days = value
        # Debounce: only re-render once the user pauses/releases, instead
        # of on every intermediate value while dragging.
        self._horizon_debounce.start()

    def _apply_horizon_change(self):
        self._horizon_days = self._pending_horizon_days
        if self._df is not None:
            # Cheap: re-slices the already-computed forecast, no refit.
            self._build_and_render(self._df, in_place=True)

    def _on_hourly_precision_toggled(self, checked: bool):
        self._hourly_precision = checked
        # Unlike the horizon slider, this changes what the model was
        # trained on, not just how much of its output to show -- a refit
        # is required. If a fit is already running (with the old setting),
        # this coalesces and starts fresh right after it finishes.
        if self._df is not None and self._csv_path is not None:
            self._request_forecast_refit(self._df, self._csv_path)

    # --- Background Prophet forecasting --------------------------------

    def _request_forecast_refit(self, df, path: str):
        self._forecast_row_count = len(df)
        if self._fitting:
            # A fit is already running and fitting is far slower than
            # reloads can arrive; keep only the latest request and run it
            # once the current fit finishes, instead of queuing every one.
            self._forecast_pending = (df, path)
            return
        self._start_forecast_thread(df, path)

    def _start_forecast_thread(self, df, path: str):
        self._forecast_progress_action.setVisible(True)
        self._forecast_columns_done = 0
        self._forecast_columns_total = len(FORECAST_COLUMNS)
        self.forecast_status_label.setText(f"Forecasting… (0/{self._forecast_columns_total})")
        self._fitting = True

        worker = ForecastWorker(
            df, path, FORECAST_COLUMNS, MAX_HORIZON_DAYS, self._hourly_precision
        )
        worker.finished_ok.connect(self._on_forecast_ready)
        worker.failed.connect(self._on_forecast_thread_failed)
        worker.column_ready.connect(self._on_forecast_column_ready)
        # QThread's own `finished` signal only fires once the thread has
        # truly wound down; hold a reference until then so Qt never has to
        # destroy a QThread object that's still technically running (which
        # aborts the process) just because we're done reading its result.
        self._forecast_threads.append(worker)
        worker.finished.connect(lambda w=worker: self._cleanup_forecast_thread(w))
        worker.start()

    def _cleanup_forecast_thread(self, worker):
        if worker in self._forecast_threads:
            self._forecast_threads.remove(worker)
        worker.deleteLater()

    def _on_forecast_column_ready(self, path: str, column: str, forecast_df, error):
        self._forecast_columns_done += 1
        if path != self._csv_path:
            return  # user has since opened a different file; discard
        if self._fitting:
            self.forecast_status_label.setText(
                f"Forecasting… ({self._forecast_columns_done}/{self._forecast_columns_total})"
            )
        if error is None:
            # Progressive reveal: patch this one column's forecast trace in
            # immediately rather than waiting for the whole fit cycle, via
            # the same in-place Plotly.react path as any other redraw --
            # zoom/pan stay put (uirevision), no full page reload.
            self._forecasts[column] = forecast_df
            self._check_forecast_breach(column, forecast_df)
            self._build_and_render(self._df, in_place=True)
        # else: this column's error is folded into the summary message
        # _on_forecast_ready shows once the whole fit cycle finishes.

    def _check_forecast_breach(self, column: str, forecast_df):
        """Re-evaluate whether `column`'s forecast currently crosses its
        configured ceiling/floor anywhere in the horizon -- a predicted
        future breach, not just an already-observed one. Unlike the
        one-time toast (deduped via _notified_forecast_breaches), the GUI
        banner (_active_forecast_breaches) reflects the *current* state:
        it's added when a breach first shows up and removed the moment a
        later forecast no longer predicts it, not just once per file.
        """
        cfg = DEFAULT_THRESHOLDS.get(column)
        if not cfg or forecast_df is None or forecast_df.empty:
            self._clear_active_breach(column, "ceiling")
            self._clear_active_breach(column, "floor")
            return

        ceiling = cfg.get("ceiling")
        if ceiling is not None:
            over = forecast_df[forecast_df["yhat"] > ceiling]
            if not over.empty:
                self._set_active_breach(column, "ceiling", ceiling, over["ds"].iloc[0])
            else:
                self._clear_active_breach(column, "ceiling")

        floor = cfg.get("floor")
        if floor is not None:
            under = forecast_df[forecast_df["yhat"] < floor]
            if not under.empty:
                self._set_active_breach(column, "floor", floor, under["ds"].iloc[0])
            else:
                self._clear_active_breach(column, "floor")

    def _set_active_breach(self, column: str, kind: str, threshold: float, when):
        self._active_forecast_breaches[(column, kind)] = (threshold, when)
        self._refresh_forecast_warning_banner()
        if (column, kind) not in self._notified_forecast_breaches:
            self._notified_forecast_breaches.add((column, kind))
            self._notify_critical_threshold(column, kind, threshold, when)

    def _clear_active_breach(self, column: str, kind: str):
        if self._active_forecast_breaches.pop((column, kind), None) is not None:
            # Let a breach that clears and later reoccurs toast again --
            # "already notified" should mean "still ongoing", not "ever
            # happened once for this file".
            self._notified_forecast_breaches.discard((column, kind))
            self._refresh_forecast_warning_banner()

    def _refresh_forecast_warning_banner(self):
        if not self._active_forecast_breaches:
            self.forecast_warning_banner.setVisible(False)
            return
        parts = []
        for (column, kind), (threshold, when) in sorted(self._active_forecast_breaches.items()):
            verb = "exceed ceiling" if kind == "ceiling" else "fall below floor"
            parts.append(f"{column} forecast to {verb} ({threshold}) around {when:%Y-%m-%d %H:%M}")
        self.forecast_warning_banner.setText("⚠ " + "  |  ".join(parts))
        self.forecast_warning_banner.setVisible(True)

    def _notify_critical_threshold(self, column: str, kind: str, threshold: float, when):
        verb = "exceed its ceiling" if kind == "ceiling" else "fall below its floor"
        message = f"{column} is forecast to {verb} ({threshold}) around {when:%Y-%m-%d %H:%M}."
        if self._tray_icon is not None and self._tray_icon.isVisible():
            self._tray_icon.showMessage(
                "Critical forecast threshold", message,
                QSystemTrayIcon.MessageIcon.Critical, 10000,
            )
        else:
            self.statusBar().showMessage(message, 10000)

    def _on_forecast_ready(self, path: str, forecasts: dict, errors: dict):
        self._fitting = False
        if path == self._csv_path:
            self._forecasts.update(forecasts)
            if errors:
                cols = ", ".join(errors)
                self.statusBar().showMessage(f"Forecast failed for: {cols}", 8000)
            self._build_and_render(self._df, in_place=True)
        # else: user has since opened a different file; discard this
        # stale result instead of applying it.
        self._maybe_start_pending_forecast()

    def _on_forecast_thread_failed(self, path: str, message: str):
        self._fitting = False
        if path == self._csv_path:
            self.statusBar().showMessage(f"Forecast fit failed: {message}", 8000)
        self._maybe_start_pending_forecast()

    def _maybe_start_pending_forecast(self):
        if self._forecast_pending is not None:
            df, path = self._forecast_pending
            self._forecast_pending = None
            self._start_forecast_thread(df, path)
        else:
            self._forecast_progress_action.setVisible(False)
            self.forecast_status_label.setText("")

    # --- Rendering -------------------------------------------------------

    def _local_plotlyjs_url(self) -> str:
        # A "cdn" reference would be fetched from https://cdn.plot.ly under
        # file:// origin, which QtWebEngine's Chromium blocks under CORS --
        # so instead, write the plotly.js build already bundled inside the
        # installed `plotly` package (no network access needed) to a stable
        # path once, and point every rendered page's <script src> at that
        # single file. Versioned by plotly's own version so an upgrade
        # regenerates it. This also means each render's HTML no longer has
        # to re-embed ~5MB of JS text inline on every single reload.
        path = os.path.join(
            tempfile.gettempdir(), f"dam_monitoring_plotlyjs_{plotly.__version__}.js"
        )
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(pyo.get_plotlyjs())
        return QUrl.fromLocalFile(path).toString()

    def _render_figure(self, fig):
        # This navigation supersedes anything an earlier in-flight load was
        # still waiting to patch in.
        self._pending_fig = None
        self._cleanup_tmp_file()
        fd, path = tempfile.mkstemp(suffix=".html")
        os.close(fd)

        plotlyjs_url = self._local_plotlyjs_url()

        # responsive=True makes the plot re-fit its container on resize.
        chart_html = fig.to_html(
            include_plotlyjs=False,
            full_html=False,
            config={"responsive": True},
            div_id=self._CHART_DIV_ID,
        )

        # The dashboard title is plain HTML in normal page flow (not part of
        # the Plotly figure) so it can never overlap the chart's legend on
        # resize -- Plotly does not reliably auto-expand its own margins to
        # avoid that (see build_figure's comment). The chart div is forced
        # to fill whatever space remains below the title, so it now also
        # resizes with window height, not just width.
        page_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<script src="{plotlyjs_url}"></script>
<style>
  html, body {{ margin: 0; padding: 0; height: 100%; }}
  body {{
    display: flex;
    flex-direction: column;
    font-family: sans-serif;
  }}
  h1 {{
    flex: 0 0 auto;
    margin: 0;
    padding: 10px 16px;
    font-size: 18px;
    text-align: center;
  }}
  .chart-container {{ flex: 1 1 auto; min-height: 0; }}
  .chart-container .plotly-graph-div {{
    width: 100% !important;
    height: 100% !important;
  }}
</style>
</head>
<body>
<h1>Sensor Monitoring Dashboard</h1>
<div class="chart-container">{chart_html}</div>
</body>
</html>"""

        with open(path, "w", encoding="utf-8") as f:
            f.write(page_html)

        self._tmp_html_path = path
        # Any in-place update requested before this navigation completes
        # must fall back to a full render instead of running JS against a
        # stale or not-yet-populated page.
        self._chart_ready = False
        self.web_view.load(QUrl.fromLocalFile(path))

    def _update_figure(self, fig):
        # In-place patch of the already-rendered chart via Plotly.react,
        # instead of a full page navigation -- keeps the existing DOM/JS
        # runtime alive (no white flash) and, combined with the figure's
        # constant `uirevision`, preserves zoom/pan/legend state across
        # auto-reloads and forecast/horizon updates. Only reachable for
        # reloads of the same open file (see load_csv), so the subplot
        # grid is guaranteed unchanged.
        fig_json = fig.to_json()
        js = (
            "(function(){"
            f"var gd = document.getElementById({self._CHART_DIV_ID!r});"
            "if (!gd) return;"
            f"var f = {fig_json};"
            "Plotly.react(gd, f.data, f.layout);"
            "})();"
        )
        self.web_view.page().runJavaScript(js)

    def _cleanup_tmp_file(self):
        if self._tmp_html_path and os.path.exists(self._tmp_html_path):
            try:
                os.remove(self._tmp_html_path)
            except OSError:
                # Still open by the QtWebEngine render process (e.g. a
                # navigation to it was just superseded and hasn't released
                # its handle yet on Windows) -- harmless to leave behind;
                # the OS temp dir gets reaped eventually.
                pass
        self._tmp_html_path = None


def main():
    app = QApplication(sys.argv)
    window = DamMonitoringWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
