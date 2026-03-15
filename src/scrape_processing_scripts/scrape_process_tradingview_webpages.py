import html
import os
import re
from typing import Any


def _default_screener_html_path() -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(
        project_root, "savedData", "USvalueScreenerExclusive_AerospaceDefense.html"
    )


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    # TradingView often uses nbsp/narrow-nbsp for formatted numbers.
    text = text.replace("\xa0", " ").replace("\u202f", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _make_field_key(data_field: str) -> str:
    key = data_field.strip().lower()
    key = key.replace("|", "_").replace("/", "_")
    key = re.sub(r"[^a-z0-9_]+", "_", key)
    key = re.sub(r"_+", "_", key).strip("_")
    return key or "column"


def _extract_header_columns(raw_html: str) -> list[dict[str, str]]:
    header_pattern = re.compile(
        r'<th[^>]*data-field="([^"]+)"[^>]*>(.*?)</th>',
        re.IGNORECASE | re.DOTALL,
    )
    upper_line_pattern = re.compile(
        r'<div[^>]*class="[^"]*upperLine[^"]*"[^>]*>(.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    bottom_line_pattern = re.compile(
        r'<div[^>]*class="[^"]*bottomLine[^"]*"[^>]*>(.*?)</div>',
        re.IGNORECASE | re.DOTALL,
    )
    title_pattern = re.compile(r'title="([^"]+)"', re.IGNORECASE)

    columns: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    for data_field, header_html in header_pattern.findall(raw_html):
        upper_parts = [
            _clean_html_text(part)
            for part in upper_line_pattern.findall(header_html)
            if _clean_html_text(part)
        ]
        bottom_parts = [
            _clean_html_text(part)
            for part in bottom_line_pattern.findall(header_html)
            if _clean_html_text(part)
        ]

        label = " ".join(upper_parts).strip()
        if bottom_parts:
            suffix = " ".join(bottom_parts).strip()
            label = f"{label} {suffix}".strip()

        if not label:
            title_match = title_pattern.search(header_html)
            label = _clean_html_text(title_match.group(1)) if title_match else ""

        if not label:
            label = data_field

        field_key = _make_field_key(data_field)
        if field_key in seen_keys:
            continue

        columns.append({"field": data_field, "key": field_key, "label": label})
        seen_keys.add(field_key)

    return columns


def extract_companies_from_tradingview_screener_html(
    html_path: str | None = None,
) -> list[dict[str, Any]]:
    """
    Extract screener table companies from a saved TradingView HTML page.

    Returns one object per table row with fields:
    - exchange
    - ticker
    - symbol (exchange:ticker)
    - company_name
    - symbol_url
    - fields (machine-friendly row metrics)
    - columns (human-friendly row metrics)
    """
    source_path = html_path or _default_screener_html_path()

    with open(source_path, "r", encoding="utf-8", errors="replace") as f:
        raw_html = f.read()

    row_pattern = re.compile(
        r'<tr[^>]*class="[^"]*listRow[^"]*"[^>]*data-rowkey="([A-Z0-9_\-]+:[A-Z0-9.\-]+)"[^>]*>(.*?)</tr>',
        re.IGNORECASE | re.DOTALL,
    )
    ticker_pattern = re.compile(
        r'<a[^>]*class="[^"]*tickerName[^\"]*"[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    description_pattern = re.compile(
        r'<a[^>]*class="[^"]*tickerDescription[^\"]*"[^>]*>([^<]+)</a>',
        re.IGNORECASE,
    )
    href_pattern = re.compile(
        r'<a[^>]*class="[^"]*tickerName[^\"]*"[^>]*href="([^"]+)"',
        re.IGNORECASE,
    )
    cell_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)

    header_columns = _extract_header_columns(raw_html)

    companies: list[dict[str, Any]] = []
    seen_symbols: set[str] = set()

    for symbol, row_html in row_pattern.findall(raw_html):
        if symbol in seen_symbols:
            continue

        ticker_match = ticker_pattern.search(row_html)
        description_match = description_pattern.search(row_html)
        href_match = href_pattern.search(row_html)
        cell_values = [
            _clean_html_text(cell_html) for cell_html in cell_pattern.findall(row_html)
        ]

        exchange, ticker_from_symbol = symbol.split(":", 1)
        ticker_value = (
            html.unescape(ticker_match.group(1).strip())
            if ticker_match
            else ticker_from_symbol
        )
        company_name = (
            html.unescape(description_match.group(1).strip())
            if description_match
            else ""
        )
        symbol_url = html.unescape(href_match.group(1).strip()) if href_match else ""

        fields: dict[str, str] = {}
        columns: dict[str, str] = {}
        for index, column in enumerate(header_columns):
            if index >= len(cell_values):
                break

            value = cell_values[index]
            if column["key"] == "tickeruniversal" and ticker_value:
                # Keep the symbol cell deterministic even if extra UI marker text is present.
                value = ticker_value

            fields[column["key"]] = value
            columns[column["label"]] = value

        extra_values = [value for value in cell_values[len(header_columns) :] if value]

        companies.append(
            {
                "exchange": exchange,
                "ticker": ticker_value,
                "symbol": symbol,
                "company_name": company_name,
                "symbol_url": symbol_url,
                "fields": fields,
                "columns": columns,
                "extra_values": extra_values,
            }
        )
        seen_symbols.add(symbol)

    return companies
