def window_sums(xs, k):
    """Sums of all contiguous windows of length k, left to right."""
    if k <= 0:
        raise ValueError("k must be positive")
    out = []
    for i in range(len(xs) - k):
        out.append(sum(xs[i:i + k]))
    return out
