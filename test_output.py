"""Verify CSV/Excel parity, blank-column removal, and spreadsheet cell types."""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import load_workbook
import pandas as pd

from rates import write_output


class OutputTests(unittest.TestCase):
    def test_matching_csv_and_excel(self):
        dates = pd.date_range("2026-10-22", periods=3)
        fiat = pd.DataFrame({
            "USD": [1.12, 1.13, float("nan")],
            "Unnamed: 42": [float("nan")] * 3,
            " ": [float("nan")] * 3,
        }, index=dates)
        crypto = pd.DataFrame({"BTC": ["0.00001", "ERROR", ""]}, index=dates)
        with TemporaryDirectory() as directory:
            path = write_output(fiat, crypto, dates[-1], Path(directory))
            csv = pd.read_csv(path, dtype=str, keep_default_na=False)
            self.assertEqual(csv.columns.tolist(), ["Date", "USD", "BTC"])
            self.assertEqual(csv["Date"].tolist(), ["2026-10-24", "2026-10-23", "2026-10-22"])
            workbook = load_workbook(path.with_suffix(".xlsx"))
            sheet = workbook["Rates"]
            rows = list(sheet.values)
            self.assertEqual(list(rows[0]), csv.columns.tolist())
            self.assertEqual(rows[1], (datetime(2026, 10, 24), None, None))
            self.assertEqual(rows[2], (datetime(2026, 10, 23), 1.13, "ERROR"))
            self.assertEqual(rows[3], (datetime(2026, 10, 22), 1.12, 0.00001))
            self.assertEqual(sheet.freeze_panes, "B2")
            self.assertEqual(sheet.auto_filter.ref, "A1:C4")
            self.assertEqual(sheet["A2"].number_format, "YYYY-MM-DD")
            self.assertEqual(sheet["C4"].number_format, "0.##############################")
            self.assertEqual(sheet["B3"].number_format, "0.##############################")
            workbook.close()


if __name__ == "__main__":
    unittest.main()
