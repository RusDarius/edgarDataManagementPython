from db.connection_provider import get_mysql_connection
from db.connection_credentials import BASE_DB_CONFIG


def insert_bvb_ticker_entries(entries):
    """
    Insert multiple entries into the bvb_tickers table.
    Args:
        entries: List of dicts, each with keys matching the bvb_tickers table columns.
    Returns:
        Number of rows inserted.
    """
    if not entries:
        return 0
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = """
                INSERT INTO bvb_tickers (
                    simbol, isin, denumire_emisiune, tip_instrument, stare, emitent, tara, judet, localitate, cod_fiscal
                ) VALUES (%(simbol)s, %(isin)s, %(denumire_emisiune)s, %(tip_instrument)s, %(stare)s, %(emitent)s, %(tara)s, %(judet)s, %(localitate)s, %(cod_fiscal)s)
                ON DUPLICATE KEY UPDATE
                    simbol=VALUES(simbol),
                    denumire_emisiune=VALUES(denumire_emisiune),
                    tip_instrument=VALUES(tip_instrument),
                    stare=VALUES(stare),
                    emitent=VALUES(emitent),
                    tara=VALUES(tara),
                    judet=VALUES(judet),
                    localitate=VALUES(localitate),
                    cod_fiscal=VALUES(cod_fiscal)
            """
            cursor.executemany(sql, entries)
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def get_bvb_tickers_filtered(
    stare_value="T", tara_value="Romania", tip_instrument="Actiuni"
):
    """
    Fetch entries from bvb_tickers filtered by 'stare', 'tara', and 'tip_instrument'.
    Args:
        stare_value: Value for 'stare' column (default 'T').
        tara_value: Value for 'tara' column (default 'Romania').
        tip_instrument: Value for 'tip_instrument' column (default 'Actiuni').
    Returns:
        List of dicts for each row.
    """
    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor(dictionary=True) as cursor:
            sql = """
                SELECT * FROM bvb_tickers
                WHERE stare = %s AND tara = %s AND tip_instrument = %s
            """
            cursor.execute(sql, (stare_value, tara_value, tip_instrument))
            results = cursor.fetchall()
    finally:
        conn.close()
    return results
