import csv
import os
import sys
from datetime import datetime
from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection


# Load a mapping of CIK to ticker from the database.
def load_cik_ticker_map(conn):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT Cik, Ticker FROM sec_cik_tickers_mapping")
    return {row["Cik"]: row["Ticker"] for row in cursor.fetchall()}


# Load tag metadata from tag.txt in the given bulk data directory.
def load_tag_info(bulk_data_dir):
    tag_info = {}
    with open(os.path.join(bulk_data_dir, "tag.txt"), encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        for row in reader:
            if len(row) < 9:
                continue
            tag, _, _, _, datatype, _, _, _, doc = row[:9]
            tag_info[tag] = {"datatype": datatype, "doc": doc}
    return tag_info


# Parse sub.txt and return a list of filing dictionaries (one per unique CIK).
def parse_sub_txt(sub_path):
    filings = []
    seen_ciks = set()
    with open(sub_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            cik = row.get("cik", "")
            if not cik or not cik.isdigit():
                continue
            cik_int = int(cik)
            if cik_int in seen_ciks:
                continue  # skip duplicates
            seen_ciks.add(cik_int)
            filings.append(
                {
                    "cik": cik_int,
                    "name": row.get("name", ""),
                    "adsh": row.get("adsh", ""),
                    "sic": int(row["sic"]) if row.get("sic", "").isdigit() else None,
                    "countryba": row.get("countryba", ""),
                    "stprba": row.get("stprba", ""),
                    "cityba": row.get("cityba", ""),
                    "zipba": row.get("zipba", ""),
                    "bas1": row.get("bas1", ""),
                    "bas2": row.get("bas2", ""),
                    "baph": row.get("baph", ""),
                    "countryma": row.get("countryma", ""),
                    "stprma": row.get("stprma", ""),
                    "cityma": row.get("cityma", ""),
                    "zipma": row.get("zipma", ""),
                    "mas1": row.get("mas1", ""),
                    "mas2": row.get("mas2", ""),
                    "countryinc": row.get("countryinc", ""),
                    "stprinc": row.get("stprinc", ""),
                    "ein": row.get("ein", ""),
                    "former": row.get("former", ""),
                    "form": row.get("form", ""),
                    "period": row.get("period", ""),
                    "fy": row.get("fy", ""),
                    "fp": row.get("fp", ""),
                    "filed": row.get("filed", ""),
                    "changed": row.get("changed", ""),
                    "afs": row.get("afs", ""),
                    "wksi": row.get("wksi", ""),
                }
            )
    return filings


# Parse num.txt and return a list of all fact dictionaries.
def parse_num_txt(bulk_data_dir):
    facts = []
    with open(os.path.join(bulk_data_dir, "num.txt"), encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            facts.append(row)
    return facts


# Convert a string to a date object or return None if invalid/empty.
def safe_date(val):
    if not val or str(val).strip() == "":
        return None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(val, fmt).date()
        except Exception:
            continue
    return None


# Insert a batch of financial data rows into the edgar_financial_data_concepts table.
def insert_financial_data(conn, data):
    cursor = conn.cursor()
    insert_sql = """
        INSERT INTO edgar_financial_data_concepts
        (Cik, Ticker, FilingType, FiscalPeriod, FiscalYear, 
        Concept, Value, ValueString, Unit, DataType, Adsh, PeriodEnd, Ddate, Segment, Qtrs, BatchTag)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    try:
        cursor.executemany(insert_sql, data)
        conn.commit()
    except Exception as e:
        print("Error during batch insert:", e)
        for row in data:
            try:
                cursor.execute(insert_sql, row)
            except Exception as row_e:
                print("Error with row:", row)
                print("Exception:", row_e)
        conn.rollback()
        sys.exit(1)
    finally:
        cursor.close()


# Main workflow: loads, processes, and inserts financial data for a given batch.
def run_financial_data_loader(bulk_data_dir, batch_data_tag):
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    cik_ticker_map = load_cik_ticker_map(conn)
    tag_info = load_tag_info(bulk_data_dir)
    filings = parse_sub_txt(os.path.join(bulk_data_dir, "sub.txt"))
    facts = parse_num_txt(bulk_data_dir)

    facts_by_adsh = {}
    for fact in facts:
        facts_by_adsh.setdefault(fact["adsh"], []).append(fact)

    data_to_insert = []
    for filing in filings:
        adsh = filing["adsh"]
        cik = filing["cik"]
        ticker = cik_ticker_map.get(cik, None)
        if not ticker:
            continue
        filing_type = filing["form"]
        fiscal_period = filing["fp"]
        fiscal_year_raw = filing.get("fy", "")
        fiscal_year = int(fiscal_year_raw) if str(fiscal_year_raw).isdigit() else 0
        period_end_raw = filing["period"]
        period_end = safe_date(period_end_raw)
        for fact in facts_by_adsh.get(adsh, []):
            tag = fact["tag"]
            value = fact.get("value")
            uom = fact.get("uom")
            ddate_raw = fact.get("ddate")
            ddate = safe_date(ddate_raw)
            segment = fact.get("segments", "")
            qtrs_raw = fact.get("qtrs", "")
            qtrs = int(qtrs_raw) if str(qtrs_raw).isdigit() else None
            batchTag = batch_data_tag
            try:
                value_num = float(value)
                value_str = None
            except (ValueError, TypeError):
                value_num = None
                value_str = value if value is not None else None
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
                    qtrs,
                    batchTag,
                )
            )

    batch_size = 1000
    for i in range(0, len(data_to_insert), batch_size):
        insert_financial_data(conn, data_to_insert[i : i + batch_size])

    conn.close()
    print(
        f"Financial data loaded into edgar_financial_data_concepts for {batch_data_tag}."
    )


def run_load_tag_data_into_db(bulk_data_dir):
    """Load tag metadata from tag.txt into the edgar_tag_info table."""
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    insert_tag_info_to_db(conn, os.path.join(bulk_data_dir, "tag.txt"))


def insert_tag_info_to_db(conn, tag_txt_path):
    """Insert tag metadata into the edgar_tag_info table, updating existing entries."""
    cursor = conn.cursor()
    insert_sql = """
        INSERT INTO edgar_tag_info
        (Tag, Version, Custom, Abstract, Datatype, Iord, Crdr, Tlabel, Doc)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            Version=VALUES(Version),
            Custom=VALUES(Custom),
            Abstract=VALUES(Abstract),
            Datatype=VALUES(Datatype),
            Iord=VALUES(Iord),
            Crdr=VALUES(Crdr),
            Tlabel=VALUES(Tlabel),
            Doc=VALUES(Doc)
    """
    with open(tag_txt_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        data = []
        for row in reader:
            data.append(
                (
                    row["tag"],
                    row.get("version", ""),
                    int(row.get("custom", 0)),
                    int(row.get("abstract", 0)),
                    row.get("datatype", ""),
                    row.get("iord", ""),
                    row.get("crdr", ""),
                    row.get("tlabel", ""),
                    row.get("doc", ""),
                )
            )
        cursor.executemany(insert_sql, data)
        conn.commit()
        cursor.close()


def run_load_edgar_submissions(sub_txt_path):
    """Load company info from sub.txt into the sec_company_info table, skipping existing CIKs."""
    filings_raw = parse_sub_txt(sub_txt_path)

    filings = []
    for row in filings_raw:
        filings.append({k.capitalize(): v for k, v in row.items()})

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    cursor = conn.cursor(dictionary=True)

    # Fetch existing CIKs
    cursor.execute("SELECT Cik FROM sec_company_info")
    all_rows = cursor.fetchall()
    existing_ciks = set(row["Cik"] for row in all_rows if "Cik" in row)  # type: ignore

    # Filter out rows where CIK already exists
    data_to_insert = [
        row for row in filings if row.get("Cik") and row["Cik"] not in existing_ciks
    ]

    if not data_to_insert:
        print("No new companies to insert.")
        cursor.close()
        conn.close()
        return

    insert_sql = """
        INSERT INTO sec_company_info (
            Cik, Name, Sic, CountryBa, StprBa, CityBa, ZipBa, Bas1, Bas2, Baph,
            CountryMa, StprMa, CityMa, ZipMa, Mas1, Mas2, CountryInc, StprInc, Ein,
            Former, Changed, Afs, Wksi
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
    """

    cursor.executemany(
        insert_sql,
        [
            (
                row.get("Cik"),
                row.get("Name"),
                row.get("Sic"),
                row.get("Countryba"),
                row.get("Stprba"),
                row.get("Cityba"),
                row.get("Zipba"),
                row.get("Bas1"),
                row.get("Bas2"),
                row.get("Baph"),
                row.get("Countryma"),
                row.get("Stprma"),
                row.get("Cityma"),
                row.get("Zipma"),
                row.get("Mas1"),
                row.get("Mas2"),
                row.get("Countryinc"),
                row.get("Stprinc"),
                row.get("Ein"),
                row.get("Former"),
                row.get("Changed"),
                row.get("Afs"),
                row.get("Wksi"),
            )
            for row in data_to_insert
        ],
    )

    conn.commit()
    cursor.close()
    conn.close()
    print(f"Loaded {len(data_to_insert)} new companies into sec_company_info.")
