"""Minimal calculator package -- the fixture the agent reasons about.

Small on purpose: `add` contains a seeded bug that clamps negative operands.
"""

from __future__ import annotations


def add(a: int, b: int) -> int:
    """Add two numbers.

    BUG: negative operands are silently treated as zero.
    """
    if a < 0:
        a = 0
    if b < 0:
        b = 0
    return a + b


def subtract(a: int, b: int) -> int:
    return a - b


def average(values: list[int]) -> float:
    """Mean of a non-empty list."""
    return sum(values) / len(values)
