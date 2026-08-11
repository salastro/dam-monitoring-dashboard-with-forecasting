#!/usr/bin/env python
# coding: utf-8
"""Append synthetic sensor rows to the dam monitoring CSV at an interval.

Meant for testing gui.py's file-watcher auto-reload: run this alongside the
GUI (with the CSV already open) and watch the dashboard refresh itself as
new rows land.

Usage:
    python3 simulate_live_data.py
    python3 simulate_live_data.py --csv "egypt_dam_monitoring_2years_hourly (1).csv" --interval 3 --rows 20
"""

import argparse
import csv
import random
import time
from datetime import datetime, timedelta

# Column order must match the dashboard's expected CSV schema.
COLUMNS = [
    "timestamp",
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

# Per-column (low, high, step_stddev) for a bounded random walk, roughly
# matching the ranges seen in the existing dataset.
BOUNDS = {
    "water_level_upstream_m": (168.0, 182.0, 0.15),
    "water_level_downstream_m": (100.0, 112.0, 0.15),
    "discharge_m3s": (880.0, 1100.0, 8.0),
    "disp_horiz_mm": (8.0, 14.0, 0.1),
    "disp_vert_mm": (-6.0, -2.0, 0.1),
    "seismic_acc_g": (0.00005, 0.0006, 0.00003),
    "water_temp_c": (15.0, 28.0, 0.2),
    "pH": (7.2, 8.2, 0.03),
    "TDS_mgL": (190.0, 230.0, 1.5),
    "DO_mgL": (7.5, 9.5, 0.05),
    "chlorophyll_a_ugL": (2.0, 8.0, 0.1),
}


def read_last_row(csv_path: str) -> dict:
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        last = None
        for row in reader:
            last = row
        if last is None:
            raise ValueError(f"{csv_path} has no data rows to continue from")
        return dict(zip(header, last))


def walk(value: float, low: float, high: float, stddev: float) -> float:
    value += random.gauss(0, stddev)
    return min(max(value, low), high)


def make_next_row(prev: dict, timestamp: datetime) -> dict:
    row = {"timestamp": f"{timestamp.month}/{timestamp.day}/{timestamp.year} {timestamp.hour}:00"}
    for col, (low, high, stddev) in BOUNDS.items():
        row[col] = walk(float(prev[col]), low, high, stddev)
    return row


def format_row(row: dict) -> list:
    return [row["timestamp"]] + [f"{row[col]:.5g}" for col in COLUMNS[1:]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", default="egypt_dam_monitoring_2years_hourly (1).csv",
        help="CSV file to append to",
    )
    parser.add_argument(
        "--interval", type=float, default=3.0,
        help="seconds to wait between appended rows (default: 3)",
    )
    parser.add_argument(
        "--rows", type=int, default=0,
        help="number of rows to append; 0 runs forever until Ctrl+C (default: 0)",
    )
    parser.add_argument(
        "--step-hours", type=float, default=1.0,
        help="hours to advance the timestamp per appended row (default: 1)",
    )
    args = parser.parse_args()

    prev = read_last_row(args.csv)
    timestamp = datetime.strptime(prev["timestamp"], "%m/%d/%Y %H:%M")

    count = 0
    try:
        while args.rows == 0 or count < args.rows:
            timestamp += timedelta(hours=args.step_hours)
            row = make_next_row(prev, timestamp)

            with open(args.csv, "a", newline="") as f:
                csv.writer(f).writerow(format_row(row))

            count += 1
            print(f"[{count}] appended row for {row['timestamp']}")
            prev = row

            if args.rows == 0 or count < args.rows:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
