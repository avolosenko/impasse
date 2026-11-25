from ledger import Ledger
from report import summary


def test_single_category():
    led = Ledger().add("food", "12.30").add("food", "0.70")
    assert summary(led) == ([("food", 1300)], 1300)


def test_two_categories_sorted():
    led = Ledger().add("rent", "500").add("food", "20.5").add("food", "4.05")
    assert summary(led) == ([("rent", 50000), ("food", 2455)], 52455)


def test_ledgers_are_independent():
    a = Ledger().add("x", "1")
    b = Ledger()
    assert summary(b) == ([], 0)
    assert summary(a) == ([("x", 100)], 100)


def test_whole_amount():
    assert summary(Ledger().add("misc", "7")) == ([("misc", 700)], 700)
