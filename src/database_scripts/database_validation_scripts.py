import json
import os
from datetime import date, datetime

from db.connection_credentials import BASE_DB_CONFIG
from db.connection_provider import get_mysql_connection


def _normalize_date(value):
	if isinstance(value, (datetime, date)):
		return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
	if value is None:
		return None
	return str(value)


def _default_output_path():
	project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
	return os.path.join(
		project_root,
		"documentation",
		"edgarBulkDataSample",
		"all_cik_filings.json",
	)


def export_all_cik_filings(output_path=None):
	"""
	Build a filings list per CIK and write a JSON array to disk.
	Each entry includes a Cik and a Filings list formatted like sample_all_fillings1.json.
	"""
	conn = get_mysql_connection(**BASE_DB_CONFIG)
	cursor = conn.cursor(dictionary=True)

	query = """
		SELECT DISTINCT
			f.Cik AS Cik,
			f.Adsh AS Adsh,
			f.FilingType AS FilingType,
			f.PeriodEnd AS PeriodEnd,
			f.FiscalPeriod AS FiscalPeriod
		FROM edgar_financial_data_concepts f
		ORDER BY f.Cik, f.PeriodEnd DESC
	"""

	try:
		cursor.execute(query)
		rows = cursor.fetchall()
	finally:
		cursor.close()
		conn.close()

	filings_by_cik = {}
	for row in rows:
		cik = row.get("Cik")
		if cik is None:
			continue
		filings_by_cik.setdefault(cik, []).append(
			{
				"Adsh": row.get("Adsh"),
				"FilingType": row.get("FilingType"),
				"PeriodEnd": _normalize_date(row.get("PeriodEnd")),
				"FiscalPeriod": row.get("FiscalPeriod") or "",
			}
		)

	output_data = [
		{"Cik": cik, "Filings": filings}
		for cik, filings in filings_by_cik.items()
	]

	output_path = output_path or _default_output_path()
	os.makedirs(os.path.dirname(output_path), exist_ok=True)

	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(output_data, f, indent=2, ensure_ascii=True)

	print(f"Wrote {len(output_data)} CIK entries to {output_path}.")


def _parse_iso_date(value):
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	if not value:
		return None
	try:
		return datetime.fromisoformat(str(value)).date()
	except ValueError:
		return None


def _detect_20f_gap(filings):
	if len(filings) < 2:
		return []
	gaps = []
	for idx in range(1, len(filings)):
		prev_filing = filings[idx - 1]
		curr_filing = filings[idx]
		prev_date = _parse_iso_date(prev_filing.get("PeriodEnd"))
		curr_date = _parse_iso_date(curr_filing.get("PeriodEnd"))
		if not prev_date or not curr_date:
			gaps.append(
				{
					"Expected": "330-400 days",
					"Before": prev_filing,
					"After": curr_filing,
					"DeltaDays": None,
				}
			)
			continue
		delta_days = (prev_date - curr_date).days
		if delta_days < 330 or delta_days > 400:
			gaps.append(
				{
					"Expected": "330-400 days",
					"Before": prev_filing,
					"After": curr_filing,
					"DeltaDays": delta_days,
				}
			)
	return gaps


def _detect_fiscal_period_gap(filings):
	if len(filings) < 2:
		return []
	next_period = {
		"FY": "Q3",
		"Q3": "Q2",
		"Q2": "Q1",
		"Q1": "FY",
	}
	previous_period = filings[0].get("FiscalPeriod")
	if previous_period not in next_period:
		return [
			{
				"Expected": None,
				"Before": filings[0],
				"After": filings[1] if len(filings) > 1 else None,
			}
		]
	previous_filing = filings[0]
	gaps = []
	for filing in filings[1:]:
		period = filing.get("FiscalPeriod")
		expected = next_period.get(previous_period)
		if period != expected:
			gaps.append(
				{
					"Expected": expected,
					"Before": previous_filing,
					"After": filing,
				}
			)
		previous_period = period
		previous_filing = filing
	return gaps


def export_ciks_missing_filing_patterns(input_path, output_path):
	"""
	Read all_cik_filings.json and write a JSON array of CIKs with filing gaps.
	"""
	with open(input_path, encoding="utf-8") as f:
		data = json.load(f)

	ciks_with_gaps = []
	for entry in data:
		cik = entry.get("Cik")
		filings = entry.get("Filings") or []
		if not cik or not filings:
			continue
		filings_20f = [f for f in filings if f.get("FilingType") == "20-F"]
		filings_10qk = [
			f
			for f in filings
			if f.get("FilingType") in {"10-Q", "10-K"}
		]
		if filings_20f:
			gaps = _detect_20f_gap(filings_20f)
		else:
			gaps = _detect_fiscal_period_gap(filings_10qk)
		if gaps:
			ciks_with_gaps.append({"Cik": cik, "Missing": gaps})

	os.makedirs(os.path.dirname(output_path), exist_ok=True)
	with open(output_path, "w", encoding="utf-8") as f:
		json.dump(ciks_with_gaps, f, indent=2, ensure_ascii=True)

	print(f"Wrote {len(ciks_with_gaps)} CIKs with gaps to {output_path}.")

