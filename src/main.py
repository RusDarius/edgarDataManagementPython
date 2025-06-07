from edgar.api_client import ApiClient
from edgar.edgar_financial_loader import run_financial_data_loader
from edgar.edgar_workflow import run_edgar_workflow, run_sec_cik_ticker_mapping_workflow


def main():
    # Uncomment the workflow you want to run:
    # run_edgar_workflow()
    # run_sec_cik_ticker_mapping_workflow()
    run_financial_data_loader()


if __name__ == "__main__":
    main()
