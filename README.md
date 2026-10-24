# ECB + CoinMarketCap daily exchange-rate snapshots

This repository generates matching cumulative CSV and Excel files every day:

```text
output/eurofxref_and_crypto_rates.YYYY.MM.DD.csv
output/eurofxref_and_crypto_rates.YYYY.MM.DD.xlsx
```

Each snapshot contains **all dates from the ECB historical start (1999-01-04)
through that snapshot date**, newest first.

Both formats contain the same dates and currency columns, with no empty separator
column. Excel uses a `Rates` sheet with numeric rates, real date cells displayed as
`YYYY-MM-DD`, column filters, and a frozen header row and date column. Missing
values stay blank and download failures remain the text `ERROR`. CSV files remain
the cache used by subsequent daily runs. The daily action commits both formats.

## Output semantics

The file preserves the ECB convention consistently across fiat and crypto:

> **each rate is the number of units of that currency/asset equal to 1 EUR**

So:

- `USD = 1.1205` means `1 EUR = 1.1205 USD`
- `BTC = 0.0000143` means `1 EUR = 0.0000143 BTC`

This consistency makes downstream conversion simple:

```python
amount_eur = amount_in_original_currency / rate
```

### Missing values

- **Fiat blank**: ECB did not publish a reference rate for that calendar date
  (typically weekends, TARGET/bank holidays, or a currency not yet/any longer
  covered). Fiat values are **not carried forward**.
- **Crypto blank**: the date is before CoinMarketCap's `first_historical_data`
  for that coin, or CoinMarketCap genuinely returned no observation.
- **Crypto `ERROR`**: the script attempted to obtain that crypto period but the
  request failed or appeared to be blocked/rate-limited after three attempts.

A later successful run retries both missing and `ERROR` crypto cells.
Once a coin's historical start is known, cached cells before that date are
cleared, including errors left by an earlier failed asset lookup.

### USD retry when EUR history is missing

EUR quotes are preferred. After the EUR download attempts, the script retries
unresolved dates in USD when a valid **same-day ECB USD reference rate** exists.
This applies to both the official API and the public website endpoint, including
partial EUR responses and failed EUR requests. Existing cached rates and valid
EUR observations are preserved.

The output convention stays **coins per EUR**:

```text
coins per EUR = ECB USD per EUR / CoinMarketCap USD per coin
```

For example, an ECB rate of 1.0561 USD per EUR and a coin price of 20,000 USD
produce 0.000052805 coins per EUR. This uses two reference sources, so the result
may differ slightly from a direct CoinMarketCap EUR quote.

ECB rates are never carried forward for this conversion. If the same-day USD
reference rate is missing (such as weekends or holidays), no USD retry is made.
If neither source returns an observation, the cell stays blank; a failed request
leaves `ERROR`. Both are retried on later runs. Today's final daily price may not
be available yet.

The USD retry repairs source gaps such as June 28–29, 2022, where CoinMarketCap's
EUR history is missing observations present in its USD history. It runs during
the normal daily update; older snapshot files are not rewritten automatically.

## Why missed GitHub Actions runs are repaired automatically

The script does **not** assume yesterday's Action succeeded.

Every run:

1. downloads the full ECB historical ZIP (`eurofxref-hist.zip`);
2. downloads the tiny latest ECB daily ZIP (`eurofxref.zip`) and overlays its
   newest row;
3. creates a complete daily calendar through the current date;
4. uses the latest existing output snapshot as a crypto cache;
5. detects every missing/`ERROR` crypto date;
6. downloads those missing crypto windows;
7. writes a new cumulative snapshot.

Therefore, if GitHub Actions fails for 10 days, the next successful run fills the
10-day window rather than only downloading one day.

## Crypto source

The configured set is 20 major cryptocurrencies plus 5 major stablecoins in
`assets.json`.

All 25 configured assets specify `cmc_id` and `first_historical_date` in
`assets.json`, verified against CoinMarketCap's asset metadata
(`actualTimeStart`). Daily runs use these fixed IDs directly and make no asset
map requests. Historical price downloads can still fail or be rate-limited;
missing and `ERROR` cells are retried on later runs.

IDs and start dates were verified on 2026-10-24 using CoinMarketCap's
`/data-api/v3/cryptocurrency/detail?id=<cmc_id>` metadata endpoint. The `TON`
output column retains its existing name and uses ID 11419, currently listed
by CoinMarketCap as "Gram (prev. Toncoin)" (`GRAM`).

For newly added assets without both fields, the script falls back to the asset
map, paginating to include newer assets and using the authenticated API when
`CMC_API_KEY` is set. Add a verified ID and historical start date to keep new
assets independent of that lookup too.

### Recommended: official CoinMarketCap API

Create a GitHub repository secret named:

```text
CMC_API_KEY
```

When present, the script uses CoinMarketCap's official historical quotes API and
requests EUR conversion directly.

**Important:** how far back the API actually allows you to query depends on your
CoinMarketCap plan. The script asks for the full available range, but it cannot
override plan limits.

### Free fallback

If `CMC_API_KEY` is absent, the script uses CoinMarketCap's public website
historical-data endpoint. This endpoint is not the documented Pro API and may
change or apply anti-bot controls.

To reduce the chance of blocking, the script:

- sends a descriptive User-Agent;
- makes requests serially (no concurrency);
- pauses between requests;
- requests only missing periods;
- uses moderate 180-day chunks;
- retries at most three times with backoff;
- uses explicit connect/read timeouts.

If a crypto window is blocked or fails after the three attempts, the script does
not fabricate or carry forward a rate: it writes `ERROR` for the affected crypto
cells.

## ECB behavior

The ECB daily CSV can use a human-readable date such as:

```text
22 October 2026
```

while the historical CSV uses ISO dates:

```text
2026-10-22
```

`rates.py` normalizes both to `YYYY-MM-DD`.

The historical ECB file is the recovery source for missed runs. The daily file
is used as an additional freshness/overlay source.

## GitHub Actions

The workflow runs daily at **16:30 Europe/Amsterdam** (CET in winter, CEST in
summer), half an hour after the ECB's usual 16:00 publication time. The rate-setting
procedure around 14:10 is earlier than publication; see the
[ECB reference-rate page](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html).
GitHub's schedule uses `cron: "30 16 * * *"` with `timezone: "Europe/Amsterdam"`
to follow daylight saving time automatically. GitHub may delay scheduled starts.
The workflow also runs on weekends for crypto data and can be started manually
from the Actions tab.

The workflow requires:

```yaml
permissions:
  contents: write
```

so that it can commit each generated snapshot back to `output/`.

If branch protection prevents direct pushes, change the workflow to open a pull
request instead.

## Local run

```bash
python -m pip install -r requirements.txt
python rates.py
```

For testing with local ECB files:

```bash
python rates.py \
  --run-date 2026-10-23 \
  --ecb-daily-file eurofxref.csv \
  --ecb-hist-file eurofxref-hist.csv
```

## Repository structure

```text
.
├── rates.py
├── assets.json
├── requirements.txt
├── README.md
├── .github/
│   └── workflows/
│       └── update-rates.yml
└── output/
    └── .gitkeep
```

## Operational note

For an air-gapped downstream environment, treat each dated CSV as an immutable
reference-data package. The filename itself identifies the snapshot date. Keep
the original transaction amount/currency and the rate/snapshot used for each
conversion so that later backfills are auditable.
