# ECB + CoinMarketCap daily exchange-rate snapshots

This repository generates matching cumulative CSV and Excel files every day:

```text
output/eurofxref_and_crypto_rates.YYYY.MM.DD.csv
output/eurofxref_and_crypto_rates.YYYY.MM.DD.xlsx
```

Each snapshot contains **all dates from the ECB historical start (1999-01-04)
through the day before the snapshot's run date**, newest first. The filename's
date is the **run date**, using Europe/Amsterdam local time, not the last data date.

### Completed days only

Both CSV and Excel end yesterday. Crypto uses completed daily closing prices,
not live intraday prices, so the unfinished current day is excluded for all
currencies. At the daily 16:30 Europe/Amsterdam update on October 24, 2026, the
files are named `eurofxref_and_crypto_rates.2026.10.24.csv` and `.xlsx`, and their
newest data row is **2026-10-23**.

Today's ECB fiat rates are also withheld until tomorrow's snapshot, even if
already published. Weekend and holiday rows remain in the calendar: fiat stays
blank when the ECB publishes no rate, while crypto is included when available.
For example, Sunday's snapshot includes Saturday's crypto closing prices and
blank Saturday fiat cells. Missing source observations can still leave gaps and
are retried on later runs.

`--run-date YYYY-MM-DD` overrides the run date and applies the same rule: data
ends the preceding calendar day. Existing cached rows from the run date or later
are excluded. The script logs the last data date when it creates the files.

Both formats contain the same dates and currency columns, with no empty separator
column. Excel uses a `Rates` sheet with numeric rates, real date cells displayed as
`YYYY-MM-DD`, column filters, and a frozen header row and date column. Missing
values stay blank and download failures remain the text `ERROR`. CSV files remain
the cache used by subsequent daily runs. The daily action commits both formats.
Both CSV and Excel display rates as plain decimals, never scientific notation,
including small values such as BTC (`0.0000138166606522`, not `1.38166606522e-05`).
CSV conversion expands exponent notation without rounding existing decimal digits;
Excel cells retain their underlying numeric values.

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
leaves `ERROR`. Both are retried on later runs. The unfinished current day is
excluded from the snapshot entirely.

The USD retry repairs source gaps such as June 28–29, 2022, where CoinMarketCap's
EUR history is missing observations present in its USD history. It runs during
the normal daily update; older snapshot files are not rewritten automatically.

## Why missed GitHub Actions runs are repaired automatically

The script does **not** assume yesterday's Action succeeded.

Every run:

1. checks cached ECB historical and daily data against the publication schedule,
   downloading missing, invalid, or stale files;
2. combines the full historical data with the latest daily row;
3. creates a complete daily calendar through yesterday;
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

### ECB download cache and `--force`

Downloaded ZIPs are stored in `.cache/ecb/` (change this with `--ecb-cache-dir`).
Each file is checked independently against the latest expected release: around
16:00 Europe/Amsterdam on weekdays, excluding TARGET closing days (January 1,
Good Friday, Easter Monday, May 1, December 25 and 26). The calendar follows
daylight saving time. See the [ECB publication schedule](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html)
and [TARGET closing days](https://www.ecb.europa.eu/ecb/contacts/working-hours/html/index.en.html).

If the latest date inside a cached file covers that release, it is reused without
a network request. Repeated runs, weekends, holidays, and runs before publication
therefore reuse current downloads. Existing `eurofxref-hist.zip`, `eurofxref.zip`,
or their unzipped `.csv` equivalents in the working directory are also recognized.
Missing, corrupt, or outdated files are downloaded and validated before replacing
their cache entries. If a source still returns old data after its expected release,
the next run retries it. Same-day corrections are picked up by the next scheduled
refresh or a forced download.

When nothing is downloaded, the script prints a reminder:

```text
No ECB downloads needed. Use --force to force an ECB download (explicit local CSV overrides stay local).
```

Run `python rates.py --force` to refresh both ZIPs regardless of freshness.
`--force` affects ECB downloads only; valid cached crypto rates remain preserved.
The explicit `--ecb-daily-file` and `--ecb-hist-file` options always use the supplied
local CSVs, including with `--force`. Freshness uses the actual clock; `--run-date`
only changes the snapshot's label and data cutoff.

GitHub Actions restores and saves `.cache/ecb` between runs, so fresh runners can
also skip unnecessary downloads. For manual runs, the Actions form includes a
**Force ECB ZIP downloads** option. If GitHub evicts the cache, the next run
downloads it again automatically. Cache files are not part of the exported data.

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
