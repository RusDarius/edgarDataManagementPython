import csv
import os
from db.connection_provider import get_mysql_connection

BULK_DATA_DIR = (
    r"d:\Projects\edgarDataManagementPython\documentation\edgarBulkDataSample"
)


def load_cik_ticker_map(conn):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT Cik, Ticker FROM sec_cik_tickers_mapping")
    return {row["Cik"]: row["Ticker"] for row in cursor.fetchall()}


def load_tag_info():
    tag_info = {}
    with open(os.path.join(BULK_DATA_DIR, "tag.txt"), encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 9:
                continue
            tag, _, _, _, datatype, _, _, _, doc = row[:9]
            tag_info[tag] = {"datatype": datatype, "doc": doc}
    return tag_info


def parse_sub_txt():
    filings = []
    with open(os.path.join(BULK_DATA_DIR, "sub.txt"), encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row["form"] in ("10-K", "10-Q"):
                filings.append(
                    {
                        "adsh": row["adsh"],
                        "cik": int(row["cik"]),
                        "form": row["form"],
                        "period": row["period"],
                        "fy": row["fy"],
                        "fp": row["fp"],
                        "filed": row["filed"],
                    }
                )
    return filings


def parse_num_txt():
    facts = []
    with open(os.path.join(BULK_DATA_DIR, "num.txt"), encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            facts.append(row)
    return facts


def insert_financial_data(conn, data):
    cursor = conn.cursor()
    insert_sql = """
        INSERT INTO edgar_financial_data_concepts
        (Cik, Ticker, FilingType, FiscalPeriod, FiscalYear, 
        Concept, Value, ValueString, Unit, DataType, Adsh, PeriodEnd, Ddate, Segment)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    cursor.executemany(insert_sql, data)
    conn.commit()
    cursor.close()


def run_financial_data_loader():
    conn = get_mysql_connection(
        host="localhost",
        port=3306,
        user="root",
        password="!@#4QWEr",
        database="finance_manager_iteration_1",
    )
    cik_ticker_map = load_cik_ticker_map(conn)
    tag_info = load_tag_info()
    filings = parse_sub_txt()
    facts = parse_num_txt()

    # Index facts by adsh for fast lookup
    facts_by_adsh = {}
    for fact in facts:
        facts_by_adsh.setdefault(fact["adsh"], []).append(fact)

    data_to_insert = []
    for filing in filings:
        adsh = filing["adsh"]
        cik = filing["cik"]
        ticker = cik_ticker_map.get(cik, None)
        if not ticker:
            continue  # skip if ticker not mapped
        filing_type = filing["form"]
        fiscal_period = filing["fp"]
        fiscal_year = filing["fy"]
        period_end = filing["period"]
        for fact in facts_by_adsh.get(adsh, []):
            tag = fact["tag"]
            value = fact.get("value")
            uom = fact.get("uom")
            ddate = fact.get("ddate")  # <-- add this
            segment = fact.get("segment", "")  # <-- if available
            # Try to convert value to float, else store as string
            try:
                value_num = float(value)
                value_str = None
            except (ValueError, TypeError):
                value_num = None
                value_str = value
            tag_meta = tag_info.get(tag, {})
            datatype = tag_meta.get("datatype", "")
            data_to_insert.append(
                (
                    cik,
                    ticker,
                    filing_type,
                    fiscal_period,
                    fiscal_year,
                    tag,
                    value_num,
                    value_str,
                    uom,
                    datatype,
                    adsh,
                    period_end,
                    ddate,
                    segment,
                )
            )

    # Insert in batches for efficiency
    batch_size = 1000
    for i in range(0, len(data_to_insert), batch_size):
        insert_financial_data(conn, data_to_insert[i : i + batch_size])

    conn.close()
    print("Financial data loaded into edgar_financial_data_concepts.")
