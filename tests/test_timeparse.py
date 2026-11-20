"""Reminder times (app/timeparse.py)."""
from datetime import datetime

import pytest

from timeparse import parse_time

FRI_0900 = datetime(2026, 11, 20, 9, 0)     # a Friday morning
FRI_1530 = datetime(2026, 11, 20, 15, 30)   # the same Friday afternoon


@pytest.mark.parametrize("text,now,want", [
    ("in 20 minutes", FRI_1530, datetime(2026, 11, 20, 15, 50)),
    ("in half an hour", FRI_1530, datetime(2026, 11, 20, 16, 0)),
    ("in 30 seconds", FRI_1530, datetime(2026, 11, 20, 15, 30, 30)),
    ("at 7 pm", FRI_1530, datetime(2026, 11, 20, 19, 0)),
    ("7:45 am", FRI_1530, datetime(2026, 11, 21, 7, 45)),          # already past today -> tomorrow
    ("at 7", FRI_1530, datetime(2026, 11, 20, 19, 0)),             # no am/pm, 7 am is past -> 7 pm
    ("tomorrow at 9", FRI_1530, datetime(2026, 11, 21, 9, 0)),
    ("noon", FRI_0900, datetime(2026, 11, 20, 12, 0)),
    ("monday 10am", FRI_1530, datetime(2026, 11, 23, 10, 0)),
    ("friday 10am", FRI_0900, datetime(2026, 11, 20, 10, 0)),      # today, still ahead
    ("friday 10am", FRI_1530, datetime(2026, 11, 27, 10, 0)),      # today but past -> next Friday
    ("friday", FRI_1530, datetime(2026, 11, 27, 9, 0)),
])
def test_parse_time(text, now, want):
    assert parse_time(text, now) == want


@pytest.mark.parametrize("text", ["", "whenever", "at 25:00"])
def test_not_understood(text):
    assert parse_time(text, FRI_1530) is None
