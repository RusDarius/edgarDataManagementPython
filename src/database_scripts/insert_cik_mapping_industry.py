import json
from datetime import datetime
from pathlib import Path

from data_loaders.api_tradingview_client import ApiTradingViewClient
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection
from generic_utils.log_to_files_util import log_to_file

USER_AGENT = "Barnnabass daniOO7XbX@gmail.com"
TRADINGVIEW_API_CLIENT = ApiTradingViewClient(user_agent=USER_AGENT)

TICKER_CIK_LOOKUP_QUERY = """
SELECT Cik, Ticker, Exchanges
FROM sec_cik_tickers_mapping
WHERE UPPER(Ticker) = UPPER(%s)
"""

TICKER_CIK_LOOKUP_SECONDARY_QUERY = """
SELECT m.Cik, m.Ticker, m.Exchanges
FROM sec_cik_tickers_mapping m
JOIN JSON_TABLE(
    m.SecondaryTickers,
    '$[*]' COLUMNS (secondary_ticker VARCHAR(16) PATH '$')
) jt
    ON UPPER(jt.secondary_ticker) = UPPER(%s)
"""


def _normalize_ticker(ticker: str) -> str:
    ticker_str = str(ticker).strip().upper()
    # Convert formats like BRK.A to BRK-A.
    if "." in ticker_str:
        parts = ticker_str.split(".")
        if len(parts) == 2 and parts[1]:
            ticker_str = f"{parts[0]}-{parts[1]}"
    return ticker_str


def _normalize_exchange_token(value: str | None) -> str | None:
    if value is None:
        return None
    token = "".join(ch for ch in str(value).upper() if ch.isalnum())
    if not token:
        return None

    exchange_alias_map = {
        "NASDAQ": "NASDAQ",
        "XNAS": "NASDAQ",
        "NASDAQGS": "NASDAQ",
        "NASDAQGM": "NASDAQ",
        "NASDAQCM": "NASDAQ",
        "NYSE": "NYSE",
        "XNYS": "NYSE",
        "NYSEARCA": "NYSE",
        "ARCA": "NYSE",
        "NYSEAMERICAN": "NYSE",
        "AMEX": "NYSE",
        "NYSEMKT": "NYSE",
    }
    return exchange_alias_map.get(token, token)


def _parse_exchanges(exchanges_raw) -> set[str]:
    if exchanges_raw is None:
        return set()

    parsed = exchanges_raw
    if isinstance(exchanges_raw, str):
        text = exchanges_raw.strip()
        if not text:
            return set()
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = [text]

    if isinstance(parsed, list):
        result = set()
        for item in parsed:
            normalized = _normalize_exchange_token(str(item))
            if normalized:
                result.add(normalized)
        return result

    normalized_single = _normalize_exchange_token(str(parsed))
    return {normalized_single} if normalized_single else set()


def _extract_tradingview_fields(
    scan_entry: dict,
) -> tuple[str | None, str | None, str | None]:
    raw_values = scan_entry.get("d")
    if not isinstance(raw_values, list) or not raw_values:
        return (None, None, None)

    ticker_view = raw_values[0] if isinstance(raw_values[0], dict) else {}
    ticker_name = ticker_view.get("name")
    exchange = ticker_view.get("exchange")

    industry = None
    if (
        len(raw_values) > 19
        and isinstance(raw_values[19], str)
        and raw_values[19].strip()
    ):
        industry = raw_values[19].strip()
    elif (
        len(raw_values) > 17
        and isinstance(raw_values[17], str)
        and raw_values[17].strip()
    ):
        industry = raw_values[17].strip()

    return (ticker_name, exchange, industry)


def _select_best_candidate(candidates: list[dict], exchange: str | None):
    if not candidates:
        return None, "not_found"

    if len(candidates) == 1:
        return candidates[0], None

    exchange_key = _normalize_exchange_token(exchange)
    if exchange_key:
        matched = []
        for candidate in candidates:
            candidate_exchanges = _parse_exchanges(candidate.get("Exchanges"))
            if exchange_key in candidate_exchanges:
                matched.append(candidate)

        if len(matched) == 1:
            return matched[0], None
        if len(matched) > 1:
            return None, "ambiguous_after_exchange_filter"

    return None, "ambiguous_no_exchange_match"


def _ensure_industry_tradingview_column(conn):
    with conn.cursor() as cursor:
        cursor.execute(
            "SHOW COLUMNS FROM sec_cik_tickers_mapping LIKE 'IndustryTradingView'"
        )
        if cursor.fetchone() is None:
            cursor.execute(
                "ALTER TABLE sec_cik_tickers_mapping ADD COLUMN IndustryTradingView VARCHAR(255) NULL"
            )


def insert_cik_mapping_industry():
    """
    Populate IndustryTradingView in sec_cik_tickers_mapping from TradingView scan rows.

    Matching rules:
    - Try primary ticker match first.
    - If no primary candidates, try SecondaryTickers.
    - If multiple candidates exist, use exchange intersection to disambiguate.
    - Skip ambiguous or unresolved rows for correctness.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        _ensure_industry_tradingview_column(conn)

        # outdated use of scan_listed_america_market, currently has entries with key - value
        response = TRADINGVIEW_API_CLIENT.scan_listed_america_market()
        entries = response.get("data", [])
        log_file = (
            Path(__file__).resolve().parents[2]
            / "logs"
            / "tradingview_industry_unresolved_tickers.log"
        )

        log_to_file(
            log_file,
            f"[{datetime.now().isoformat(timespec='seconds')}] Start run: total_entries={len(entries)}",
        )

        updated_rows = 0
        unresolved = 0
        ambiguous = 0
        skipped_missing_fields = 0

        with conn.cursor(dictionary=True) as cursor:
            for entry in entries:
                ticker_raw, exchange_raw, industry = _extract_tradingview_fields(entry)
                if not ticker_raw or not industry:
                    skipped_missing_fields += 1
                    continue

                ticker = _normalize_ticker(ticker_raw)

                cursor.execute(TICKER_CIK_LOOKUP_QUERY, (ticker,))
                primary_candidates = cursor.fetchall()

                candidates = primary_candidates
                if not candidates:
                    cursor.execute(TICKER_CIK_LOOKUP_SECONDARY_QUERY, (ticker,))
                    candidates = cursor.fetchall()

                chosen_candidate, reason = _select_best_candidate(
                    candidates, exchange_raw
                )
                if chosen_candidate is None:
                    if reason == "not_found":
                        unresolved += 1
                        log_to_file(
                            log_file,
                            (
                                f"UNRESOLVED ticker={ticker} exchange={exchange_raw} "
                                f"industry={industry} reason={reason}"
                            ),
                        )
                    else:
                        ambiguous += 1
                    continue

                cik = chosen_candidate["Cik"]
                cursor.execute(
                    """
                    UPDATE sec_cik_tickers_mapping
                    SET IndustryTradingView = %s
                    WHERE Cik = %s
                    """,
                    (industry, cik),
                )
                updated_rows += cursor.rowcount

        conn.commit()

        print(
            "IndustryTradingView update summary: "
            f"updated={updated_rows}, "
            f"unresolved={unresolved}, "
            f"ambiguous={ambiguous}, "
            f"skipped_missing_fields={skipped_missing_fields}, "
            f"total_entries={len(entries)}"
        )
        log_to_file(
            log_file,
            (
                "Run summary: "
                f"updated={updated_rows}, unresolved={unresolved}, "
                f"ambiguous={ambiguous}, skipped_missing_fields={skipped_missing_fields}, "
                f"total_entries={len(entries)}"
            ),
        )
    finally:
        conn.close()
