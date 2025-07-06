# FDX Revenue Facts Analysis Report

## Executive Summary

This report provides a comprehensive analysis of FDX's "RevenueFromContractWithCustomerExcludingAssessedTax" facts from Q1 2024 and FY 2024 SEC filings, including XBRL/iXBRL data extraction, quarters assignment validation, and equity component context analysis.

## Analysis Scope

- **Company**: FedEx Corporation (FDX)
- **CIK**: 1048911
- **Filings Analyzed**:
  - Q1 2024: ADSH 0000950170-23-048994 (https://www.sec.gov/Archives/edgar/data/1048911/000095017023048994/fdx-20230831.htm)
  - FY 2024: ADSH 0000950170-24-083577 (https://www.sec.gov/Archives/edgar/data/1048911/000095017024083577/fdx-20240531.htm)
- **Target Financial Concept**: RevenueFromContractWithCustomerExcludingAssessedTax

## Key Findings

### 1. Revenue Facts Extraction

- **Q1 2024 Filing**: 56 revenue facts extracted
- **FY 2024 Filing**: 108 revenue facts extracted
- **Document Format**: Both filings contain inline XBRL (iXBRL) data
- **Context Coverage**: All revenue facts have proper XBRL context references

### 2. Quarters Assignment Validation

✅ **ALL FACTS CORRECTLY ASSIGNED**

- **Q1 2024**: All 56 facts correctly assigned `qtrs = 1` (quarterly period)
- **FY 2024**: All 108 facts correctly assigned `qtrs = 4` (annual period)
- **Period Validation**: All periods match expected date ranges
  - Q1 2024: 2023-06-01 to 2023-08-31 (3-month period)
  - FY 2024: 2023-06-01 to 2024-05-31 (12-month period)

### 3. XBRL Context Analysis

- **Context Resolution**: All context references successfully found and parsed
- **Entity Information**: Consistent entity ID (0001048911) and scheme (http://www.sec.gov/CIK)
- **Period Types**: All revenue facts use duration periods (appropriate for revenue)

### 4. Segment Information

Revenue facts are properly segmented across multiple dimensions:

#### Business Segments:

- FedEx Express (`fdx:FedexExpressSegmentMember`)
- FedEx Ground (`fdx:FedexGroundSegmentMember`)
- FedEx Freight (`fdx:FedexFreightSegmentMember`)
- FedEx Services (`fdx:FedexServicesSegmentMember`)

#### Product/Service Segments:

- United States Overnight Box
- United States Overnight Envelope
- United States Deferred
- United States Domestic Package Revenue
- International Priority
- International Economy
- International Export Package Revenue
- International Domestic
- Package Revenue
- Freight Revenue
- Other services

#### Geographic Segments:

- United States (`country:US`)
- Non-US (`us-gaap:NonUsMember`)

#### Consolidation Items:

- Operating Segments (`us-gaap:OperatingSegmentsMember`)
- Intersegment Elimination (`us-gaap:IntersegmentEliminationMember`)
- Corporate Reconciling Items (`fdx:CorporateReconcilingItemsAndEliminationsMember`)

### 5. Equity Component Analysis

🔍 **NO EQUITY COMPONENTS FOUND**

- **Comprehensive Search**: Analyzed all context segments for equity-related terms
- **Search Terms**: "equity", "accumulated", "retained", "comprehensive", "component"
- **Result**: No equity component context data found in any revenue fact
- **Explanation**: Revenue facts typically do not have equity component dimensions as they represent income statement items rather than balance sheet equity components

## Technical Implementation

### Tools and Scripts Used

1. **Primary Test Script**: `src/tests/test_fdx_revenue_equity_contexts.py`

   - Enhanced XBRL context analysis
   - Comprehensive equity component detection
   - Detailed segment information extraction

2. **Supporting Infrastructure**:
   - `fetch_and_parse_all_financial_facts_from_submission_by_cik.py`: Core extraction engine
   - `extract_comprehensive_period_info.py`: Period and quarters calculation
   - BeautifulSoup for HTML/iXBRL parsing

### Data Extraction Process

1. **Filing Retrieval**: Download SEC filing documents via HTTP
2. **iXBRL Detection**: Identify inline XBRL content in HTML documents
3. **Context Extraction**: Parse all XBRL contexts (134 in Q1, 524 in FY)
4. **Fact Extraction**: Extract financial facts with context mapping
5. **Segment Analysis**: Parse explicit and typed segment members
6. **Equity Detection**: Search for equity-related context dimensions

## Validation Results

### ✅ Successful Validations

- **Quarters Assignment**: 100% accuracy (164/164 facts correctly assigned)
- **Context Resolution**: 100% success rate (all contexts found and parsed)
- **Period Matching**: All facts have correct start/end dates
- **Segment Coverage**: Comprehensive business, product, and geographic segmentation
- **Entity Consistency**: All facts reference correct FDX entity

### ❓ Expected Limitations

- **Equity Components**: None found (expected for revenue facts)
- **Balance Sheet Facts**: Not analyzed (outside scope of revenue analysis)

## Sample Revenue Fact Analysis

### Q1 2024 Example (Total Company Revenue)

- **Value**: $21,681 million USD
- **Period**: 2023-06-01 to 2023-08-31
- **Quarters**: 1 (quarterly)
- **Context**: No segment breakdown (consolidated total)
- **Validation**: ✅ Correct

### FY 2024 Example (Total Company Revenue)

- **Value**: $87,693 million USD
- **Period**: 2023-06-01 to 2024-05-31
- **Quarters**: 4 (annual)
- **Context**: No segment breakdown (consolidated total)
- **Validation**: ✅ Correct

## Recommendations

1. **Data Quality**: FDX's XBRL implementation is robust with proper context assignments
2. **Segment Analysis**: Rich segmentation enables detailed revenue analysis by business unit and geography
3. **Period Validation**: Quarters calculation logic is functioning correctly
4. **Future Enhancements**: Consider extending analysis to balance sheet items for equity component investigation

## Conclusion

The analysis confirms that FDX's Q1 2024 and FY 2024 SEC filings contain well-structured XBRL data with:

- ✅ Accurate quarters assignment for all revenue facts
- ✅ Comprehensive XBRL context information
- ✅ Detailed business and geographic segmentation
- ✅ Proper period and entity identification

No equity component context data was found in revenue facts, which is expected as equity components typically apply to balance sheet items rather than income statement revenue items.

---

_Report generated: December 2024_
_Analysis performed using enhanced XBRL extraction and validation tools_
