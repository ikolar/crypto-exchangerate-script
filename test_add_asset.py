"""URL identity, ticker collision, and safe config update checks."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from add_asset import add_asset, currency_url, resolve_url
from rates import DownloadFailed, resolve_assets


URL = "https://coinmarketcap.com/currencies/tontoken/"


def page(ident=6890, ticker="TON", name="TON Token", slug="tontoken"):
    detail = {"id": ident, "symbol": ticker, "name": name, "slug": slug,
              "actualTimeStart": "2020-09-04T06:30:00.000Z"}
    data = {"props": {"pageProps": {"detailRes": {"detail": detail}}}}
    return Mock(url=URL, text=f"<script type='application/json' id='__NEXT_DATA__'>{json.dumps(data)}</script>")


class AddAssetTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "assets.json"
        self.config = {
            "major_cryptocurrencies": [{
                "symbol": "TON", "name": "Toncoin", "cmc_id": 11419,
                "first_historical_date": "2021-08-26",
                "cmc_url": "https://coinmarketcap.com/currencies/gram/",
            }],
            "stablecoins": [],
        }
        self.path.write_text(json.dumps(self.config))
        self.original = self.path.read_bytes()

    def test_url_validation_and_pasted_trailing_comma(self):
        self.assertEqual(currency_url(URL + ","), URL)
        self.assertEqual(currency_url("https://www.coinmarketcap.com/currencies/tontoken?foo=1#chart"), URL)
        for invalid in ("https://evil.example/currencies/tontoken/", "http://coinmarketcap.com/currencies/tontoken/", "https://coinmarketcap.com@evil.example/currencies/tontoken/", "https://coinmarketcap.com/currencies/tontoken/extra", "https://coinmarketcap.com/"):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                currency_url(invalid)

    def test_resolves_exact_page_identity(self):
        with patch("add_asset.request_with_retries", return_value=page()) as request:
            entry = resolve_url(Mock(), URL)
        self.assertEqual(request.call_args.args[1], URL)
        self.assertEqual(request.call_args.kwargs["headers"]["Accept"], "text/html,application/xhtml+xml")
        self.assertEqual(entry["cmc_id"], 6890)
        self.assertEqual(entry["first_historical_date"], "2020-09-04")
        self.assertEqual(entry["cmc_url"], URL)

    def test_collision_does_not_replace_existing_ton_or_change_its_id(self):
        with patch("add_asset.request_with_retries", return_value=page()):
            entry, added = add_asset(URL, self.path)
        self.assertTrue(added)
        self.assertEqual(entry["symbol"], "TON_6890")
        self.assertEqual(entry["cmc_symbol"], "TON")
        config = json.loads(self.path.read_text())
        self.assertEqual(config["major_cryptocurrencies"][0], self.config["major_cryptocurrencies"][0])
        with patch("rates.request_with_retries") as request, patch("rates.log"):
            assets = resolve_assets(Mock(), config["major_cryptocurrencies"])
        self.assertEqual([(a.symbol, a.cmc_id) for a in assets], [("TON", 11419), ("TON_6890", 6890)])
        request.assert_not_called()

    def test_duplicate_id_is_noop_even_after_a_rename(self):
        with patch("add_asset.request_with_retries", return_value=page(11419, "GRAM", "Gram (prev. Toncoin)", "gram")):
            entry, added = add_asset(URL, self.path)
        self.assertFalse(added)
        self.assertEqual(entry["symbol"], "TON")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_same_names_are_allowed_and_group_and_alias_are_respected(self):
        with patch("add_asset.request_with_retries", return_value=page(name="Toncoin")):
            entry, added = add_asset(URL, self.path, group="stablecoins", symbol="ton_token")
        self.assertTrue(added)
        self.assertEqual(entry["symbol"], "TON_TOKEN")
        self.assertEqual(json.loads(self.path.read_text())["stablecoins"], [entry])

    def test_dry_run_and_conflicting_alias_leave_file_untouched(self):
        with patch("add_asset.request_with_retries", return_value=page()):
            entry, added = add_asset(URL, self.path, dry_run=True)
            self.assertTrue(added)
            self.assertEqual(entry["symbol"], "TON_6890")
            with self.assertRaises(ValueError):
                add_asset(URL, self.path, symbol="TON")
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_fiat_header_collision_gets_a_unique_column(self):
        output = self.path.parent / "output"
        output.mkdir()
        (output / "eurofxref_and_crypto_rates.2026.10.28.csv").write_text("Date,USD,TON\n")
        with patch("add_asset.request_with_retries", return_value=page(42, "USD")):
            entry, _ = add_asset(URL, self.path)
        self.assertEqual(entry["symbol"], "USD_42")

    def test_bad_page_or_download_failure_does_not_change_config(self):
        for response in (Mock(url=URL, text="challenge"), Mock(url="https://evil.example/", text="")):
            with patch("add_asset.request_with_retries", return_value=response), self.assertRaises(ValueError):
                add_asset(URL, self.path)
        with patch("add_asset.request_with_retries", side_effect=DownloadFailed("blocked")), self.assertRaises(DownloadFailed):
            add_asset(URL, self.path)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_concurrent_config_edit_is_preserved(self):
        def resolve(*args):
            self.path.write_text("User changed this")
            return {"symbol": "TON", "name": "TON Token", "cmc_id": 6890}

        with patch("add_asset.resolve_url", side_effect=resolve), self.assertRaises(ValueError):
            add_asset(URL, self.path)
        self.assertEqual(self.path.read_text(), "User changed this")


if __name__ == "__main__":
    unittest.main()
