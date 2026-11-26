from units import to_minutes


def total_minutes(slots):
    """Total length in minutes of a list of "H:MM" durations."""
    return sum(to_minutes(s) for s in slots)
