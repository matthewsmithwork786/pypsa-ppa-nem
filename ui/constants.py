"""Shared UI constants used across tabs.

Pure data, no Streamlit imports, so any module can import this safely.
"""

# Label -> period in minutes for the NEM resolution pickers (shared by the
# Optimisation tab's reference-period selector and the Custom Data template).
NEM_RESOLUTION_MINUTES = {"1 hour": 60, "30 minutes": 30, "5 minutes": 5}

# Row count above which a generated/uploaded timeseries is flagged as slow to
# render in the browser (full-year 5-min = 105 120 rows).
LARGE_TIMESERIES_ROWS = 50_000
