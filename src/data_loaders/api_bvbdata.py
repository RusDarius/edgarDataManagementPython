from copy import deepcopy
import requests
from constants.bvb_constants import BVB_TICKERS_FIELDS


BVB_FINANCIAL_INSTRUMENTS_URL = "https://www.bvb.ro/FinancialInstruments/Markets/Search.ashx?l=&t=S&c=&lang=ro&tr=False"
BVB_SCAN_LENGTH = 500


class ApiBvbClient:
    """Client for interacting with the Bvb API."""

    def __init__(self, user_agent):
        """Initialize the API client with a user agent and headers."""
        self.headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }

    def map_bvb_response_to_dicts(self, response_json):
        """
        Maps each entry in response_json["aaData"] to a dict with keys from BVB_TICKERS_FIELDS in declaration order.
        """

        field_names = [
            v
            for k, v in BVB_TICKERS_FIELDS.__dict__.items()
            if not k.startswith("__") and k.isupper()
        ]
        mapped = []
        for entry in response_json["aaData"]:
            mapped.append({field: entry[idx] for idx, field in enumerate(field_names)})
        return mapped

    def scan_bvb_all_stocks(
        self,
        timeout: int = 30,
    ):
        """
        Sends a POST request to the BVB Financial Instruments endpoint with the required form data payload.
        Returns the response object.
        """
        form_data = (
            "draw=4&columns%5B0%5D%5Bdata%5D=0&columns%5B0%5D%5Bname%5D="
            "&columns%5B0%5D%5Bsearchable%5D=true&columns%5B0%5D%5Borderable%5D=true&columns%5B0%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B0%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B1%5D%5Bdata%5D=1&columns%5B1%5D%5Bname%5D="
            "&columns%5B1%5D%5Bsearchable%5D=true&columns%5B1%5D%5Borderable%5D=true&columns%5B1%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B1%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B2%5D%5Bdata%5D=2&columns%5B2%5D%5Bname%5D="
            "&columns%5B2%5D%5Bsearchable%5D=true&columns%5B2%5D%5Borderable%5D=true&columns%5B2%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B2%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B3%5D%5Bdata%5D=3&columns%5B3%5D%5Bname%5D="
            "&columns%5B3%5D%5Bsearchable%5D=true&columns%5B3%5D%5Borderable%5D=true&columns%5B3%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B3%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B4%5D%5Bdata%5D=4&columns%5B4%5D%5Bname%5D="
            "&columns%5B4%5D%5Bsearchable%5D=true&columns%5B4%5D%5Borderable%5D=true&columns%5B4%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B4%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B5%5D%5Bdata%5D=5&columns%5B5%5D%5Bname%5D="
            "&columns%5B5%5D%5Bsearchable%5D=true&columns%5B5%5D%5Borderable%5D=true&columns%5B5%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B5%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B6%5D%5Bdata%5D=6&columns%5B6%5D%5Bname%5D="
            "&columns%5B6%5D%5Bsearchable%5D=true&columns%5B6%5D%5Borderable%5D=true&columns%5B6%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B6%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B7%5D%5Bdata%5D=7&columns%5B7%5D%5Bname%5D="
            "&columns%5B7%5D%5Bsearchable%5D=true&columns%5B7%5D%5Borderable%5D=true&columns%5B7%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B7%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B8%5D%5Bdata%5D=8&columns%5B8%5D%5Bname%5D="
            "&columns%5B8%5D%5Bsearchable%5D=true&columns%5B8%5D%5Borderable%5D=true&columns%5B8%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B8%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B9%5D%5Bdata%5D=9&columns%5B9%5D%5Bname%5D="
            "&columns%5B9%5D%5Bsearchable%5D=true&columns%5B9%5D%5Borderable%5D=true&columns%5B9%5D%5Bsearch%5D%5Bvalue%5D="
            "&columns%5B9%5D%5Bsearch%5D%5Bregex%5D=false&columns%5B10%5D%5Bdata%5D=10&columns%5B10%5D%5Bname%5D="
            "&columns%5B10%5D%5Bsearchable%5D=true&columns%5B10%5D%5Borderable%5D=false&columns%5B10%5D%5Bsearch%5D%5Bvalue%5D="
            f"&columns%5B10%5D%5Bsearch%5D%5Bregex%5D=false&order%5B0%5D%5Bcolumn%5D=1&order%5B0%5D%5Bdir%5D=asc&start=0&length={BVB_SCAN_LENGTH}&search%5Bvalue%5D=&search%5Bregex%5D=false"
        )
        headers = self.headers.copy()
        response = requests.post(
            BVB_FINANCIAL_INSTRUMENTS_URL,
            headers=headers,
            data=form_data,
            timeout=timeout,
        )
        response.raise_for_status()

        result = self.map_bvb_response_to_dicts(response.json())

        return result
