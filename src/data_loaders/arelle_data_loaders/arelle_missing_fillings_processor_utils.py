from typing import Any, List

TARGET_REVENUE_TAGS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "SalesRevenueNet",
    "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "TotalRevenues",
}


def display_fact_summary(facts: List[Any]) -> None:
    """Display a summary of all extracted facts."""
    print(f"\n📊 Fact Extraction Summary")
    print("=" * 50)
    print(f"Total facts extracted: {len(facts)}")

    # Group by concept type
    concept_counts = {}
    for fact in facts:
        concept = fact[5]
        concept_counts[concept] = concept_counts.get(concept, 0) + 1

    # Show top 10 most common concepts
    sorted_concepts = sorted(concept_counts.items(), key=lambda x: x[1], reverse=True)
    print(f"\n🔝 Top 10 Most Common Concepts:")
    for i, (concept, count) in enumerate(sorted_concepts[:10], 1):
        print(f"  {i:2}. {concept}: {count} facts")

    # Show units summary
    units = {}
    for fact in facts:
        unit = fact[8]
        units[unit] = units.get(unit, 0) + 1

    print(f"\n💱 Units Summary:")
    for unit, count in sorted(units.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"  {unit}: {count} facts")


def analyze_revenue_facts(facts: List[Any]) -> None:
    """Analyze and display revenue-specific facts."""
    revenue_facts = [
        f
        for f in facts
        if any(target.lower() in str(f[5]).lower() for target in TARGET_REVENUE_TAGS)
    ]

    print(f"\n📈 Revenue Analysis")
    print("=" * 50)
    print(f"Total revenue facts found: {len(revenue_facts)}")
    fact_labels = [
        "CIK",
        "Ticker",
        "FilingType",
        "FiscalPeriod",
        "FiscalYear",
        "Concept",
        "Value",
        "ValueString",
        "Unit",
        "DataType",
        "Adsh",
        "PeriodEnd",
        "Ddate",
        "Segment",
        "Qtrs",
        "BatchTag",
    ]

    if revenue_facts:
        print(f"\n🎯 Revenue Facts Detail:")
        for idx, fact in enumerate(revenue_facts[:3], 1):
            print(f"\n--- Revenue Fact #{idx} ---")
            for label, value in zip(fact_labels, fact):
                print(f"{label}: {value}")
    else:
        print("⚠️ No revenue facts found")
