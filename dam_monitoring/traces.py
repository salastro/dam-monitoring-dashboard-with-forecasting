import plotly.graph_objects as go


def add_single_trace(fig, df, x, row, col, label, color, yaxis_title):
    fig.add_trace(
        go.Scattergl(
            x=df[x], y=df[col], name=label,
            line=dict(color=color, width=1.6),
            mode="lines",
        ),
        row=row, col=1, secondary_y=False
    )
    fig.update_yaxes(title_text=yaxis_title, row=row, col=1, secondary_y=False)


def add_pair_twiny_traces(
    fig, df, x, row,
    col_a, label_a, color_a, yaxis_title_a,
    col_b, label_b, color_b, yaxis_title_b,
):
    fig.add_trace(
        go.Scattergl(
            x=df[x], y=df[col_a], name=label_a,
            line=dict(color=color_a, width=1.6),
            mode="lines",
        ),
        row=row, col=1, secondary_y=False
    )
    fig.add_trace(
        go.Scattergl(
            x=df[x], y=df[col_b], name=label_b,
            line=dict(color=color_b, width=1.6),
            mode="lines",
        ),
        row=row, col=1, secondary_y=True
    )
    fig.update_yaxes(title_text=yaxis_title_a, row=row, col=1, secondary_y=False)
    fig.update_yaxes(title_text=yaxis_title_b, row=row, col=1, secondary_y=True)
