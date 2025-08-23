import decimal

try:
    from arelle.ModelValue import (
        gMonthDay,
        IsoDuration,
        gYear,
        gYearMonth,
        DateTime,
    )
except ImportError:
    # If arelle is not installed, define dummy types for type checking
    gMonthDay = IsoDuration = gYear = gYearMonth = DateTime = type(None)


def extract_numeric_value_from_fact(fact):
    """
    Extracts a numeric value from an Arelle fact's xValue, if possible.
    Returns float if numeric, else None.
    """
    raw_value = getattr(fact, "xValue", None)
    # Handle None and bool (treat bool as not numeric for financial data)
    if raw_value is None or isinstance(raw_value, bool):
        return None
    # Handle int, float, decimal.Decimal
    if isinstance(raw_value, (int, float, decimal.Decimal)):
        try:
            return float(raw_value)
        except Exception:
            return None
    # Handle string that can be parsed as float
    if isinstance(raw_value, str):
        try:
            return float(raw_value)
        except Exception:
            return None
    # Handle Arelle date/duration types, lists, or other custom types as not numeric
    return None


def extract_datatype_value_from_fact(fact):
    """
    Determines the type of xValue for a fact and returns it as a string.
    Known possible types for xValue (as of 2024-2025):
        - bool
        - NoneType
        - decimal.Decimal
        - int
        - float
        - str
        - list
        - arelle.ModelValue.gMonthDay
        - arelle.ModelValue.IsoDuration
        - arelle.ModelValue.gYear
        - arelle.ModelValue.gYearMonth
        - arelle.ModelValue.DateTime
    """
    raw_value = getattr(fact, "xValue", None)
    if raw_value is None:
        return "NoneType"
    if isinstance(raw_value, bool):
        return "bool"
    if isinstance(raw_value, int):
        return "int"
    if isinstance(raw_value, float):
        return "float"
    if isinstance(raw_value, decimal.Decimal):
        return "decimal.Decimal"
    if isinstance(raw_value, str):
        return "str"
    if isinstance(raw_value, list):
        return "list"
    if isinstance(raw_value, gMonthDay):
        return "arelle.ModelValue.gMonthDay"
    if isinstance(raw_value, IsoDuration):
        return "arelle.ModelValue.IsoDuration"
    if isinstance(raw_value, gYear):
        return "arelle.ModelValue.gYear"
    if isinstance(raw_value, gYearMonth):
        return "arelle.ModelValue.gYearMonth"
    if isinstance(raw_value, DateTime):
        return "arelle.ModelValue.DateTime"
    # Fallback for unknown types
    return type(raw_value).__name__


def extract_quarters_covered(context):
    """
    Infers the number of quarters (qtrs) a fact covers based on its context period.
    Returns an integer (1, 2, 3, 4) or None if not inferrable.
    """
    if context is None:
        return None
    # Try to get start and end dates from context
    start = None
    end = None
    if hasattr(context, "startDatetime") and context.startDatetime:
        start = context.startDatetime
    elif hasattr(context, "startDate") and context.startDate:
        start = context.startDate
    if hasattr(context, "endDate") and context.endDate:
        end = context.endDate
    elif hasattr(context, "instantDate") and context.instantDate:
        end = context.instantDate

    # If both dates are available, calculate quarters
    if start and end:
        try:
            from datetime import datetime

            if not isinstance(start, datetime):
                start = datetime.fromisoformat(str(start)[:10])
            if not isinstance(end, datetime):
                end = datetime.fromisoformat(str(end)[:10])
            months = (end.year - start.year) * 12 + (end.month - start.month)
            if months < 1:
                return 1
            qtrs = max(1, min(4, round(months / 3)))
            if months >= 11:
                return 4
            return qtrs
        except Exception:
            return None
    # If only end/instant is available, treat as instant fact (1 quarter)
    if (not start) and end:
        return 1
    return None


def extract_unit_string(unit_obj):
    """
    Extracts the readable unit string (e.g., 'USD', 'EUR', 'shares', 'pure') from an Arelle ModelUnit object.
    """
    if unit_obj is None:
        return None
    # Most common case: measures[0] is a list of numerators (e.g., [iso4217:USD])
    if hasattr(unit_obj, "measures") and unit_obj.measures:
        numerators = unit_obj.measures[0]
        if numerators and hasattr(numerators[0], "localName"):
            return numerators[0].localName  # e.g., 'USD'
        elif numerators:
            return str(numerators[0])
    # Fallback: try id or string representation
    if hasattr(unit_obj, "id"):
        return str(unit_obj.id)
    return str(unit_obj)


def extract_segment_string(context):
    """
    Extracts a readable segment/dimension string from an Arelle context object.
    Example output: "BusinessSegments:Express, GeographicAreas:US"
    """
    if context is None:
        return None

    # For dimensional XBRL, use qnameDims
    if hasattr(context, "qnameDims") and context.qnameDims:
        seg_parts = []
        for dim_qname, dim_value in context.qnameDims.items():
            # dim_qname: QName of the dimension (e.g., us-gaap:StatementBusinessSegmentsAxis)
            # dim_value: ModelDimensionValue object
            dim_name = getattr(dim_qname, "localName", str(dim_qname))
            # Try to get the member name (e.g., us-gaap:ExpressMember)
            member_name = (
                getattr(dim_value.memberQname, "localName", str(dim_value.memberQname))
                if hasattr(dim_value, "memberQname")
                else str(dim_value)
            )
            seg_parts.append(f"{dim_name}:{member_name}")
        return ", ".join(seg_parts) if seg_parts else None

    # For non-dimensional XBRL, you may have explicit segments in the XML
    if hasattr(context, "segment") and context.segment is not None:
        # Sometimes, context.segment.xmlValue is a string of XML; you can return it as a fallback
        xml_val = getattr(context.segment, "xmlValue", None)
        if xml_val:
            return str(xml_val)
    return None


def extract_datatype_string(fact_or_concept):
    """
    Extracts a simplified datatype string (e.g., 'monetary', 'shares', 'pure', 'string', 'integer')
    from an Arelle fact or concept object.
    """
    # Get the concept object
    concept = getattr(fact_or_concept, "concept", fact_or_concept)
    if concept is None:
        return None
    # Try the most common properties in order
    try:
        # 1. Try .type (most common)
        if hasattr(concept, "type") and concept.type is not None:
            type_name = str(concept.type.name).lower()
        # 2. Try .xbrlType (sometimes present)
        elif hasattr(concept, "xbrlType") and concept.xbrlType is not None:
            type_name = str(concept.xbrlType).lower()
        # 3. Try .typeQname (QName, fallback)
        elif hasattr(concept, "typeQname") and concept.typeQname is not None:
            type_name = str(concept.typeQname.localName).lower()
        # 4. Try .baseXbrlType (rare fallback)
        elif hasattr(concept, "baseXbrlType") and concept.baseXbrlType is not None:
            type_name = str(concept.baseXbrlType).lower()
        else:
            return None

        # Simplify to common types
        if "monetary" in type_name:
            return "monetary"
        elif "shares" in type_name:
            return "shares"
        elif "pure" in type_name:
            return "pure"
        elif "string" in type_name:
            return "string"
        elif "integer" in type_name:
            return "integer"
        elif "boolean" in type_name:
            return "boolean"
        elif "date" in type_name:
            return "date"
        # Add more mappings as needed
        return type_name
    except Exception:
        return None


def get_fact_string_value(fact):
    """
    Get the string value from an XBRL fact using multiple approaches.

    Args:
        fact: Arelle fact object

    Returns:
        Clean string value or None
    """
    # Try multiple approaches to get the fact value
    approaches = [
        lambda f: getattr(f, "value", None),  # Standard value
        lambda f: getattr(f, "textValue", None),  # Text value
        lambda f: getattr(f, "stringValue", None),  # String value
        lambda f: getattr(f, "effectiveValue", None),  # Effective value
    ]

    for approach in approaches:
        try:
            raw_value = approach(fact)
            if raw_value is not None:
                clean_value = extract_clean_text_from_value(raw_value)
                if clean_value:
                    if len(clean_value) > 200:
                        clean_value = "Text too long, keep fact only"
                    return clean_value
        except Exception:
            continue

    return None


# === UTILITY FUNCTIONS ===
def extract_clean_text_from_value(value):
    """
    Extract clean text from a fact value, handling HTML content and various data types.

    Args:
        value: The raw value from an XBRL fact

    Returns:
        Clean string representation of the value
    """
    if value is None:
        return None

    # Convert to string first
    str_value = str(value).strip()

    if not str_value:
        return None

    # Check if it contains HTML tags
    if "<" in str_value and ">" in str_value:
        try:
            # Use BeautifulSoup to extract text from HTML
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(str_value, "html.parser")
            clean_text = soup.get_text(separator=" ", strip=True)

            # Clean up extra whitespace
            import re

            clean_text = re.sub(r"\s+", " ", clean_text).strip()

            return clean_text if clean_text else None
        except Exception:
            # Fallback: simple regex-based HTML tag removal
            import re

            clean_text = re.sub(r"<[^>]+>", "", str_value).strip()
            # Clean up extra whitespace
            clean_text = re.sub(r"\s+", " ", clean_text)
            return clean_text if clean_text else None

    return str_value
