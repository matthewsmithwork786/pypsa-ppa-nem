"""Consistent procurement charts with shared monthly column positions."""
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLOURS = {"PV": "#F4C542", "Wind": "#2E8B57", "Battery discharge": "#7B3FB2",
           "Battery charge": "#C5A3E0", "Spot purchases": "#9AA4AF", "Load": "#C23B3B"}


def monthly_chart(dispatch):
    monthly = dispatch.resample("MS").sum() / 1000
    months = monthly.index.strftime("%b").tolist()
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.13,
                        subplot_titles=("Generation & storage flows", "Load served"))
    for source in ("PV", "Wind", "Battery discharge", "Battery charge"):
        values = -monthly[source] if source == "Battery charge" else monthly[source]
        fig.add_trace(go.Bar(x=months, y=values, name=source, marker_color=COLOURS[source],
                             legendgroup=source), row=1, col=1)
    for source, column in [("PV", "PV to load"), ("Wind", "Wind to load"),
                           ("Battery discharge", "BESS to load"), ("Spot purchases", "Spot purchases")]:
        fig.add_trace(go.Bar(x=months, y=monthly[column], name=source, marker_color=COLOURS[source],
                             legendgroup=source, showlegend=source == "Spot purchases"), row=2, col=1)
    fig.update_xaxes(type="category", categoryorder="array", categoryarray=months)
    fig.update_yaxes(title_text="GWh", tickformat=",.0f", hoverformat=",.0f")
    fig.update_layout(height=700, barmode="relative", bargap=0.2, hovermode="x unified",
                      legend=dict(orientation="h", y=1.13, x=0), margin=dict(l=65, r=20, t=100, b=40))
    return fig


def hourly_chart(dispatch):
    fig = go.Figure()
    for source in ("PV", "Wind", "Battery discharge", "Spot purchases", "Battery charge"):
        values = -dispatch[source] if source == "Battery charge" else dispatch[source]
        fig.add_bar(x=dispatch.index, y=values, name=source, marker_color=COLOURS[source])
    fig.add_scatter(x=dispatch.index, y=dispatch.Load, name="Load", mode="lines",
                    line=dict(color=COLOURS["Load"], width=2, dash="dot"))
    fig.update_layout(barmode="relative", bargap=0, height=480, hovermode="x unified",
                      yaxis_title="Power (MW)", xaxis_title="AEST",
                      xaxis_rangeslider_visible=True, legend=dict(orientation="h", y=1.15),
                      margin=dict(l=65, r=20, t=65, b=40))
    fig.update_yaxes(tickformat=",.0f", hoverformat=",.0f")
    return fig


def storage_chart(dispatch):
    fig = go.Figure(go.Bar(x=dispatch.index, y=dispatch["Stored energy"], name="Stored energy",
                           marker_color=COLOURS["Battery discharge"]))
    fig.update_layout(height=280, yaxis_title="Stored energy (MWh)", xaxis_title="AEST", bargap=0,
                      margin=dict(l=65, r=20, t=20, b=40))
    fig.update_yaxes(tickformat=",.0f", hoverformat=",.0f")
    return fig
