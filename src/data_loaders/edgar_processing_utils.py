def extract_guidance_from_text(text):
    """Extract lines containing guidance-related keywords from the given text."""
    # Simple keyword-based extraction; you can expand this with NLP
    guidance_keywords = [
        "guidance",
        "outlook",
        "forecast",
        "expect",
        "project",
        "estimate",
    ]
    lines = text.splitlines()
    guidance_lines = [
        line for line in lines if any(kw in line.lower() for kw in guidance_keywords)
    ]
    # Optionally, use regex to extract numbers or financial metrics
    return guidance_lines
