def to_cents(text):
    """Parse a decimal amount like "12.3" or "12.30" or "7" into integer cents."""
    text = text.strip()
    if "." not in text:
        return int(text) * 100
    whole, frac = text.split(".")
    return int(whole) * 100 + int(frac)
