import csv
import os
from typing import List, Optional, Tuple

from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection


POSITIVES_TABLE = "nyu_ev_ebitda_multiples_positives"
NEGATIVES_TABLE = "nyu_ev_ebitda_multiples_negatives"


def _default_csv_path() -> str:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(project_root, "savedData", "evmultiples.csv")


def _to_int_or_none(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized.upper() == "NA":
        return None
    return int(normalized)


def _to_float_or_none(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or normalized.upper() == "NA":
        return None
    return float(normalized)


def _build_insert_rows(
    csv_path: str,
) -> Tuple[
    List[
        Tuple[
            str,
            Optional[int],
            Optional[float],
            Optional[float],
            Optional[float],
            Optional[float],
        ]
    ],
    List[
        Tuple[
            str,
            Optional[int],
            Optional[float],
            Optional[float],
            Optional[float],
            Optional[float],
        ]
    ],
]:
    positive_rows = []
    negative_rows = []

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        next(reader, None)  # Skip header row

        for row in reader:
            # Expecting 10 columns with duplicate metric names in the header.
            if len(row) < 10:
                continue

            industry_name = row[0].strip()
            if not industry_name:
                continue

            number_of_firms = _to_int_or_none(row[1])

            positive_rows.append(
                (
                    industry_name,
                    number_of_firms,
                    _to_float_or_none(row[2]),
                    _to_float_or_none(row[3]),
                    _to_float_or_none(row[4]),
                    _to_float_or_none(row[5]),
                )
            )

            negative_rows.append(
                (
                    industry_name,
                    number_of_firms,
                    _to_float_or_none(row[6]),
                    _to_float_or_none(row[7]),
                    _to_float_or_none(row[8]),
                    _to_float_or_none(row[9]),
                )
            )

    return positive_rows, negative_rows


def insert_ev_multiples_records(csv_path: Optional[str] = None) -> Tuple[int, int]:
    """
    Insert EV multiples CSV data into both NYU tables.

    Mapping used:
    - Positives table: columns 1-6 (Industry Name through first EV/EBIT (1-t)).
    - Negatives table: columns 1-2 plus columns 7-10 (second EV/EBITDAR&D through final EV/EBIT (1-t)).

    Returns:
    - Tuple of (inserted_positive_rows, inserted_negative_rows).
    """
    source_csv_path = csv_path or _default_csv_path()
    positive_rows, negative_rows = _build_insert_rows(source_csv_path)

    if not positive_rows and not negative_rows:
        return 0, 0

    conn = get_mysql_connection(**BASE_DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            if positive_rows:
                cursor.executemany(
                    f"""
					INSERT INTO {POSITIVES_TABLE}
					(IndustryName, NumberOfFirms, EV_EBITDARnD, EV_EBITDA, EV_EBIT, EV_EBIT_1t)
					VALUES (%s, %s, %s, %s, %s, %s)
					""",
                    positive_rows,
                )

            if negative_rows:
                cursor.executemany(
                    f"""
					INSERT INTO {NEGATIVES_TABLE}
					(IndustryName, NumberOfFirms, EV_EBITDARnD, EV_EBITDA, EV_EBIT, EV_EBIT_1t)
					VALUES (%s, %s, %s, %s, %s, %s)
					""",
                    negative_rows,
                )

        conn.commit()
    finally:
        conn.close()

    return len(positive_rows), len(negative_rows)
