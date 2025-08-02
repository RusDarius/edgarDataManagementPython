from datetime import datetime


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
