"""Consistent procurement charts with shared monthly column positions."""
from io import BytesIO

import numpy as np
import plotly.graph_objects as go
from matplotlib.figure import Figure
from matplotlib.ticker import StrMethodFormatter
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


def generation_duration_png(dispatch, max_charge_mw: float) -> bytes:
    """Render all hours as contiguous stacked columns in a static PNG."""
    sources = {"PV supply": "PV", "Wind supply": "Wind"}
    if max_charge_mw > 0:
        sources["BESS supply"] = "Battery discharge"
    generation = dispatch[list(sources)].sum(axis=1).to_numpy()
    ranked = dispatch.iloc[np.argsort(-generation, kind="stable")]
    edges = np.arange(len(ranked) + 1)

    fig = Figure(figsize=(10, 4), layout="constrained")
    ax = fig.subplots()
    bottom = np.zeros(len(ranked))
    for source, label in sources.items():
        top = bottom + ranked[source].to_numpy()
        ax.stairs(top, edges, baseline=bottom, fill=True, linewidth=0,
                  color=COLOURS[label], label=label)
        bottom = top
    if max_charge_mw > 0:
        ax.stairs(-ranked["Battery charge"].to_numpy(), edges, fill=True, linewidth=0,
                  color=COLOURS["Battery charge"], label="Battery charge")
        ax.axhline(0, color="#555555", linewidth=0.7)
    ax.plot(edges[:-1] + 0.5, ranked["Load"], color=COLOURS["Load"],
            linewidth=1.5, label="Load")
    ax.set(xlim=(0, len(ranked)), ylim=(-max_charge_mw, None), ylabel="Power (MW)",
           xlabel="Hours ranked by net generation (highest to lowest)")
    ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(axis="y", alpha=0.22)
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1), ncols=5, frameon=False)

    excess_gwh = ranked["Excess sold"].sum() / 1_000
    load_met = ranked["Renewable delivery"].sum() / ranked["Load"].sum()
    label_style = dict(ha="right", va="center", fontsize=10, zorder=5,
                       bbox=dict(facecolor="white", edgecolor="none", alpha=0.9, pad=3))
    ax.text(0.97, 0.92, f"{excess_gwh:,.2f} GWh excess sold",
            transform=ax.transAxes, **label_style)
    # At the right edge, the lowest generation hours leave room under load.
    right_load = ranked["Load"].iloc[int(0.85 * len(ranked)):].median()
    label_height = 0.5 * right_load if right_load > 0 else 0.25 * ranked["Load"].max()
    ax.text(0.97, label_height, f"{load_met:.1%} of load met",
            transform=ax.get_yaxis_transform(), **label_style)

    png = BytesIO()
    fig.savefig(png, format="png", dpi=150)
    return png.getvalue()


def storage_chart(dispatch):
    fig = go.Figure(go.Bar(x=dispatch.index, y=dispatch["Stored energy"], name="Stored energy",
                           marker_color=COLOURS["Battery discharge"]))
    fig.update_layout(height=280, yaxis_title="Stored energy (MWh)", xaxis_title="AEST", bargap=0,
                      margin=dict(l=65, r=20, t=20, b=40))
    fig.update_yaxes(tickformat=",.0f", hoverformat=",.0f")
    return fig
