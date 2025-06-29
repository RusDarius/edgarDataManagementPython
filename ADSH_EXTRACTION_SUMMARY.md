# New ADSH-Based Concept Extraction Function

## Summary

I've implemented a new function `fetch_and_parse_submission_by_adsh()` in `src/data_loaders/sec_api_loader.py` that allows direct extraction of XBRL concepts from specific SEC filings using their ADSH (Accession Number).

## Key Features

### 1. Direct ADSH Access

- Takes an ADSH (e.g., "0000950170-24-108107") and extracts concepts directly
- Automatically constructs the SEC EDGAR URL for the filing
- Supports both provided primary document names or auto-detection

### 2. Comprehensive Fact Extraction

- Finds **ALL instances** of a concept tag in the document
- Extracts associated period information (start date, end date, period type)
- Supports both inline XBRL (iXBRL) and standard XBRL formats

### 3. Date/Period Extraction

- Parses XBRL context elements to extract period information
- Extracts dates from context IDs (e.g., "C_0001048911_20210601_20210831")
- Handles both duration periods (start/end dates) and instant dates

### 4. Smart Fact Selection

- Returns all facts found in `all_facts` array
- Selects the most recent fact as `best_fact` based on period end dates
- Applies proper scaling/decimals to calculate actual values

## Function Signature

```python
def fetch_and_parse_submission_by_adsh(cik, adsh, fact_tag, primary_document=None):
    """
    Args:
        cik: Company CIK (string or int)
        adsh: Accession Number (e.g., "0000950170-24-108107")
        fact_tag: XBRL fact tag to extract (e.g., 'Revenues')
        primary_document: Optional primary document filename

    Returns:
        Dictionary with:
        - all_facts: List of all fact instances found
        - best_fact: The most recent/latest fact instance
        - document_url: URL of the accessed document
        - error: Error message if any
    """
```

## Example Usage

```python
from data_loaders.sec_api_loader import fetch_and_parse_submission_by_adsh

# Extract revenue from FDX Q1 2025 filing
result = fetch_and_parse_submission_by_adsh(
    cik="1048911",
    adsh="0000950170-24-108107",
    fact_tag="Revenues",
    primary_document="fdx-20240831.htm"
)

# Access all facts found
for fact in result['all_facts']:
    print(f"Value: {fact['actual_value']} {fact['unit_ref']}")
    print(f"Period: {fact['period_start']} to {fact['period_end']}")

# Access the best (most recent) fact
best = result['best_fact']
if best:
    print(f"Latest Revenue: {best['actual_value']} {best['unit_ref']}")
```

## Integration with Missing Filings Handler

I've also updated `missing_filings_handler.py` to use this new function as a **fallback method**:

1. **Primary Method**: Uses `fetch_and_parse_submission_enhanced()` with period validation
2. **Fallback Method**: If primary fails, automatically tries the new ADSH-based extraction
3. **Dual Benefits**: Gets period validation when possible, but still extracts data when period matching fails

## Test Script

Created `test_adsh_extraction.py` which demonstrates:

- Testing multiple concepts from the same filing
- Testing the same concept across multiple years
- Error handling and troubleshooting tips
- Real examples using FDX filings from the missing filings report

## Key Advantages

1. **No Period Matching Required**: Extracts data even when fiscal period logic fails
2. **All Instances Captured**: Returns every occurrence of a concept with dates
3. **Flexible Document Access**: Works with known or auto-detected primary documents
4. **Robust Date Extraction**: Multiple methods to extract period information
5. **Fallback Integration**: Seamlessly integrated into existing workflows

## Files Modified

1. `src/data_loaders/sec_api_loader.py` - Added new function
2. `src/financial_execution_flows/missing_filings_handler.py` - Added fallback logic
3. `src/main.py` - Added test function
4. `test_adsh_extraction.py` - New standalone test script

## Testing

The function is designed to work with the FDX filings mentioned in your request:

- ADSH: "0000950170-24-108107" (Q1 2025)
- ADSH: "0000950170-23-048994" (Q1 2024)
- ADSH: "0000950170-22-018769" (Q1 2023)
- ADSH: "0001564590-21-048468" (Q1 2022)

This should solve the issue where "No matching submissions found" was preventing concept extraction!
