"""Small 'download this chart's data as CSV' button, shared across result tabs."""
from __future__ import annotations

import pandas as pd
import streamlit as st


def csv_download_button(data, filename: str, key: str, label: str = "⬇️ Download chart data (CSV)") -> None:
    """Render a CSV download button for a Series/DataFrame behind a chart.

    Callers pass the same DataFrame/Series they handed to the chart builder,
    so the download always matches what's on screen."""
    df = data.to_frame() if isinstance(data, pd.Series) else data
    st.download_button(
        label, data=df.to_csv(index=True).encode("utf-8"),
        file_name=filename, mime="text/csv", key=key,
    )
