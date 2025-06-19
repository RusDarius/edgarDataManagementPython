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
