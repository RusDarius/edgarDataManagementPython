#!/usr/bin/env python3
"""
Enhanced XBRL Data Extraction using build_sec_edgar_urls

This version improves upon the original test_python_package_for_xbrl_data.py by:
1. Using the proven URL building logic from missing_filings_utils.py
2. Supporting dynamic CIK and ADSH specification via declared parameters
3. Better error handling and file management
4. More comprehensive fact extraction

Usage:
    python test_python_package_for_xbrl_data_v2.py

To test different filings, modify the FILING_PARAMETERS in get_filing_parameters() function.

Examples of filings you can test:
- FedEx: CIK=1048911, ADSH=0000950170-23-048994, BatchTag=fdx-20230831.htm
- Microsoft: CIK=789019, ADSH=0000891020-23-000024, BatchTag=msft-20230930.htm
- Apple: CIK=320193, ADSH=0000320193-23-000077, BatchTag=aapl-20230930.htm
"""

import os
import sys

from data_loaders.arelle_data_loaders.arelle_missing_fillings_processor import (
    arelle_missing_fillings_processing_ticker,
)


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()


def main():

    ticker = "FDX"
    cik = "1048911"
    # ticker = "AIR"
    # cik = "1750"
    verbosity = True  # Set to False to suppress all non-exception output
    arelle_missing_fillings_processing_ticker(
        ticker=ticker, cik=cik, verbosity=verbosity
    )


if __name__ == "__main__":
    main()
