from typing import List, Dict, Optional
from datetime import datetime
from .extract_comprehensive_period_info import extract_comprehensive_period_info


def update_fact_selection_with_comprehensive_extraction(
    all_potential_facts: List[Dict],
    document_soup,
    expected_period_date: Optional[str] = None,
) -> Optional[Dict]:
    """
    Updated fact selection logic using the comprehensive period extraction method.

    Args:
        all_potential_facts: List of potential facts found
        document_soup: BeautifulSoup object of the entire document
        expected_period_date: Optional expected period end date for validation

    Returns:
        Best fact selected using comprehensive extraction, or None if no facts available
    """
    if not all_potential_facts:
        return None

    print(
        f"Selecting best fact from {len(all_potential_facts)} candidates using comprehensive extraction"
    )

    # Extract comprehensive period info for each fact
    enhanced_facts = []

    for fact_data in all_potential_facts:
        context_ref = fact_data.get("context")
        fact_element = fact_data.get("element")

        # Extract comprehensive period information
        period_info = extract_comprehensive_period_info(
            fact_element, context_ref, document_soup, expected_period_date
        )

        # Enhance the fact data with extracted period information
        enhanced_fact = fact_data.copy()
        enhanced_fact.update(
            {
                "period_start": period_info["period_start"],
                "period_end": period_info["period_end"],
                "period_type": period_info["period_type"],
                "extraction_method": period_info["extraction_method"],
                "context_found": period_info["context_found"],
                "period_match_score": period_info["period_match_score"],
            }
        )

        enhanced_facts.append(enhanced_fact)

        print(
            f"Enhanced fact: context={context_ref}, period_end={period_info['period_end']}, "
            f"method={period_info['extraction_method']}, score={period_info['period_match_score']}"
        )

    # Selection strategy: prioritize by period match score and recency
    best_fact = None

    # Strategy 1: If expected_period_date provided, select highest scoring match
    if expected_period_date:
        facts_with_scores = [
            f for f in enhanced_facts if f.get("period_match_score", 0) > 0
        ]
        if facts_with_scores:
            facts_with_scores.sort(key=lambda x: x["period_match_score"], reverse=True)
            best_fact = facts_with_scores[0]
            print(
                f"Selected fact with highest period match score: {best_fact['period_match_score']}"
            )

    # Strategy 2: Select most recent fact by period_end date
    if not best_fact:
        facts_with_dates = [f for f in enhanced_facts if f.get("period_end")]
        if facts_with_dates:
            try:
                # Sort by period_end date (most recent first)
                facts_with_dates.sort(
                    key=lambda x: datetime.strptime(x["period_end"], "%Y-%m-%d"),
                    reverse=True,
                )
                best_fact = facts_with_dates[0]
                print(
                    f"Selected most recent fact by period_end: {best_fact['period_end']}"
                )
            except ValueError as e:
                print(f"Error sorting by date: {e}")

    # Strategy 3: Fallback to first available fact
    if not best_fact:
        best_fact = enhanced_facts[0]
        print("Using first available fact as fallback")

    return best_fact
