def check_values_types(facts):
    """
    Utility to track and print all unique types encountered for xValue in a list of Arelle facts.

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

    This function will print any additional types encountered at runtime.
    """
    xValue_types = set()

    # ...existing code...

    for fact in facts:
        xValue = getattr(fact, "xValue", None)
        xValue_types.add(type(xValue))

    # After the loop, print the summary of encountered types:
    print("All raw_value types encountered:", xValue_types)
