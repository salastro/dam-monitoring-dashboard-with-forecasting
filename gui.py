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
import tempfile

from PyQt5.QtCore import QFileSystemWatcher, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSlider,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtWebEngineWidgets import QWebEngineView

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

    def __init__(self, df, path, columns, horizon_days):
        super().__init__()
        self._df = df
        self._path = path
        self._columns = columns
        self._horizon_days = horizon_days

    def run(self):
        def on_column_done(column, result, error):
            self.column_ready.emit(self._path, column, result, error)

        try:
            forecasts, errors = forecast_all(
                self._df, self._columns, horizon_days=self._horizon_days,
                on_column_done=on_column_done,
            )
        except Exception as exc:
            self.failed.emit(self._path, str(exc))
            return
        self.finished_ok.emit(self._path, forecasts, errors)


class DamMonitoringWindow(QMainWindow):
    # Fixed DOM id for the chart div, so in-place updates (Plotly.react) can
    # always find it regardless of which render produced the current page.
    _CHART_DIV_ID = "dam-chart"

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
        self.web_view.loadFinished.connect(self._on_load_finished)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
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
        self.horizon_slider = QSlider(Qt.Horizontal)
        self.horizon_slider.setMinimum(1)
        self.horizon_slider.setMaximum(MAX_HORIZON_DAYS)
        self.horizon_slider.setValue(DEFAULT_HORIZON_DAYS)
        self.horizon_slider.setFixedWidth(160)
        self.horizon_slider.valueChanged.connect(self._on_horizon_slider_changed)
        toolbar.addWidget(self.horizon_slider)
        self.horizon_label = QLabel(f"{DEFAULT_HORIZON_DAYS}d")
        self.horizon_label.setFixedWidth(36)
        toolbar.addWidget(self.horizon_label)

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

        self._df = df
        self._build_and_render(df, in_place=in_place and not is_new_file)

        self.path_label.setText(path)
        self._csv_path = path
        self._watch_path(path)
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

    # --- Background Prophet forecasting --------------------------------

    def _request_forecast_refit(self, df, path: str):
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

        worker = ForecastWorker(df, path, FORECAST_COLUMNS, MAX_HORIZON_DAYS)
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
            self._build_and_render(self._df, in_place=True)
        # else: this column's error is folded into the summary message
        # _on_forecast_ready shows once the whole fit cycle finishes.

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

    def _render_figure(self, fig):
        self._cleanup_tmp_file()
        fd, path = tempfile.mkstemp(suffix=".html")
        os.close(fd)

        # Embed plotly.js inline: a "cdn" reference would be fetched from
        # https://cdn.plot.ly under file:// origin, which QtWebEngine's
        # Chromium blocks under CORS.
        # responsive=True makes the plot re-fit its container on resize.
        chart_html = fig.to_html(
            include_plotlyjs=True,
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
            os.remove(self._tmp_html_path)
        self._tmp_html_path = None


def main():
    app = QApplication([])
    window = DamMonitoringWindow()
    window.show()
    app.exec_()


if __name__ == "__main__":
    main()
