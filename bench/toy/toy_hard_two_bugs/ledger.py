from money import to_cents


class Ledger:
    def __init__(self, entries=[]):
        self.entries = entries

    def add(self, category, amount):
        """Record a purchase; `amount` is a string like "12.30"."""
        self.entries.append((category, to_cents(amount)))
        return self

    def totals(self):
        out = {}
        for category, cents in self.entries:
            out[category] = out.get(category, 0) + cents
        return out
