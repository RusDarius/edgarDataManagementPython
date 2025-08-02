import os
import sys
import requests
from arelle import Cntlr
from arelle.ModelXbrl import ModelXbrl
from urllib.parse import urljoin, urlparse

# Add src to sys.path to allow imports from generic_utils
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.generic_utils.accept_utf8_encoding import accept_utf8_encoding

accept_utf8_encoding()


# === UTILITY FUNCTIONS ===
def detect_file_format(file_path):
    """
    Detect whether a file is iXBRL, traditional XBRL, or HTML.
    
    Returns:
        str: 'ixbrl', 'xbrl', 'html', or 'unknown'
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read(2000)  # Read first 2KB to determine format
        
        # Check for iXBRL (inline XBRL) indicators
        if any(indicator in content for indicator in ['ix:', 'xmlns:ix', 'inline XBRL']):
            return 'ixbrl'
        
        # Check for traditional XBRL indicators
        if any(indicator in content for indicator in ['<xbrl', 'xmlns:xbrl', 'xbrl.org/2003/instance']):
            return 'xbrl'
        
        # Check if it's XML at all
        if content.strip().startswith('<?xml'):
            return 'xml'
        
        # If it contains HTML but no XBRL, it's traditional HTML filing
        if any(indicator in content.lower() for indicator in ['<html', '<body', 'doctype html']):
            return 'html'
        
        return 'unknown'
        
    except Exception as e:
        print(f"⚠️ Error detecting file format: {e}")
        return 'unknown'


def find_xbrl_instance_file(base_url, filing_basename):
    """
    Try to find the separate XBRL instance file for traditional filings.
    
    Returns:
        str: URL of XBRL instance file if found, None otherwise
    """
    import requests
    from urllib.parse import urljoin
    
    # Common patterns for XBRL instance files
    possible_names = [
        f"{filing_basename}.xml",
        f"{filing_basename}_htm.xml",
        f"{filing_basename}.xbrl",
        f"FilingSummary.xml",  # Sometimes used by SEC
    ]
    
    headers = {"User-Agent": "YourName Contact@Email.com"}
    
    for filename in possible_names:
        url = urljoin(base_url, filename)
        try:
            response = requests.head(url, headers=headers, timeout=10)
            if response.status_code == 200:
                print(f"✅ Found XBRL instance file: {filename}")
                return url
        except:
            continue
    
    print("⚠️ No separate XBRL instance file found")
    return None


def clean_value_for_display(value, max_length=50):
    """Clean and format a value for safe display, avoiding raw HTML/XML content."""
    if not value:
        return "N/A"

    # Convert to string and strip whitespace
    str_value = str(value).strip()

    # If it contains HTML tags or is very long, summarize it
    if "<" in str_value and ">" in str_value:
        return f"<HTML_CONTENT:{len(str_value)}_chars>"

    # If it's very long, truncate it
    if len(str_value) > max_length:
        return f"{str_value[:max_length]}... ({len(str_value)} chars)"

    return str_value


def suppress_arelle_warnings():
    """
    Suppress non-critical Arelle warnings that don't affect fact extraction.

    Common warnings that can be safely ignored:
    - invalidTransformation: SEC-specific transformation namespaces not recognized
    - IOerror: Missing linkbase files that aren't required for basic fact extraction
    - Schema validation warnings for presentation/calculation linkbases
    """
    import logging
    import warnings

    # Suppress specific warnings
    warnings.filterwarnings("ignore", category=UserWarning, module="arelle")

    # Set Arelle logger to only show errors
    arelle_logger = logging.getLogger("arelle")
    arelle_logger.setLevel(logging.ERROR)

    return arelle_logger


# === 1. CONFIGURATION ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Create savedData/dataExtraction1 path (savedData is at same level as src)
PROJECT_ROOT = os.path.dirname(
    os.path.dirname(SCRIPT_DIR)
)  # Go up from src/demo_flows to project root
DOWNLOAD_DIR = os.path.join(PROJECT_ROOT, "savedData", "dataExtraction1")
IXBRL_URL = "https://www.sec.gov/Archives/edgar/data/1048911/000095012317006152/fdx-10k_20170531.htm"
LOCAL_FILENAME = os.path.join(DOWNLOAD_DIR, "fdx-20170531.htm")
TARGET_TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
}


# === 2. DOWNLOAD FILING AND SCHEMAS ===
def ensure_download_directory():
    """Ensure the download directory exists."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    print(f"📁 Download directory: {DOWNLOAD_DIR}")


def download_file(url, local_path, description="file"):
    print(f"Downloading {description} from: {url}")
    headers = {
        "User-Agent": "YourName Contact@Email.com",
    }
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(response.content)
    print(f"Saved {description} to {local_path}")


def download_ixbrl(url, local_path):
    download_file(url, local_path, "iXBRL filing")


def cleanup_downloaded_files():
    """Clean up downloaded files after processing."""
    import shutil

    if os.path.exists(DOWNLOAD_DIR):
        try:
            shutil.rmtree(DOWNLOAD_DIR)
            print(f"🧹 Cleaned up temporary files from {DOWNLOAD_DIR}")
        except Exception as e:
            print(f"⚠️ Could not clean up directory {DOWNLOAD_DIR}: {e}")


def download_required_schemas(ixbrl_file_path):
    """Download the XSD schema files and related XML files referenced by the iXBRL filing."""
    import re
    from urllib.parse import urljoin, urlparse

    base_url = "https://www.sec.gov/Archives/edgar/data/1048911/000095012317006152/"
    schema_dir = os.path.dirname(ixbrl_file_path)

    # Read the iXBRL file to find schema references
    with open(ixbrl_file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Find schema references in the file
    schema_patterns = [
        r'schemaLocation="([^"]*\.xsd)"',
        r'xsi:schemaLocation="[^"]*?\s+([^"]*\.xsd)"',
        r'href="([^"]*\.xsd)"',
    ]

    schema_files = set()
    for pattern in schema_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        schema_files.update(matches)

    # Also look for the main schema file and related XML files based on filing name
    filing_basename = os.path.splitext(os.path.basename(ixbrl_file_path))[0]
    main_schema = f"{filing_basename}.xsd"
    schema_files.add(main_schema)

    # Add the related XML files that are typically referenced
    related_files = [
        f"{filing_basename}_lab.xml",  # Labels linkbase
        f"{filing_basename}_pre.xml",  # Presentation linkbase
        f"{filing_basename}_def.xml",  # Definition linkbase
        f"{filing_basename}_cal.xml",  # Calculation linkbase
    ]
    schema_files.update(related_files)

    print(f"📋 Downloading {len(schema_files)} schema and linkbase files...")

    # Download each schema file
    downloaded_schemas = []
    for schema_file in schema_files:
        # Skip if it's already a full URL or looks like a standard taxonomy
        if schema_file.startswith("http") or "www.xbrl.org" in schema_file:
            continue

        schema_url = urljoin(base_url, schema_file)
        local_schema_path = os.path.join(schema_dir, os.path.basename(schema_file))

        if not os.path.exists(local_schema_path):
            try:
                download_file(schema_url, local_schema_path, f"linkbase {schema_file}")
                downloaded_schemas.append(local_schema_path)
            except Exception as e:
                print(f"⚠️  Could not download {schema_file}: {e}")
        else:
            print(f"✅ File already exists: {local_schema_path}")
            downloaded_schemas.append(local_schema_path)

    return downloaded_schemas


# === 3. PARSE WITH ARELLE ===
def extract_facts_from_ixbrl(file_path):
    """Extract facts from iXBRL file using arelle-release with enhanced segment/dimension parsing."""
    
    # First, detect the file format
    file_format = detect_file_format(file_path)
    print(f"🔍 Detected file format: {file_format.upper()}")
    
    if file_format == 'html':
        print("⚠️ This is a traditional HTML filing, not iXBRL")
        print("🔍 Looking for separate XBRL instance file...")
        
        # Try to find separate XBRL instance file
        base_url = IXBRL_URL.rsplit('/', 1)[0] + '/'
        filing_basename = os.path.splitext(os.path.basename(file_path))[0]
        
        xbrl_url = find_xbrl_instance_file(base_url, filing_basename)
        if xbrl_url:
            # Download the XBRL instance file
            xbrl_filename = os.path.join(os.path.dirname(file_path), os.path.basename(xbrl_url))
            if not os.path.exists(xbrl_filename):
                print(f"📥 Downloading XBRL instance file: {os.path.basename(xbrl_url)}")
                download_file(xbrl_url, xbrl_filename, "XBRL instance file")
            
            # Use the XBRL file instead
            file_path = xbrl_filename
            file_format = detect_file_format(file_path)
            print(f"🔄 Switching to XBRL file format: {file_format.upper()}")
        else:
            print("❌ No XBRL instance file found - cannot extract facts with Arelle")
            print("🔄 Falling back to HTML parsing for basic revenue extraction...")
            return extract_facts_from_html_only(file_path)

    # Suppress non-critical Arelle warnings
    # arelle_logger = suppress_arelle_warnings()
    # original_level = arelle_logger.level

    try:
        # Create controller with minimal logging
        cntlr = Cntlr.Cntlr()
        model_manager = cntlr.modelManager

        # Load with appropriate settings based on file format
        is_inline = file_format == 'ixbrl'
        
        model_xbrl: ModelXbrl = model_manager.load(
            file_path,
            isInlineXBRL=is_inline,
            # validateInferences=False,  # Skip validation to avoid schema issues
            # validateInlineXBRL=False,  # Skip inline validation
            # validateDuplicateFacts=False,  # Skip duplicate fact validation
            # validateDisclosureSystem=False,  # Skip disclosure system validation
        )

        if not model_xbrl:
            raise Exception("Failed to load XBRL instance.")

    finally:
        pass
        # Restore original logging level
        # arelle_logger.setLevel(original_level)

    # Count facts for summary
    facts_with_concepts = 0
    facts_without_concepts = 0

    for fact in model_xbrl.facts:
        if fact.concept is not None:
            facts_with_concepts += 1
        else:
            facts_without_concepts += 1

    print(
        f"📊 Facts summary: {facts_with_concepts} with concepts, {facts_without_concepts} without concepts"
    )

    revenue_fact_count = 0
    revenue_facts = []
    
    for fact in model_xbrl.facts:
        concept = fact.concept
        context = fact.context

        if concept is None or context is None:
            continue

        # Check if this is a revenue fact we're interested in
        is_revenue_fact = False
        concept_name = getattr(concept, 'name', '') or ''
        
        for target_tag in TARGET_TAGS:
            if target_tag in concept_name:
                is_revenue_fact = True
                break
        
        # Also check for general revenue patterns
        if not is_revenue_fact and any(
            pattern in concept_name.lower() for pattern in ["revenue", "sales"]
        ):
            is_revenue_fact = True

        if is_revenue_fact:
            revenue_fact_count += 1
            clean_value = clean_value_for_display(fact.value)
            print(f"🎯 Found revenue fact via Arelle: {concept.qname} = {clean_value}")
            
            # Check for segment dimensions
            has_segments = False
            segment_count = 0
            
            if hasattr(context, "segDimValues") and context.segDimValues:
                has_segments = True
                segment_count = len(context.segDimValues)
                print(f"  → {segment_count} segment dimensions found")
            else:
                print(f"  → No segment dimensions")
            
            # Store the fact for analysis
            revenue_fact = {
                "concept": concept_name,
                "qname": str(concept.qname),
                "value": fact.value,
                "context_id": fact.contextID,
                "unit": getattr(fact, 'unitID', None),
                "has_segments": has_segments,
                "segment_count": segment_count,
                "decimals": getattr(fact, 'decimals', None),
                "period": str(context.period) if context.period else None,
            }
            revenue_facts.append(revenue_fact)

    if revenue_fact_count == 0:
        print("⚠️ No revenue facts found via Arelle")
    else:
        print(f"✅ Found {revenue_fact_count} revenue facts via Arelle")

    print(f"📋 Processing with Arelle - found {len(model_xbrl.facts)} total facts")

    # Summary of revenue facts
    print(f"\n� Revenue extraction complete: {len(revenue_facts)} facts found")
    facts_with_segments = [f for f in revenue_facts if f["has_segments"]]
    print(
        f"  → {len(facts_with_segments)} with segments, {len(revenue_facts) - len(facts_with_segments)} without"
    )

    return model_xbrl, revenue_facts


def extract_facts_from_html_only(file_path):
    """
    Fallback method to extract basic financial information from HTML filing
    when no XBRL instance file is available.
    """
    print("🔍 Using HTML-only parsing for basic financial data extraction")
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"❌ Failed to read HTML file: {e}")
        return None, []

    revenue_facts = []
    
    # Look for revenue patterns in HTML tables and text
    import re
    
    # Common revenue patterns in SEC filings
    revenue_patterns = [
        r'Revenue[s]?\s*[:\-]?\s*\$?\s*([\d,]+)',
        r'Net\s+revenue[s]?\s*[:\-]?\s*\$?\s*([\d,]+)',
        r'Total\s+revenue[s]?\s*[:\-]?\s*\$?\s*([\d,]+)',
        r'Sales\s*[:\-]?\s*\$?\s*([\d,]+)',
        r'Net\s+sales\s*[:\-]?\s*\$?\s*([\d,]+)',
    ]
    
    print(f"🔍 Searching for revenue patterns in HTML content...")
    
    for pattern in revenue_patterns:
        matches = re.finditer(pattern, content, re.IGNORECASE)
        for match in matches:
            value = match.group(1).replace(',', '')
            if value.isdigit() and len(value) >= 4:  # At least 4 digits for meaningful revenue
                revenue_fact = {
                    "concept": "Revenue (HTML extracted)",
                    "qname": "html:Revenue",
                    "value": value,
                    "context_id": "html_context",
                    "unit": "USD (assumed)",
                    "has_segments": False,
                    "segment_count": 0,
                    "decimals": None,
                    "period": "Unknown",
                    "extraction_method": "HTML pattern matching"
                }
                revenue_facts.append(revenue_fact)
                print(f"🎯 Found revenue (HTML): {clean_value_for_display(value)}")
    
    print(f"📈 HTML extraction complete: {len(revenue_facts)} potential revenue values found")
    
    return None, revenue_facts


# === 4. RUN SCRIPT ===
if __name__ == "__main__":
    print("🚀 Starting XBRL revenue extraction")

    # Ensure download directory exists
    ensure_download_directory()

    # Download file if needed
    if not os.path.exists(LOCAL_FILENAME):
        print("📥 Downloading iXBRL file...")
        download_ixbrl(IXBRL_URL, LOCAL_FILENAME)
    else:
        print("📄 Using existing iXBRL file")

    # Download required schema files
    print("📥 Checking schema files...")
    try:
        downloaded_schemas = download_required_schemas(LOCAL_FILENAME)
    except Exception as e:
        print(f"⚠️ Schema download issue: {e}")

    try:
        print("🔄 Loading XBRL with Arelle (validation warnings suppressed)...")
        print(
            "💡 Note: Transformation namespace warnings are expected and can be safely ignored"
        )
        model_xbrl, revenue_facts = extract_facts_from_ixbrl(LOCAL_FILENAME)

        print(f"\n✅ Processing completed!")
        print(f"✅ Extracted {len(revenue_facts)} revenue facts")

        if revenue_facts:
            print(f"\n📋 Summary:")
            for fact in revenue_facts[:3]:
                status = "🔗" if fact["has_segments"] else "—"
                print()
                cleaned_value = clean_value_for_display(fact["value"])
                print(f"  {status} {fact['concept']}: {cleaned_value}")

    except Exception as e:
        print(f"❌ Processing failed: {e}")
        print("⚠️ Enable debug mode for detailed error information")

    finally:
        # Clean up downloaded files
        print("\n🧹 Cleaning up temporary files... DISABLED NOW")
        # cleanup_downloaded_files()
