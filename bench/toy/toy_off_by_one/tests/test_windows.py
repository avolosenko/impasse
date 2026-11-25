from windows import window_sums


def test_basic():
    assert window_sums([1, 2, 3, 4], 2) == [3, 5, 7]


def test_whole_list():
    assert window_sums([5, 6], 2) == [11]


def test_k_too_big():
    assert window_sums([1], 3) == []
