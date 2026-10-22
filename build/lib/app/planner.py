"""Time resolution: free-form "when" -> explicit hours in IST.

The SLM reads the *language* of time ("tomorrow evening", "this weekend",
"around 2pm"); this module does the *calendar arithmetic* deterministically.
That split keeps the model out of anything that can be checked, and it makes
every time claim in the response traceable to a timestamp we computed.

All times are handled in Asia/Kolkata (IST, UTC+05:30).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")

# name -> (first_hour, last_hour) inclusive
PART_OF_DAY: dict[str, tuple[int, int]] = {
    "early_morning": (4, 6),
    "morning": (6, 11),
    "afternoon": (12, 16),
    "evening": (17, 20),
    "night": (21, 23),
    "late_night": (22, 23),
}

WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_DAY_WORDS = "|".join(WEEKDAYS)


@dataclass
class TimeWindow:
    start: datetime
    end: datetime
    hours: list[datetime] = field(default_factory=list)
    label: str = ""
    assumptions: list[str] = field(default_factory=list)

    @property
    def dates(self) -> list[str]:
        return sorted({h.date().isoformat() for h in self.hours})


def now_ist() -> datetime:
    return datetime.now(TZ)


def _midnight(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_clock(text: str) -> int | None:
    """'2pm', '2 pm', 'at 14:30', 'around 5' -> hour (24h).

    Numbers that are clearly not clock times ('15 km', '5th phase', '2 days')
    are ignored.
    """
    for m in re.finditer(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text, re.IGNORECASE):
        tail = text[m.end() : m.end() + 12].lower()
        if re.match(r"\s*(st|nd|rd|th)\b", tail):
            continue  # ordinal, e.g. "5th phase"
        if re.match(r"\s*(km|kilometre|kilometer|min|mins|minute|minutes|hr|hrs|hour|hours|day|days|week|weeks|month|months)\b", tail):
            continue  # a distance or a duration
        head = text[max(0, m.start() - 6) : m.start()].lower()
        if re.search(r"(within|about|around|roughly|near|of|up to)\s+$", head):
            continue  # "within 15 km", "about 5 minutes"
        hour = int(m.group(1))
        meridiem = (m.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23):
            continue
        return hour
    return None


def _next_weekday(base: datetime, target: int, allow_today: bool) -> datetime:
    day = _midnight(base)
    delta = (target - day.weekday()) % 7
    if delta == 0 and not allow_today:
        delta = 7
    return day + timedelta(days=delta)


def _weekend_days(base: datetime) -> list[datetime]:
    """Saturday + Sunday of the upcoming weekend (today counts if it is one)."""
    day = _midnight(base)
    sat = day + timedelta(days=(5 - day.weekday()) % 7)
    if sat == day and day.weekday() == 5:
        return [day, day + timedelta(days=1)]
    if day.weekday() == 6:  # Sunday: today only
        return [day]
    return [sat, sat + timedelta(days=1)]


def resolve(
    time_text: str = "",
    day: str | None = None,
    part_of_day: str | None = None,
    hour: int | None = None,
    days_ahead: int = 1,
    now: datetime | None = None,
    raw_text: str = "",
) -> TimeWindow:
    """Turn a vague or explicit time expression into concrete hours.

    ``raw_text`` is the user's own question. It wins over the model-parsed
    fields, because the question is the ground truth for the wording.
    """
    now = now or now_ist()
    raw = (raw_text or "").lower()
    assumptions: list[str] = []

    # ------------------------------------------------------ number of days
    span = max(1, min(int(days_ahead or 1), 7))
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    }
    m = re.search(
        r"\b(?:next|in|over|for)\s+(?:(\d{1,2})|(" + "|".join(words) + r"))\s+days?\b",
        raw,
    )
    span_explicit = False
    if m:
        span = int(m.group(1)) if m.group(1) else words[m.group(2)]
        span = max(1, min(span, 7))
        day_label_hint = f"the next {span} days"
        span_explicit = True
    else:
        day_label_hint = ""

    # --------------------------------------------------------- which days?
    raw_day = None
    if re.search(r"\bnext weekend\b", raw):
        raw_day = "next_weekend"
    elif re.search(r"\bweekend\b", raw):
        raw_day = "weekend"
    elif re.search(r"\bday after tomorrow\b|\bovermorrow\b", raw):
        raw_day = "day_after"
    elif re.search(r"\btomorrow\b", raw):
        raw_day = "tomorrow"
    elif re.search(r"\btoday\b|\bright now\b|\btonight\b|\bthis (?:evening|morning|afternoon)\b", raw):
        raw_day = "today"
    else:
        wd = re.search(rf"\b({_DAY_WORDS})\b", raw)
        if wd:
            raw_day = wd.group(1)

    chosen_day = raw_day or (day if day and day != "none" else None)

    # "in the next two days" is an explicit span. The model sometimes invents
    # a named day for it, and the user's own words win.
    if span_explicit and raw_day is None:
        chosen_day = None

    target_days: list[datetime] = []
    day_label = ""
    day_is_fixed = True

    if chosen_day in {"weekend", "next_weekend"}:
        target_days = _weekend_days(now)
        day_label = "the weekend"
        if chosen_day == "next_weekend":
            target_days = [d + timedelta(days=7) for d in target_days]
            day_label = "next weekend"
    elif chosen_day == "today":
        target_days = [_midnight(now)]
        day_label = "today"
    elif chosen_day == "tomorrow":
        target_days = [_midnight(now) + timedelta(days=1)]
        day_label = "tomorrow"
    elif chosen_day == "day_after":
        target_days = [_midnight(now) + timedelta(days=2)]
        day_label = "the day after tomorrow"
    elif chosen_day in WEEKDAYS:
        target_days = [_next_weekday(now, WEEKDAYS[chosen_day], allow_today=True)]
        day_label = chosen_day
    else:
        # no day named: today, then the following days
        target_days = [_midnight(now) + timedelta(days=i) for i in range(span)]
        day_label = "today" if span == 1 else (day_label_hint or f"the next {span} days")
        day_is_fixed = False
        if span > 1:
            assumptions.append(f"Interpreted the request as the next {span} days.")

    if not target_days:
        target_days = [_midnight(now)]
        day_is_fixed = False

    # -------------------------------------------------------- which hours?
    horizon = now - timedelta(minutes=30)
    hours: list[datetime] = []
    slot_label = ""
    hour_specified = False

    raw_pod = None
    if re.search(r"\bearly morning\b|\bdawn\b", raw):
        raw_pod = "early_morning"
    elif re.search(r"\bmorning\b", raw):
        raw_pod = "morning"
    elif re.search(r"\bafternoon\b|\bmidday\b|\blunch time\b", raw):
        raw_pod = "afternoon"
    elif re.search(r"\bevening\b|\bsunset\b|\bgolden hour\b|\bafter work\b", raw):
        raw_pod = "evening"
    elif re.search(r"\bnight\b|\btonight\b|\blate\b", raw):
        raw_pod = "night"

    chosen_pod = raw_pod or (part_of_day if part_of_day and part_of_day != "none" else None)

    clock_hour = _parse_clock(raw) if raw else None
    if clock_hour is None and not raw:
        clock_hour = hour

    if clock_hour is not None:
        hour_specified = True
        slot_label = f"{clock_hour:02d}:00"
        hours = [d.replace(hour=clock_hour) for d in target_days]
    elif chosen_pod in PART_OF_DAY:
        start_h, end_h = PART_OF_DAY[chosen_pod]
        slot_label = chosen_pod.replace("_", " ")
        # Each requested day contributes one occurrence of the part of day.
        # If that day's slot has already passed, it rolls forward to the next
        # day, so "in the next two days" gives two usable mornings.
        occ_days: list[datetime] = []
        cursor = target_days[0]
        guard = 0
        while len(occ_days) < len(target_days) and guard < 14:
            guard += 1
            occ = cursor
            if all(occ.replace(hour=h) < horizon for h in range(start_h, end_h + 1)):
                occ = cursor + timedelta(days=1)
            if occ not in occ_days:
                occ_days.append(occ)
            cursor += timedelta(days=1)
        for d in occ_days:
            for h in range(start_h, end_h + 1):
                hours.append(d.replace(hour=h))
    elif chosen_day in {"weekend", "next_weekend"}:
        start_h, end_h = 6, 20
        slot_label = "all day"
        for d in target_days:
            for h in range(start_h, end_h + 1):
                hours.append(d.replace(hour=h))
    elif day_is_fixed:
        # a specific day was named but no time of day: cover its daylight
        start_h, end_h = 6, 20
        slot_label = "daylight"
        for d in target_days:
            for h in range(start_h, end_h + 1):
                hours.append(d.replace(hour=h))
    else:
        # nothing specified: the rest of today, then the following days
        start_h, end_h = max(now.hour, 5), 21
        slot_label = "remaining daylight"
        hours = [
            now.replace(hour=h, minute=0, second=0, microsecond=0)
            for h in range(max(now.hour, 5), 22)
        ]
        hours += [d.replace(hour=h) for d in target_days[1:] for h in range(6, 21)]
        assumptions.append(
            "No specific time was given, so the remaining daylight today and the "
            "following days were considered."
        )

    # ------------------------------------------------- drop past hours
    future_hours = [h for h in hours if h >= horizon]
    if not future_hours and hours:
        if hour_specified or chosen_pod in PART_OF_DAY:
            # the slot has passed today: offer the same slot tomorrow
            rolled = [h + timedelta(days=1) for h in hours]
            hours = rolled
            future_hours = [h for h in rolled if h >= horizon] or rolled
            assumptions.append(
                f"{slot_label} today has already passed, so the same time tomorrow "
                "is offered."
            )
        else:
            future_hours = hours[-1:]
            assumptions.append(
                "The requested time has already passed, so the latest available slot "
                "is shown."
            )
    hours = sorted(set(future_hours))

    if not hours:
        hours = [now.replace(minute=0, second=0, microsecond=0)]

    start = hours[0]
    end = hours[-1] + timedelta(hours=1)
    label = f"{day_label} {slot_label}".strip()

    return TimeWindow(
        start=start,
        end=end,
        hours=hours,
        label=label,
        assumptions=assumptions,
    )
