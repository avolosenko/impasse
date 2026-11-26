from schedule import total_minutes


def test_single():
    assert total_minutes(["1:30"]) == 90


def test_many():
    assert total_minutes(["0:15", "2:00", "0:45"]) == 180


def test_empty():
    assert total_minutes([]) == 0
