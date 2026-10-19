"""Shared units, numeric formatting and chart explanations for result views."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def number_table(frame: pd.DataFrame, decimals: int = 2):
    return frame.style.format({
        column: f"{{:,.{decimals}f}}"
        for column in frame.select_dtypes(include="number").columns
    }, na_rep="—")


def aggregate_units(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert aggregate energy to GWh and cash to A$ million, with matching labels."""
    result = frame.copy()
    for column in frame.columns:
        if column.endswith("(MWh)"):
            result[column] /= 1e3
            result.rename(columns={column: column.replace("(MWh)", "(GWh)")}, inplace=True)
        elif column.endswith("(A$)"):
            result[column] /= 1e6
            result.rename(columns={column: column.replace("(A$)", "(A$ million)")}, inplace=True)
    return result


def chart_help(title: str, explanation: str) -> None:
    with st.popover(f"? {title}"):
        st.markdown(explanation)


def result_chart(fig, **kwargs) -> None:
    for axis in fig.select_yaxes():
        if not axis.tickformat:
            axis.update(tickformat=",.1f", separatethousands=True)
    st.plotly_chart(fig, **kwargs)
