#!/usr/bin/env python3
"""Small structural test that uses local ECB CSVs and no network crypto calls."""
from io import StringIO
import pandas as pd

from rates import clean_ecb_frame

daily = pd.read_csv(StringIO("Date, USD, Unnamed: 2\n22 October 2026,1.1205,\n"))
hist = pd.read_csv(StringIO(
    "Date, USD, DEM, Unnamed: 3\n"
    "2026-10-22,1.1205,N/A,\n"
    "1999-01-04,1.1789,1.9558,\n"
))

d = clean_ecb_frame(daily, daily=True)
h = clean_ecb_frame(hist, daily=False)

assert d.index.max() == pd.Timestamp("2026-10-22")
assert h.index.max() == pd.Timestamp("2026-10-22")
assert h.index.min() == pd.Timestamp("1999-01-04")
assert "USD" in h.columns
assert d.at[pd.Timestamp("2026-10-22"), "USD"] == 1.1205
assert pd.isna(h.at[pd.Timestamp("2026-10-22"), "DEM"])
assert all(not c.lower().startswith("unnamed") for c in h.columns)
print("ECB parser validation passed.")
