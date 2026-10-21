"""Tests for the calculator fixture. Two fail on purpose."""

from calculator.ops import add, average, subtract


def test_add_positive():
    assert add(2, 3) == 5


def test_add_negative():
    # Fails: ops.add clamps negatives to zero.
    assert add(-2, 5) == 3


def test_subtract():
    assert subtract(10, 4) == 6


def test_average():
    assert average([2, 4, 6]) == 4
