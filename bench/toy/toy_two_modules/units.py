def to_minutes(text):
    """Parse a duration "H:MM" into minutes."""
    hours, minutes = text.strip().split(":")
    return int(minutes) * 60 + int(hours)
