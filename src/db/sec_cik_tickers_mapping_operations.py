import json
from typing import Iterable


# Query the database for a specific CIK and return its ticker.
def read_sec_cik_ticker_for_cik(conn, cik):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT Ticker FROM sec_cik_tickers_mapping WHERE Cik = %s", (cik,))
    row = cursor.fetchone()
    return row["Ticker"] if row else None


# Load a mapping of CIK to ticker from the database.
def read_all_sec_cik_ticker_map(conn):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT Cik, Ticker FROM sec_cik_tickers_mapping")
    return {row["Cik"]: row["Ticker"] for row in cursor.fetchall()}


def _normalize_cik(cik) -> str:
    return str(cik).replace("CIK", "").lstrip("0") or "0"


def _normalize_sic(sic_code):
    if sic_code is None:
        return None
    sic_text = str(sic_code).strip()
    return sic_text if sic_text else None


def _normalize_exchanges(exchanges):
    if isinstance(exchanges, list) and exchanges:
        return json.dumps(exchanges, ensure_ascii=True)
    return None


def _chunked(values, batch_size: int):
    for idx in range(0, len(values), batch_size):
        yield values[idx : idx + batch_size]


def batch_update_sec_cik_mapping_sic_and_exchanges(
    conn,
    updates: Iterable[tuple],
    batch_size: int = 500,
):
    """
    Batch update SicCode/Exchanges for many CIKs using chunked executemany.

    `updates` items can be tuples: (cik, sic_code, exchanges).
    Returns total number of affected rows reported by MySQL cursor.rowcount.
    """
    both_values = []
    sic_only_values = []
    exchanges_only_values = []

    for cik, sic_code, exchanges in updates:
        cik_normalized = _normalize_cik(cik)
        normalized_sic = _normalize_sic(sic_code)
        normalized_exchanges = _normalize_exchanges(exchanges)

        if normalized_sic is not None and normalized_exchanges is not None:
            both_values.append((normalized_sic, normalized_exchanges, cik_normalized))
        elif normalized_sic is not None:
            sic_only_values.append((normalized_sic, cik_normalized))
        elif normalized_exchanges is not None:
            exchanges_only_values.append((normalized_exchanges, cik_normalized))

    total_updated_rows = 0

    with conn.cursor() as cursor:
        if both_values:
            sql = """
                UPDATE sec_cik_tickers_mapping
                SET SicCode = %s, Exchanges = %s
                WHERE Cik = %s
            """
            for chunk in _chunked(both_values, batch_size):
                cursor.executemany(sql, chunk)
                total_updated_rows += cursor.rowcount

        if sic_only_values:
            sql = """
                UPDATE sec_cik_tickers_mapping
                SET SicCode = %s
                WHERE Cik = %s
            """
            for chunk in _chunked(sic_only_values, batch_size):
                cursor.executemany(sql, chunk)
                total_updated_rows += cursor.rowcount

        if exchanges_only_values:
            sql = """
                UPDATE sec_cik_tickers_mapping
                SET Exchanges = %s
                WHERE Cik = %s
            """
            for chunk in _chunked(exchanges_only_values, batch_size):
                cursor.executemany(sql, chunk)
                total_updated_rows += cursor.rowcount

    return total_updated_rows
