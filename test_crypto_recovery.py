"""Offline regression checks for asset resolution and cached history repair."""

import unittest
from unittest.mock import Mock, patch

import pandas as pd

from rates import (
    Asset, CMC_MAP_PRO, DownloadFailed, build_crypto, load_asset_config,
    resolve_assets, fetch_crypto_window,
)


class CryptoRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.logs = patch("rates.log").start()
        self.addCleanup(patch.stopall)

    def test_configured_assets_survive_map_failure(self):
        config = load_asset_config() + [{"symbol": "NEW", "name": "New Coin"}]
        with patch("rates.request_with_retries", side_effect=DownloadFailed("429")):
            assets = {a.symbol: a for a in resolve_assets(Mock(), config)}
        for symbol, ident, first in (
            ("HYPE", 32196, "2024-11-29"),
            ("USDE", 29470, "2023-12-31"),
            ("USDS", 33039, "2024-09-19"),
        ):
            self.assertEqual(assets[symbol].cmc_id, ident)
            self.assertEqual(assets[symbol].first_historical_date, pd.Timestamp(first))

    def test_configured_assets_need_no_map_request(self):
        config = load_asset_config()
        with patch("rates.request_with_retries") as request:
            assets = resolve_assets(Mock(), config)
        self.assertEqual(len(assets), len(config))
        self.assertEqual(len({a.cmc_id for a in assets}), len(assets))
        for asset in assets:
            self.assertGreater(asset.cmc_id, 0)
            self.assertIsNotNone(asset.first_historical_date)
        request.assert_not_called()

    def test_map_paginates_and_uses_api_key(self):
        first_page = [{"id": i, "symbol": "OTHER", "name": "Other"} for i in range(5000)]
        second_page = [{
            "id": 6000, "symbol": "NEW", "name": "New Coin",
            "first_historical_data": "2024-01-01T00:00:00Z",
        }]
        starts = []

        def response(session, url, **kwargs):
            self.assertEqual(url, CMC_MAP_PRO)
            self.assertEqual(kwargs["headers"], {"X-CMC_PRO_API_KEY": "test-key"})
            starts.append(kwargs["params"]["start"])
            return Mock(json=lambda: {"data": first_page if len(starts) == 1 else second_page})

        with patch.dict("os.environ", {"CMC_API_KEY": "test-key"}), patch(
            "rates.request_with_retries", side_effect=response,
        ):
            assets = resolve_assets(Mock(), [{"symbol": "NEW", "name": "New Coin"}])
        self.assertEqual(starts, [1, 5001])
        self.assertEqual(assets[0].cmc_id, 6000)

    def test_repair_clears_prelaunch_errors_and_preserves_cached_rates(self):
        calendar = pd.date_range("2024-11-28", periods=4)
        previous = pd.DataFrame({"HYPE": ["ERROR", "ERROR", "0.25", ""]}, index=calendar)
        asset = Asset("HYPE", "Hyperliquid", 32196, calendar[1])
        with patch("rates.fetch_crypto_window", return_value=(
            {calendar[1]: 2.0, calendar[3]: 8.0}, False,
        )) as fetch:
            result = build_crypto(Mock(), calendar, previous, [asset], calendar[-1])
        self.assertEqual(result["HYPE"].tolist(), ["", "0.5", "0.25", "0.125"])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[0].args[2:4], (calendar[1], calendar[1]))
        self.assertEqual(fetch.call_args_list[1].args[2:4], (calendar[3], calendar[3]))

    def test_successful_empty_retry_clears_stale_error(self):
        calendar = pd.date_range("2024-11-29", periods=2)
        previous = pd.DataFrame({"HYPE": ["0.5", "ERROR"]}, index=calendar)
        asset = Asset("HYPE", "Hyperliquid", 32196, calendar[0])
        with patch("rates.fetch_crypto_window", return_value=({}, False)):
            result = build_crypto(Mock(), calendar, previous, [asset], calendar[-1])
        self.assertEqual(result["HYPE"].tolist(), ["0.5", ""])

    def test_usd_fallback_fills_only_missing_eur_with_same_day_fx(self):
        calendar = pd.date_range("2022-06-27", periods=4)
        previous = pd.DataFrame({"BTC": ["0.5", "ERROR", "", ""]}, index=calendar)
        fx = pd.Series([1.1, 1.2, float("nan"), 1.3], index=calendar)
        asset = Asset("BTC", "Bitcoin", 1, calendar[0])
        with patch("rates.fetch_crypto_window", side_effect=[
            ({calendar[3]: 4.0}, False),
            ({calendar[1]: 2.0}, False),
        ]) as fetch:
            result = build_crypto(Mock(), calendar, previous, [asset], calendar[-1], fx)
        self.assertEqual(result["BTC"].tolist(), ["0.5", "0.6", "", "0.25"])
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args.args[2:4], (calendar[1], calendar[1]))
        self.assertEqual(fetch.call_args.kwargs, {"currency": "USD"})

    def test_usd_fallback_recovers_failed_eur_download(self):
        calendar = pd.date_range("2022-06-28", periods=1)
        asset = Asset("BTC", "Bitcoin", 1, calendar[0])
        with patch("rates.fetch_crypto_window", side_effect=[
            ({}, True), ({calendar[0]: 20000.0}, False),
        ]):
            result = build_crypto(
                Mock(), calendar, pd.DataFrame(), [asset], calendar[-1],
                pd.Series([1.0561], index=calendar),
            )
        self.assertAlmostEqual(float(result.iloc[0, 0]), 1.0561 / 20000)

    def test_unavailable_usd_remains_retryable(self):
        calendar = pd.date_range("2022-06-28", periods=1)
        asset = Asset("BTC", "Bitcoin", 1, calendar[0])
        for failed, expected in ((False, ""), (True, "ERROR")):
            with self.subTest(failed=failed), patch("rates.fetch_crypto_window", side_effect=[
                ({}, False), ({}, failed),
            ]):
                result = build_crypto(
                    Mock(), calendar, pd.DataFrame(), [asset], calendar[-1],
                    pd.Series([1.0561], index=calendar),
                )
                self.assertEqual(result.iloc[0, 0], expected)

    def test_usd_quote_requests_and_parsing_for_both_sources(self):
        day = pd.Timestamp("2022-06-28")
        asset = Asset("BTC", "Bitcoin", 1, day)
        for api_key in (None, "test-key"):
            payload = {"status": {"error_code": "0"}, "data": (
                {"1": {"quotes": [{"timestamp": "2022-06-28T23:59:00Z", "quote": {"USD": {"price": 20000}}}]}}
                if api_key else
                {"quotes": [{"timeClose": "2022-06-28T23:59:59Z", "quote": {"close": 20000}}]}
            )}
            with self.subTest(api_key=api_key), patch("rates.time.sleep"), patch(
                "rates.request_with_retries", return_value=Mock(json=lambda: payload),
            ) as request:
                prices, failed = fetch_crypto_window(Mock(), asset, day, day, api_key, currency="USD")
                self.assertEqual(prices, {day: 20000})
                self.assertFalse(failed)
                params = request.call_args.kwargs["params"]
                self.assertEqual(params["convert"] if api_key else params["convertId"], "USD" if api_key else "2781")


if __name__ == "__main__":
    unittest.main()
