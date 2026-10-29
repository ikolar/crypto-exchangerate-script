#!/usr/bin/env python3
"""Add an asset by its unique CoinMarketCap currency URL, never by ticker search."""

import argparse
import csv
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from rates import ASSET_CONFIG, BlockedDownload, DownloadFailed, make_session, request_with_retries


def currency_url(url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(url.strip().rstrip(",."))
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() not in ("coinmarketcap.com", "www.coinmarketcap.com")
        or not re.fullmatch(r"/currencies/[a-z0-9-]+/?", parsed.path)
    ):
        raise ValueError("Use an HTTPS CoinMarketCap currency URL, e.g. https://coinmarketcap.com/currencies/tontoken/")
    return f"https://coinmarketcap.com{parsed.path.rstrip('/')}/"


class PageData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("id") == "__NEXT_DATA__":
            self.active = True

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False


def resolve_url(session, url: str) -> dict:
    response = request_with_retries(
        session, currency_url(url), headers={"Accept": "text/html,application/xhtml+xml"},
    )
    currency_url(response.url)  # Reject redirects outside CMC currency pages.
    parser = PageData()
    parser.feed(response.text)
    try:
        page = json.loads("".join(parser.parts))
        detail = page["props"]["pageProps"]["detailRes"]["detail"]
        ident = int(detail["id"])
        slug = detail["slug"]
        symbol = detail["symbol"].strip().upper()
        name = detail["name"].strip()
        first = datetime.fromisoformat(detail["actualTimeStart"].replace("Z", "+00:00"))
        if ident <= 0 or not re.fullmatch(r"[a-z0-9-]+", slug) or not symbol or not name or first.tzinfo is None:
            raise ValueError("Invalid coin metadata")
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("CMC page has no valid coin ID and historical start date; assets.json was not changed") from exc
    return {
        "symbol": symbol,
        "name": name,
        "cmc_id": ident,
        "first_historical_date": first.astimezone(timezone.utc).date().isoformat(),
        "cmc_url": f"https://coinmarketcap.com/currencies/{slug}/",
    }


def add_asset(url, config_path=ASSET_CONFIG, *, group="major_cryptocurrencies", symbol=None, dry_run=False):
    config_path = Path(config_path)
    original = config_path.read_text(encoding="utf-8")
    config = json.loads(original)
    groups = ("major_cryptocurrencies", "stablecoins")
    if group not in groups or any(not isinstance(config.get(key), list) for key in groups):
        raise ValueError("Config must contain major_cryptocurrencies and stablecoins lists")
    entry = resolve_url(make_session(), url)
    existing = config[groups[0]] + config[groups[1]]
    for asset in existing:
        if str(asset.get("cmc_id")) == str(entry["cmc_id"]):
            return asset, False

    used = {asset["symbol"].upper() for asset in existing} | {"DATE"}
    # Keep new crypto columns distinct from fiat columns in existing snapshots too.
    snapshots = sorted((config_path.parent / "output").glob("eurofxref_and_crypto_rates.*.csv"))
    if snapshots:
        with snapshots[-1].open(newline="", encoding="utf-8") as handle:
            used.update(value.upper() for value in next(csv.reader(handle)))
    ticker = entry["symbol"]
    column = symbol.strip().upper() if symbol is not None else ticker
    if symbol is None and column in used:
        column = f"{ticker}_{entry['cmc_id']}"
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", column) or column in used:
        raise ValueError("Output symbol is invalid or already used; choose a unique column with --symbol")
    entry["symbol"] = column
    entry["cmc_symbol"] = ticker
    config[group].append(entry)
    if not dry_run:
        if config_path.read_text(encoding="utf-8") != original:
            raise ValueError("Config changed during the lookup; run the command again")
        staged = None
        try:
            with NamedTemporaryFile("w", dir=config_path.parent, suffix=".json.tmp", encoding="utf-8", delete=False) as handle:
                staged = Path(handle.name)
                json.dump(config, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            staged.replace(config_path)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
    return entry, True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="https://coinmarketcap.com/currencies/<slug>/")
    parser.add_argument("--config", type=Path, default=ASSET_CONFIG)
    parser.add_argument("--group", choices=("major_cryptocurrencies", "stablecoins"), default="major_cryptocurrencies")
    parser.add_argument("--symbol", help="Unique CSV/Excel column name; defaults to the ticker, or TICKER_CMCID on collision")
    parser.add_argument("--dry-run", action="store_true", help="Resolve and preview without changing assets.json")
    args = parser.parse_args()
    try:
        entry, added = add_asset(args.url, args.config, group=args.group, symbol=args.symbol, dry_run=args.dry_run)
    except (ValueError, OSError, BlockedDownload, DownloadFailed) as exc:
        parser.exit(1, f"Error: {exc}\n")
    action = ("Would add" if args.dry_run else "Added") if added else "Already configured"
    print(f"{action}: {entry['name']} (CMC {entry['cmc_id']}) as {entry['symbol']}")
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
