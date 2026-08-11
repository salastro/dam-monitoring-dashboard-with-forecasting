import pandas as pd


def load_data(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp")
    return df


def downsample_for_plot(df: pd.DataFrame, max_points: int = 3000) -> pd.DataFrame:
    """Reduce points for faster rendering while keeping temporal coverage."""
    n = len(df)
    if n <= max_points:
        return df

    step = max(1, n // max_points)
    sampled = df.iloc[::step].copy()

    # Keep the last point to preserve the latest state in the chart.
    if sampled.index[-1] != df.index[-1]:
        sampled = pd.concat([sampled, df.iloc[[-1]]], axis=0)

    return sampled
