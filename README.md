# edgarDataManagementPython

Personal research platform for **equity analysis and active decision support**: ingest fundamentals, score market setups, stress-test ideas historically, and project forward outcomes — then turn that into shortlists and portfolio context.

Built as a working system for real screening and research workflows, not a demo app. Useful background for recruiters (what problem it solves) and for technical interviews (how the stack fits together).

## What it does

| Layer | In plain terms | Under the hood |
| --- | --- | --- |
| **Fundamentals** | Pull and store company financial facts from SEC filings | EDGAR bulk + Arelle fill-ins → MySQL (`sec_cik_tickers_mapping`, `edgar_financial_data_concepts`, …) |
| **Market scanning** | Rank names for directional / setup interest across horizons | TradingView screener API → configurable **move-prediction profiles** → DuckDB run stores + logs |
| **Edge research** | Find high-volatility / high-liquidity states that historically preceded moves | All-fields daily snapshots, sleeve percentiles, persistence, safety / blindspot lanes, backtests |
| **Financial projection** | 5-year EV/Revenue scenarios with street / history / peer cross-checks | Scenario config + multi-lane model on all-fields DuckDB (prediction run = optional symbol shortlist) |
| **Portfolio context** | Track holdings and score them against the latest research | Dated positions, snapshot sync, holdings scoring vs move-prediction output |

Day-to-day orchestration lives in [`src/main.py`](src/main.py): uncomment or wire the flows you need (prediction suite, earnings-priority overlay, all-fields export, edge / backtest runners, portfolio bootstrap, etc.). Deeper how-tos sit under [`documentation/`](documentation/).

## Mental model

```text
SEC EDGAR / Arelle          TradingView screener API
        │                              │
        ▼                              ▼
   MySQL fundamentals          Daily / weekly scan payloads
                                       │
                    ┌──────────────────┼──────────────────┐
                    ▼                  ▼                  ▼
            Move-prediction     All-fields DuckDB    Edge research
            profile scoring       snapshots           + backtests
                    │                  │                  │
                    └────────┬─────────┴────────┬─────────┘
                             ▼                  ▼
                    Shortlists / industry      Financial projection
                    packs / holdings score     (bear / base / bull lanes)
                             │
                             ▼
                    Portfolio tracker & research logs
```

**Move prediction** is heuristic multi-profile ranking (momentum, value recovery, breakout, GARP-style lenses, etc.), with optional conviction / regime overlays — not a black-box price forecast.

**Edge research** ranks *setup state* (vol / liquidity / momentum context) and historical forward behavior under that state; it does not claim permanent intrinsic quality.

**Financial projection** keeps core, street, history, and peer lanes separate so disagreements stay visible.

## Repo map (high signal)

| Path | Role |
| --- | --- |
| `src/main.py` | Primary workflow switchboard |
| `src/data_loaders/` | TradingView / BVB / EDGAR clients and extractors |
| `src/db/`, `src/database_scripts/` | Persistence and bulk loaders |
| `src/data_analysis_scripts/` | Screening, prediction suites, pattern / upside / execution analysis |
| `src/edge_research_tools/` | Vol–liquidity edge engine and related runners |
| `src/financial_projection/` | Forward EV/Revenue model + suite entrypoints |
| `src/portfolio_performance_tracking/` | Flow-aware fund performance, NAV history, and benchmark reports |
| `config/` | Profile suites, conviction / regime, holdings, projection scenarios |
| `documentation/` | Usage guides and research playbooks |
| `logs/` | Run outputs (DuckDB, CSV, markdown reports) |

Standalone runners (examples): `src/run_edge_research_tools.py`, `src/run_financial_projection.py`, `src/run_market_timing_policy.py`, upside / historical edge backtest scripts.

## Stack

- **Python** research codebase (`requirements.txt`: requests, DuckDB, MySQL connector, Arelle, Plotly, pytest, …)
- **MySQL** for EDGAR / company-map style tables
- **DuckDB + Parquet** for large TradingView analysis runs and historical pools
- Config-driven **JSON profile suites** for repeatable scoring lenses

## Direction (what this keeps aiming at)

1. Sharper **active-manager** lenses — profiles, conviction, regime context, and calibration against realized outcomes  
2. Stronger **edge research** — persistence, safety filters, execution-style backtests, and field-level predictor tracking  
3. Better **fundamental ↔ market** linkage — projection lanes, peer views, and shortlists grounded in both filings and live screens  
4. Practical **portfolio loop** — holdings scoring, watchlists, and decision logs that stay auditable  

The long-term goal is a coherent personal research OS: data in, ranked setups and projections out, with enough history and documentation to challenge and improve the process over time.

## Getting started

```bash
pip install -r requirements.txt
cd src
python main.py
```

Enable the blocks you need inside `main()` (prediction suite is the usual live path). For focused modules, prefer the dedicated runners and guides — e.g. [`documentation/financial_projection_usage.md`](documentation/financial_projection_usage.md), [`documentation/edge_research_tools_active_management_playbook.md`](documentation/edge_research_tools_active_management_playbook.md), [`documentation/data_analysis_scripts_guide.md`](documentation/data_analysis_scripts_guide.md), [`documentation/main_db_population_flow.md`](documentation/main_db_population_flow.md).

---

*Name is historical: the project started as EDGAR data management and grew into full-market screening, scoring, and modeling on top of that foundation.*
