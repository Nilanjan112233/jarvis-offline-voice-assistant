"""Turn the model's time text ("7 pm", "Friday at 10am", "in 10 minutes") into a datetime."""
import re
from datetime import datetime, timedelta

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10,
            "fifteen": 15, "twenty": 20, "thirty": 30, "half an": 0.5, "half": 0.5}


def parse_time(text, now=None):
    """Return a datetime, or None if not understood."""
    now = now or datetime.now()
    t = (text or "").lower().strip()
    if not t:
        return None

    # relative: "in 10 minutes", "in an hour", "after 2 hours", "10 min"
    m = re.search(r"(?:in|after)?\s*(\d+(?:\.\d+)?|a|an|one|two|three|four|five|ten|fifteen|twenty|thirty|half an|half)\s*"
                  r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)\b", t)
    if m and not re.search(r"\d\s*(am|pm|:)", t):
        n = m.group(1)
        n = float(n) if n[0].isdigit() else WORD_NUM[n]
        unit = m.group(2)
        delta = timedelta(seconds=n) if unit.startswith("s") else timedelta(minutes=n) if unit.startswith("m") else timedelta(hours=n) if unit.startswith("h") else timedelta(days=n)
        return now + delta

    day = now.date()
    if "tomorrow" in t:
        day = day + timedelta(days=1)
    weekday_named = False
    for i, d in enumerate(DAYS):
        if d in t or re.search(rf"\b{d[:3]}\b", t):
            # "Friday 10am" said on Friday at 9 = today; if that time has passed, next week (handled below)
            day = now.date() + timedelta(days=(i - now.weekday()) % 7)
            weekday_named = True
            break

    hour = minute = None
    m = re.search(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = (m.group(3) or "").replace(".", "")
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour > 23 or minute > 59:
            return None
    elif "noon" in t:
        hour, minute = 12, 0
    elif "midnight" in t:
        hour, minute = 0, 0
    elif "morning" in t:
        hour, minute = 9, 0
    elif "evening" in t:
        hour, minute = 18, 0
    elif "night" in t:
        hour, minute = 21, 0
    elif day != now.date() or weekday_named:
        hour, minute = 9, 0
    else:
        return None

    when = datetime(day.year, day.month, day.day, hour, minute)
    if when <= now and day == now.date():
        # "7" said at 8 pm probably means 7 pm or tomorrow 7 am; pick the next future match
        if m and not m.group(3) and hour < 12 and when + timedelta(hours=12) > now:
            when += timedelta(hours=12)
        else:
            when += timedelta(days=7 if weekday_named else 1)
    return when
