import requests


class ApiClient:
    """Client for interacting with the SEC EDGAR API."""

    def __init__(self, user_agent, base_url="https://data.sec.gov"):
        """Initialize the API client with a user agent and base URL."""
        self.base_url = base_url
        self.headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    def fetch_company_submissions(self, cik):
        """Fetch all company submissions for a given CIK from the SEC EDGAR API."""
        # CIK must be zero-padded to 10 digits
        cik_str = str(cik).lstrip("0").zfill(10)
        url = f"{self.base_url}/submissions/CIK{cik_str}.json"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()

    def fetch_8k_document(self, cik, accession, primary_doc):
        """Fetch the primary document for a specific 8-K filing."""
        accession_nodash = accession.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{primary_doc}"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.text

    def get_submission_by_form_period(self, cik, form_type, fiscal_period, fiscal_year):
        """
        Fetches the first submission for a given CIK, form type, fiscal period, and fiscal year.
        Uses fiscalYearEnd to robustly match fiscal quarters. Falls back to filingDate/reportDate if needed.
        Returns a dict with accessionNumber, primaryDocument, and filing URL if found, else None.
        """
        import datetime

        data = self.fetch_company_submissions(cik)
        import json, os

        save_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "savedData"
        )
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, "fetchedSubmissions.json")
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print(f"Saved fetched submissions to {save_path}")

        filings = data["filings"]["recent"]
        fiscal_year_end = data.get("fiscalYearEnd", "1231")  # MMDD
        print(f"Company: {data['name']}, Fiscal Year End: {fiscal_year_end}")

        # Print form types to help debug
        form_types = set(filings["form"])
        print(f"Available form types: {form_types}")

        # Print some period dates for the requested form type
        form_periods = [
            (
                i,
                (
                    filings.get("periodOfReport", [])[i]
                    if i < len(filings.get("periodOfReport", []))
                    else None
                ),
            )
            for i, form in enumerate(filings["form"])
            if form == form_type
        ]
        print(f"Form {form_type} periods: {form_periods[:5]}")

        def get_period_date(i):
            # Try periodOfReport, then reportDate, then filingDate
            for key in ["periodOfReport", "reportDate", "filingDate"]:
                arr = filings.get(key, [None] * len(filings["form"]))
                val = arr[i] if i < len(arr) else None
                if val and len(val) >= 10:
                    return val
            return None

        def is_quarter_match(period, fiscal_period, fiscal_year, fy_end_month):
            if not period:
                return False

            # Extract year from period
            period_year = period[:4] if len(period) >= 4 else None
            if not period_year or period_year != str(fiscal_year):
                return False

            try:
                dt = datetime.datetime.strptime(period, "%Y-%m-%d")
            except Exception:
                return False

            # FDX fiscal year mapping (with May 31 fiscal year end):
            # Q1: June-August (month 6-8)
            # Q2: September-November (month 9-11)
            # Q3: December-February (month 12,1,2)
            # Q4: March-May (month 3-5)

            # For fiscal year end in month M:
            # Q1: M+1, M+2, M+3
            # Q2: M+4, M+5, M+6
            # Q3: M+7, M+8, M+9
            # Q4: M+10, M+11, M+12

            m = fy_end_month  # Month of fiscal year end

            # More tolerant quarter mapping with month ranges
            q_months = {
                "Q1": [(m + 1) % 12 or 12, (m + 2) % 12 or 12, (m + 3) % 12 or 12],
                "Q2": [(m + 4) % 12 or 12, (m + 5) % 12 or 12, (m + 6) % 12 or 12],
                "Q3": [(m + 7) % 12 or 12, (m + 8) % 12 or 12, (m + 9) % 12 or 12],
                "Q4": [(m + 10) % 12 or 12, (m + 11) % 12 or 12, m],
                "FY": [m],  # Fiscal Year end month
            }

            if fiscal_period in q_months and dt.month in q_months[fiscal_period]:
                print(f"Match found: {period} for {fiscal_period} {fiscal_year}")
                return True

            return False

        # Find all matches, return the most recent
        matches = []
        fy_end_month = int(fiscal_year_end[:2])  # Get month portion of fiscal year end

        print(f"Looking for {form_type} for {fiscal_period} {fiscal_year}")
        print(f"Fiscal Year End Month: {fy_end_month}")

        for i, form in enumerate(filings["form"]):
            if form == form_type:
                period = get_period_date(i)
                if is_quarter_match(period, fiscal_period, fiscal_year, fy_end_month):
                    accession = filings["accessionNumber"][i]
                    primary_doc = filings["primaryDocument"][i]
                    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary_doc}"
                    filed = filings["filingDate"][i]
                    matches.append(
                        {
                            "accession": accession,
                            "primary_doc": primary_doc,
                            "url": url,
                            "filed": filed,
                            "period": period,
                        }
                    )

        if matches:
            # Return the most recent by period date
            matches.sort(key=lambda x: x["period"], reverse=True)
            print(f"Found {len(matches)} matching submissions")
            print(f"Selected: {matches[0]['url']}")
            return matches[0]

        print("No matching submissions found. Check fiscal periods and year.")
        return None
