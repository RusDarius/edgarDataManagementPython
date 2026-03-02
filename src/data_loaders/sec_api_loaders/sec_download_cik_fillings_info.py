import json
import time
from pathlib import Path
import requests
from data_loaders.api_client import ApiClient
from db.connection_provider import get_mysql_connection
from db.sec_cik_tickers_mapping_operations import read_all_sec_cik_ticker_map
from generic_utils.log_to_files_util import log_to_file

user_agent="Barnnabass daniOO7XbX@gmail.com"


def download_all_company_submissions(
	output_subdir: str = "submissions_by_cik",
	pause_every: int = 10,
	pause_seconds: int = 1,
) -> dict:
	"""
	Download SEC submission JSON files for all CIKs in sec_cik_tickers_mapping.

	- Uses read_all_sec_cik_ticker_map to retrieve all CIK -> ticker entries.
	- Calls ApiClient.fetch_company_submissions for each CIK.
	- Waits `pause_seconds` every `pause_every` processed CIKs.
	- Prints a console message when SEC rate limiting (HTTP 429) is detected.
	- Logs missing/not-found CIK data to logs/missing_cik_submissions.log.

	Returns a dictionary with execution statistics.
	"""
	project_root = Path(__file__).resolve().parents[3]
	output_dir = project_root / "savedData" / output_subdir
	log_file = project_root / "logs" / "missing_cik_submissions.log"
	output_dir.mkdir(parents=True, exist_ok=True)

	connection = get_mysql_connection()
	client = ApiClient(user_agent=user_agent)

	try:
		cik_ticker_map = read_all_sec_cik_ticker_map(connection)
		total = len(cik_ticker_map)

		print(f"Starting SEC submission downloads for {total} CIK entries...")

		saved_count = 0
		missing_count = 0
		rate_limited_count = 0
		error_count = 0

		for index, (cik, ticker) in enumerate(cik_ticker_map.items(), start=1):
			cik_str = str(cik).zfill(10)
			try:
				payload = client.fetch_company_submissions(cik)

				if not payload or not payload.get("cik"):
					missing_count += 1
					log_to_file(
						log_file,
						f"CIK {cik_str} ({ticker}) returned empty or invalid submission payload.",
					)
				else:
					out_file = output_dir / f"CIK{cik_str}.json"
					with out_file.open("w", encoding="utf-8") as file_obj:
						json.dump(payload, file_obj, indent=2)
					saved_count += 1

			except requests.exceptions.HTTPError as http_err:
				status_code = (
					http_err.response.status_code
					if http_err.response is not None
					else None
				)

				if status_code == 429:
					rate_limited_count += 1
					print(
						f"[RATE LIMITED] SEC API returned HTTP 429 for CIK {cik_str} ({ticker})."
					)
				elif status_code == 404:
					missing_count += 1
					log_to_file(
						log_file,
						f"CIK {cik_str} ({ticker}) not found in SEC submissions endpoint (HTTP 404).",
					)
				else:
					error_count += 1
					log_to_file(
						log_file,
						f"CIK {cik_str} ({ticker}) request failed with HTTP {status_code}: {http_err}",
					)

			except Exception as exc:
				error_count += 1
				log_to_file(
					log_file,
					f"CIK {cik_str} ({ticker}) failed with unexpected error: {exc}",
				)

			if index % pause_every == 0:
				print(
					f"Processed {index}/{total} CIK entries. Sleeping for {pause_seconds}s to avoid SEC rate limiting..."
				)
				time.sleep(pause_seconds)

		summary = {
			"total_ciks": total,
			"saved": saved_count,
			"missing": missing_count,
			"rate_limited": rate_limited_count,
			"errors": error_count,
			"output_dir": str(output_dir),
			"log_file": str(log_file),
		}

		print("SEC submission batch download complete:", summary)
		return summary

	finally:
		connection.close()
