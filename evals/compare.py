"""Positional columns; unordered rows are multisets, never deduplicated sets."""
from decimal import Decimal, InvalidOperation


def equal_cell(actual, expected, tolerance=Decimal('0.000001')):
    if actual is None or expected is None:
        return actual is expected
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    try:
        left, right = Decimal(str(actual)), Decimal(str(expected))
        return left.is_finite() and right.is_finite() and abs(left - right) <= tolerance
    except InvalidOperation:
        return type(actual) is type(expected) and actual == expected


def equal_rows(actual, expected, *, ordered=False):
    if len(actual) != len(expected):
        return False
    def same(left, right):
        return len(left) == len(right) and all(equal_cell(a, b) for a, b in zip(left, right))
    if ordered:
        return all(same(left, right) for left, right in zip(actual, expected))
    remaining = list(expected)
    for row in actual:
        match = next((i for i, candidate in enumerate(remaining) if same(row, candidate)), None)
        if match is None:
            return False
        remaining.pop(match)
    return not remaining
