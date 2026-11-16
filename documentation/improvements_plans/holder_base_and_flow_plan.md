# Holder base and flow: who holds, who trades, and how they react

Status: research and design (4 Nov 2026). Nothing in here is built yet.

## The question

Some names get punished for a good print and others get bought on a weak one. The store has a clean case from this
season:

| Print (react file) | EPS surprise | Revenue surprise | Reaction |
|---|---|---|---|
| NYSE:RDDT (31 Jul) | +31.7% | +10.1% | -21.0% |
| NYSE:RDDT (30 Oct) | +5.4% | +2.3% | -30.2% |
| NASDAQ:DUOL (4 Nov) | -5.1% | +1.9% | +32.5% |

(`panel_store.duckdb` `prints`: `eps_surprise`, `rev_surprise`, `react`.)

The surprise does not explain the reaction. Three things plausibly do:
- who holds the stock (fast money or slow money);
- how much of the float is in play;
- what positioning the print met: short interest, run-up, crowding.

The beat bar already covers the third with run-up, tape and revisions. This plan adds the first two as measured fields
and tests whether they lead the reaction. A tested field becomes a pointer; it is not taken as fact.

## What the literature says

- **Bushee (1998, 2001)** sorts 13F institutions by portfolio turnover and concentration:
  - *Transient*: high turnover, diversified.
  - *Quasi-indexer*: low turnover, diversified.
  - *Dedicated*: low turnover, concentrated.

  High transient ownership goes with prices that over-weight near-term earnings. That is the "punish a great quarter
  on one soft KPI" pattern.
- **The counterpoint (Ke and Ramalingegowda):** transient selling after small misses is informed trading, not a
  temporary mispricing. Treat it as a measurable question, not a rule.
- **Transients trade ahead:** transient holders sell before negative guidance, while quasi-indexers and dedicated
  holders add on positive surprises.

The classification can be computed from free SEC 13F data, so it fits this repo: deterministic, generic, measured on
our own store.

## Data sources (free and official first)

| Source | What it gives | Lag / cadence | Access |
|---|---|---|---|
| **13F-HR data sets** (SEC DERA) | Every institution over $100M: holdings by CUSIP, shares, value, voting authority | Quarterly snapshot, filed up to 45 days after quarter end; data sets posted after Feb / May / Aug / Nov | Quarterly zip (INFOTABLE, SUBMISSION, COVERPAGE TSV). Live filings come from EDGAR daily indexes |
| **N-PORT data sets** (SEC) | Fund-level holdings (mutual funds, ETFs): index vs active, fund flows | Monthly data, made public quarterly (about 60 days) | Quarterly zip |
| **Insider transactions** (Forms 3/4/5 data sets) | Officer, director and 10% owner buys and sells, price, 10b5-1 flag (`AFF10B5ONE`) | Form 4 within 2 business days; data sets quarterly | Quarterly zip; daily from EDGAR indexes |
| **Schedule 13D / 13G** | 5%+ holders: activist (13D) vs passive (13G), with stake changes | Event-driven; structured XML since 18 Dec 2024 | EDGAR filings (XML) |
| **FINRA short interest** | Shares short per listed security, days to cover | Twice a month | FINRA Query API (consolidated dataset, all exchanges since mid-2022) |
| **FINRA Reg SHO daily short volume** | Daily short-sale share of reported off-exchange volume | Daily, same evening | Daily text files. This is flow, not short interest |
| **OpenFIGI** | CUSIP → ticker / exchange (13F is keyed by CUSIP) | On demand | Free API (key raises the rate limit) |
| TradingView (already scanned) | `float_shares_outstanding`, `float_shares_percent_current`, `number_of_shareholders`, volume / relvol / dollar volume | Daily | Existing all-fields scan |

**SEC access** already exists in `src/data_loaders/api_client.py`: a `User-Agent`, `data.sec.gov`, CIK mapping in
`sec_cik_ticker_mapping.py`. The SEC fair-access limit is 10 requests a second.

**Paid shortcuts,** if speed matters more than cost: FMP, sec-api.io (parsed 13F / N-PORT / Form 4), Fintel,
WhaleWisdom. Scraping Yahoo's holders page through yfinance gives only the top 10, is unofficial and breaks often. Not
a base.

**FINRA terms:** the daily files are published under FINRA's terms of use. Personal research is fine; check the terms
before any redistribution.

**What none of these give:** live retail flow and options positioning. Retail is only a residual (1 − institutional −
insider), and a rough one. Daily flow has to come from the tape itself: volume spikes, up/down volume, short-volume
ratio and relvol at the print.

## Design

### Layer A: flow profile from the store we have (no new data)

`tv_scan_cli.py flow-profile --pack PACK` (generic_utils, recipe `config/generic_utils/flow_profile.json`)

**Daily volume states** per name, from `panel` / `features`:

| State | Rule |
|---|---|
| spike up | relvol z ≥ k on an up step |
| spike down | relvol z ≥ k on a down step |
| absorption | a spike with the move under 0.5 ATR |
| air pocket | relvol under 1 with a move over 2 ATR |
| distribution | 20-day up/down volume share low |
| accumulation | 20-day up/down volume share high |

Turnover = dollar volume / (market cap × float %).

**Print reaction profile** per name, from `prints`:
- the share of beats sold and of misses bought;
- the slope of the reaction on the surprise;
- relvol at the print, and the drift after vs the reaction.

Per-name counts are small (about 3 prints per name since March), so the study pools them by cells: turnover tercile,
float % tercile, market cap, run-up.

**Evidence** follows signal-lab / beat_bar discipline:
- the next 1 / 5 / 21 steps per state;
- the reaction and 28-day hold per cell;
- lift, t on `n_indep`, halves and hit rate;
- an arrow only where they all agree.

**Output:** pointers (`flow_state`, `turnover_q`, `beat_sold_cell`) joined into beat_bar, print_night, the board and
event_risk the same way the warning field is joined now.

This answers "do the volume and turnover metrics already carry the signal?" before any collector is built.

### Layer B: holder store (new collectors)

**Store:** `logs/tradingview_analysis/holder_store/holder_store.duckdb`. It is append-only and keeps the filing date on
every row.

| Table | Contents |
|---|---|
| `cusip_map` | cusip, symbol (EXCHANGE:TICKER via OpenFIGI then `symbol_identity.qualify`; ambiguous stays unmapped and reported), name, as_of |
| `managers` | cik, name, period, n_positions, aum_usd |
| `holdings_13f` | period, filed, cik, cusip, symbol, shares, value_usd, put_call, discretion |
| `manager_class` | period, cik, turnover (Bushee: sum of the absolute changes in weights over 4 quarters), hhi concentration, class TRA / QIX / DED, plus `hedge_fund` from a curated list in config |
| `stock_holders` | period, filed, symbol, inst_pct_float, n_holders, d_holders_qoq, tra_pct, qix_pct, ded_pct, hf_pct, top10_pct, hhi, new / closed positions, holder churn |
| `insider_tx` | filed, symbol, owner, role, code (P / S / M / A), shares, px, value_usd, is_10b5_1 |
| `beneficial_5pct` | filed, symbol, filer, form (13D / 13G / amendments), pct, is_activist |
| `short_interest` | settle_date, symbol, short_shares, si_pct_float, days_to_cover, d_si |
| `short_volume` | date, symbol, short_vol, total_vol, short_ratio (Reg SHO daily) |
| (later) `fund_holdings_nport` | period, filed, series, symbol, shares, index_fund flag |

**CLI:** `tv_scan_cli.py holder-store --update`. Each run:
- checks the DERA pages for a new quarterly zip;
- pulls the EDGAR daily index for Forms 4 / 13D / 13G / 13F-HR since the last run;
- pulls FINRA short interest on its settlement dates and the Reg SHO file for each new trading day;
- maps any new CUSIPs.

**Refresh:** a producer in `daily_refresh.json`. It is cheap on most days: a short-volume file plus a few filings.

**Point-in-time:** every join into the panel store uses `filed` (or FINRA's publication date), never the period end.
A 13F for Q3 is known from its filing date, up to 45 days later. Without this the study has look-ahead bias.

### Layer C: holder lab (the study, then the pointers)

`tv_scan_cli.py holder-lab` runs on the store's prints and steps with the holder mix as of each file.

**Questions:**
- Do high-`tra_pct` names sell off on beats more often than low-`tra_pct` names (RDDT vs DUOL)?
- Does rising `d_holders_qoq` / `ded_pct` cushion misses?
- Does high `si_pct_float` with a beat give a squeeze, and a miss a gap?
- Do insider cluster buys (2+ insiders, open-market `P`, not 10b5-1) lead 28-day excess?
- Does `short_ratio` spiking into a print lead the reaction?

**Method:** each field cut into quintiles (or the recipe's terciles), crossed with the surprise sign. It reports:
- lift against all prints, with t on `n_indep` and both halves;
- the overnight tail in starter dollars.

The same arrow rule as print_night applies.

**Pointers that pass go into:**
- beat_bar / print_night: "who meets this print";
- event_risk: a *holder crowd*, meaning names that share top holders, beside the correlation crowd. A forced seller
  hits every name it holds at once.
- the board: `tra_pct`, `si_pct_float`, `insider_net_90d`.

No 0-100 and no holder "score". Each field keeps its own column and order, and none of them is a gate.

## Limits

- **13F is slow.** It is a quarterly snapshot up to 45 days late, longs only, institutions over $100M only. It says who
  is likely there, not who is trading today. The daily read has to come from Layer A and FINRA short volume.
- **Short history.** The store's prints start March 2026, about 11k prints over 3.7k names. Holder fields can be
  backfilled to 2013 from the DERA archive, but prices and prints before March are not in the store. The first study
  will have few independent quarters: read `n_indep`.
- **Hedge fund vs long-only** needs a curated manager list in config, which is judgment. The TRA / QIX / DED class is
  measured.

## Phases

| Phase | Work | Size |
|---|---|---|
| 0 | Layer A `flow-profile` on the existing store; evidence for volume states and the print asymmetry by turnover / float cells | 1 module + recipe + tests |
| 1 | `holder-store` collectors: 13F data sets + EDGAR daily 13F-HR, OpenFIGI CUSIP map, FINRA short interest + Reg SHO daily, Form 4 | 1 module per source + a shared fetch layer on `api_client` |
| 2 | Manager classification (TRA / QIX / DED) and `stock_holders`, joined into the panel store as-of `filed` | 1 module |
| 3 | `holder-lab` study and pointers into beat_bar / print_night / event_risk / board; a Holders tab | 1 module + joins |
| 4 | N-PORT (index vs active funds) and 13D / 13G event feed | later |

## Sources

- [SEC Form 13F data sets](https://www.sec.gov/dera/data/form-13f), [13F readme](https://sec.gov/files/form_13f_readme.pdf)
- [SEC Form N-PORT data sets](https://www.sec.gov/data-research/sec-markets-data/form-n-port-data-sets), [readme](https://sec.gov/files/nport_readme.pdf)
- [SEC insider transactions data sets](https://www.sec.gov/dera/data/form-345), [readme](https://SEC.gov/files/insider_transactions_readme.pdf)
- [13D / 13G XML requirement (Skadden)](https://www.skadden.com/insights/publications/2024/02/reminders-amended-beneficial-ownership-rules-effective)
- [FINRA equity short interest](https://www.finra.org/finra-data/browse-catalog/equity-short-interest/data), [short interest API guide](https://WWW.FINRA.ORG/sites/default/files/Equity_Short_Interest_Data_File_Download_API.pdf), [consolidated dataset note](https://developer.finra.org/node/2351)
- [FINRA Reg SHO daily short sale volume files](https://www.finra.org/finra-data/browse-catalog/reg-sho-daily)
- [Bushee, Do institutional investors prefer near-term earnings over long-run value?](https://repository.upenn.edu/handle/20.500.14332/1076)
- [How institutional investor objectives affect firm valuation and governance](https://clsbluesky.law.columbia.edu/2017/01/09/how-institutional-investor-objectives-affect-firm-valuation-and-governance)
- [Ke and Ramalingegowda on transient investors and small negative surprises](https://www.cb.cityu.edu.hk/research/cjar/doc/paper/200907/Mtg%205%20-%20Bin%20Ke.pdf)
