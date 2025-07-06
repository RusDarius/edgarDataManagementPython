#!/usr/bin/env python3
"""
Enhanced test script to extract and verify "RevenueFromContractWithCustomerExcludingAssessedTax" facts
for FDX Q1 2024 and FY 2024 filings, with detailed analysis of XBRL contexts including equity components.

This test demonstrates:
1. Quarters assignment validation between quarterly (Q1) and annual (FY) facts
2. XBRL context information extraction including segments and equity components
3. Analysis of contextRef attributes and entity/segment data

Usage:
    python test_fdx_revenue_equity_contexts.py
"""

import sys
import os
import requests
from bs4 import BeautifulSoup

# Add the src directory to Python path to import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from generic_utils.accept_utf8_encoding import accept_utf8_encoding

from data_loaders.fetch_and_parse_all_financial_facts_from_submission_by_cik import (
    fetch_and_parse_all_financial_facts_from_submission_by_cik,
)

accept_utf8_encoding()


def extract_context_details(soup, context_ref):
    """
    Extract detailed context information including segments and entity data.

    Args:
        soup: BeautifulSoup object of the document
        context_ref: Context reference ID

    Returns:
        Dictionary with context details including segments and entity information
    """
    context_details = {
        "context_id": context_ref,
        "period_info": {},
        "entity_info": {},
        "segment_info": {},
        "equity_components": [],
        "found": False,
    }

    if not context_ref:
        return context_details

    # Find the context element
    context_elem = soup.find("xbrli:context", {"id": context_ref}) or soup.find(
        "context", {"id": context_ref}
    )

    if context_elem:
        context_details["found"] = True

        # Extract period information
        period_elem = context_elem.find("period") or context_elem.find("xbrli:period")
        if period_elem:
            # Check for instant vs duration
            instant_elem = period_elem.find("instant") or period_elem.find(
                "xbrli:instant"
            )
            if instant_elem:
                context_details["period_info"]["type"] = "instant"
                context_details["period_info"][
                    "instant"
                ] = instant_elem.get_text().strip()
            else:
                start_elem = period_elem.find("startdate") or period_elem.find(
                    "xbrli:startdate"
                )
                end_elem = period_elem.find("enddate") or period_elem.find(
                    "xbrli:enddate"
                )
                if start_elem and end_elem:
                    context_details["period_info"]["type"] = "duration"
                    context_details["period_info"][
                        "start_date"
                    ] = start_elem.get_text().strip()
                    context_details["period_info"][
                        "end_date"
                    ] = end_elem.get_text().strip()

        # Extract entity information
        entity_elem = context_elem.find("entity") or context_elem.find("xbrli:entity")
        if entity_elem:
            # Get entity identifier
            identifier_elem = entity_elem.find("identifier") or entity_elem.find(
                "xbrli:identifier"
            )
            if identifier_elem:
                context_details["entity_info"][
                    "identifier"
                ] = identifier_elem.get_text().strip()
                context_details["entity_info"]["scheme"] = identifier_elem.get(
                    "scheme", ""
                )

            # Extract segment information
            segment_elem = entity_elem.find("segment") or entity_elem.find(
                "xbrli:segment"
            )
            if segment_elem:
                # Look for explicit members or typed members
                explicit_members = segment_elem.find_all(
                    "xbrldi:explicitmember"
                ) or segment_elem.find_all("explicitmember")
                typed_members = segment_elem.find_all(
                    "xbrldi:typedmember"
                ) or segment_elem.find_all("typedmember")

                # Process explicit members
                for member in explicit_members:
                    dimension = member.get("dimension", "")
                    member_value = member.get_text().strip()

                    segment_info = {
                        "type": "explicit",
                        "dimension": dimension,
                        "value": member_value,
                    }

                    context_details["segment_info"][dimension] = segment_info

                    # Check if this is related to equity components
                    if any(
                        equity_term in dimension.lower()
                        or equity_term in member_value.lower()
                        for equity_term in [
                            "equity",
                            "accumulated",
                            "retained",
                            "comprehensive",
                            "component",
                        ]
                    ):
                        context_details["equity_components"].append(
                            {
                                "dimension": dimension,
                                "value": member_value,
                                "type": "explicit",
                            }
                        )

                # Process typed members
                for member in typed_members:
                    dimension = member.get("dimension", "")
                    member_children = list(member.children)

                    segment_info = {
                        "type": "typed",
                        "dimension": dimension,
                        "children": [
                            str(child).strip()
                            for child in member_children
                            if str(child).strip()
                        ],
                    }

                    context_details["segment_info"][dimension] = segment_info

                    # Check for equity components in typed members
                    for child in member_children:
                        child_str = str(child).strip()
                        if any(
                            equity_term in child_str.lower()
                            for equity_term in [
                                "equity",
                                "accumulated",
                                "retained",
                                "comprehensive",
                                "component",
                            ]
                        ):
                            context_details["equity_components"].append(
                                {
                                    "dimension": dimension,
                                    "value": child_str,
                                    "type": "typed",
                                }
                            )

                # Look for any other segment children that might contain equity information
                for child in segment_elem.children:
                    if hasattr(child, "name") and child.name:
                        child_text = child.get_text().strip()
                        if child_text and any(
                            equity_term in child_text.lower()
                            for equity_term in [
                                "equity",
                                "accumulated",
                                "retained",
                                "comprehensive",
                            ]
                        ):
                            context_details["equity_components"].append(
                                {
                                    "element": child.name,
                                    "value": child_text,
                                    "type": "other_segment",
                                }
                            )

    return context_details


def test_fdx_revenue_equity_contexts():
    """Test RevenueFromContractWithCustomerExcludingAssessedTax facts with equity context analysis"""

    print("Testing FDX Revenue Quarters Assignment & Equity Contexts")
    print("=" * 80)

    # FDX CIK
    cik = "1048911"

    # Test cases: Q1 2024 and FY 2024 filings
    test_cases = [
        {
            "name": "FDX Q1 2024",
            "adsh": "0000950170-23-048994",  # Q1 2024 filing
            "expected_qtrs": 1,  # Quarterly filing should show 1 quarter
        },
        {
            "name": "FDX FY 2024",
            "adsh": "0000950170-24-083577",  # FY 2024 filing (annual)
            "expected_qtrs": 4,  # Annual filing should show 4 quarters
        },
    ]

    target_concept = "RevenueFromContractWithCustomerExcludingAssessedTax"

    for test_case in test_cases:
        print(f"\n{test_case['name']} ({test_case['adsh']})")
        print("-" * 60)

        try:
            # Extract ALL financial facts from the filing
            result = fetch_and_parse_all_financial_facts_from_submission_by_cik(
                cik=cik,
                adsh=test_case["adsh"],
                include_custom_facts=True,
                VERBOSITY=1,
            )

            if result.get("error"):
                print(f"[X] Error: {result['error']}")
                continue

            print(f"[OK] Total facts found: {result['total_facts_found']}")

            # Get the document for detailed context analysis
            document_url = result.get("document_url")
            if document_url:
                print(f"[OK] Document URL: {document_url}")

                # Fetch and parse the document for context analysis
                try:
                    resp = requests.get(
                        document_url,
                        headers={"User-Agent": "TestUser test@example.com"},
                    )
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, "html.parser")
                        print(f"[OK] Document fetched for context analysis")
                    else:
                        print(f"[X] Failed to fetch document: {resp.status_code}")
                        soup = None
                except Exception as e:
                    print(f"[X] Error fetching document: {e}")
                    soup = None
            else:
                soup = None

            # Find all Revenue facts
            revenue_facts = []
            for fact in result["all_financial_facts"]:
                tag_name = fact.get("tag_name", "")
                if target_concept.lower() in tag_name.lower():
                    revenue_facts.append(fact)

            print(f"[OK] Found {len(revenue_facts)} '{target_concept}' facts")

            if not revenue_facts:
                print(f"[!] No '{target_concept}' facts found in this filing")
                continue

            # Analyze each revenue fact with context details
            print(f"\n{target_concept} FACTS - DETAILED ANALYSIS:")
            print("=" * 70)

            for i, fact in enumerate(revenue_facts):
                print(f"\nFACT #{i+1}:")
                print("-" * 30)

                # Basic fact information
                context_ref = fact.get("context_ref")
                print(f"  Value: {fact.get('value', 'N/A')} {fact.get('unit_ref', '')}")
                print(f"  Context Ref: {context_ref}")
                print(
                    f"  Period: {fact.get('period_start', 'N/A')} to {fact.get('period_end', 'N/A')}"
                )
                print(f"  Period Type: {fact.get('period_type', 'N/A')}")
                print(f"  Qtrs: {fact.get('qtrs', 'N/A')}")

                # Quarters validation
                qtrs = fact.get("qtrs")
                print(f"\n  >> QUARTERS VALIDATION:")
                print(f"     Assigned Qtrs: {qtrs}")
                print(f"     Expected Qtrs: {test_case['expected_qtrs']}")

                if qtrs == test_case["expected_qtrs"]:
                    print(f"     ✓ CORRECT: Quarters assignment matches expectation")
                elif qtrs is None:
                    print(f"     ? UNCERTAIN: Quarters is None (irregular period?)")
                else:
                    print(
                        f"     ✗ MISMATCH: Expected {test_case['expected_qtrs']}, got {qtrs}"
                    )

                # Detailed context analysis if document is available
                if soup and context_ref:
                    print(f"\n  >> CONTEXT ANALYSIS:")
                    context_details = extract_context_details(soup, context_ref)

                    if context_details["found"]:
                        print(f"     Context Found: ✓")

                        # Period information
                        period_info = context_details["period_info"]
                        if period_info:
                            print(f"     Period Type: {period_info.get('type', 'N/A')}")
                            if period_info.get("type") == "duration":
                                print(
                                    f"     Start Date: {period_info.get('start_date', 'N/A')}"
                                )
                                print(
                                    f"     End Date: {period_info.get('end_date', 'N/A')}"
                                )
                            elif period_info.get("type") == "instant":
                                print(
                                    f"     Instant Date: {period_info.get('instant', 'N/A')}"
                                )

                        # Entity information
                        entity_info = context_details["entity_info"]
                        if entity_info:
                            print(
                                f"     Entity ID: {entity_info.get('identifier', 'N/A')}"
                            )
                            print(
                                f"     Entity Scheme: {entity_info.get('scheme', 'N/A')}"
                            )

                        # Segment information
                        segment_info = context_details["segment_info"]
                        if segment_info:
                            print(f"     Segments Found: {len(segment_info)}")
                            for dim, seg_data in segment_info.items():
                                print(f"       - {dim}: {seg_data}")
                        else:
                            print(f"     Segments: None")

                        # Equity components
                        equity_components = context_details["equity_components"]
                        if equity_components:
                            print(
                                f"     ✓ EQUITY COMPONENTS FOUND: {len(equity_components)}"
                            )
                            for comp in equity_components:
                                print(f"       - {comp}")
                        else:
                            print(f"     Equity Components: None")

                    else:
                        print(
                            f"     Context Found: ✗ (Context {context_ref} not found in document)"
                        )

                else:
                    print(
                        f"  >> CONTEXT ANALYSIS: Skipped (document not available or no context_ref)"
                    )

                print()

        except Exception as e:
            print(f"[X] Exception during extraction: {e}")
            import traceback

            print(traceback.format_exc())

    print("\n" + "=" * 80)
    print("SUMMARY:")
    print(f"This enhanced test extracts '{target_concept}' facts from FDX filings")
    print("and performs detailed analysis of:")
    print("  1. Quarters assignment validation (Q1=1, FY=4)")
    print("  2. XBRL context information including segments")
    print("  3. Equity component identification in context data")
    print("  4. Entity and period information extraction")


def main():
    """Main function to run the enhanced revenue and equity context test"""
    test_fdx_revenue_equity_contexts()


if __name__ == "__main__":
    main()
