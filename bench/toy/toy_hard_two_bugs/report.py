def summary(ledger):
    """[(category, cents), ...] sorted by cents descending, then name; plus grand total."""
    totals = ledger.totals()
    rows = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return rows, sum(totals.values())
