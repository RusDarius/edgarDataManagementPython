import requests


class ApiClient:
    def __init__(self, user_agent, base_url="https://data.sec.gov"):
        self.base_url = base_url
        self.headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
            "Host": "data.sec.gov",
        }

    def fetch_company_submissions(self, cik):
        # CIK must be zero-padded to 10 digits
        cik_str = str(cik).lstrip("0").zfill(10)
        url = f"{self.base_url}/submissions/CIK{cik_str}.json"
        response = requests.get(url, headers=self.headers)
        response.raise_for_status()
        return response.json()
