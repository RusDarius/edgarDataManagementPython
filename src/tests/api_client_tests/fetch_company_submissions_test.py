from data_loaders.api_client import ApiClient

from generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()

client = ApiClient(user_agent="Barnnabass daniOO7XbX@gmail.com")


def test_simple_fetch_submissions(cik):
    cik_str = str(cik).zfill(10)
    submissions_data = client.fetch_company_submissions(cik_str)
    print("submissions_data")
    print(submissions_data)


def test_check_xbrl_schemas_for_filing(cik, adsh, ticker):
    cik_str = str(cik).zfill(10)
    data = client.check_xbrl_schemas_for_filing(cik_str, adsh, ticker)
    print("data")
    print(data)


if __name__ == "__main__":
    cik = 1750
    adsh = "000141057825000519"
    ticker = "FDX"

    test_check_xbrl_schemas_for_filing(cik, adsh, ticker)
