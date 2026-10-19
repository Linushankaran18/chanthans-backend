"""Minimal RFC 5545 builder for a single shared event (floating studio-local times + TZID)."""
from datetime import datetime, timezone

from app.models.booking import Booking, BookingStatus


def _escape(value: str) -> str:
    for char, escaped in (("\\", "\\\\"), (";", "\\;"), (",", "\\,"), ("\r\n", "\\n"), ("\n", "\\n")):
        value = value.replace(char, escaped)
    return value


def _fold(line: str) -> str:
    """Lines are limited to 75 octets; continuation lines start with a space."""
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for ch in line:
        enc = ch.encode()
        limit = 75 if not parts else 74
        if len(current) + len(enc) > limit:
            parts.append(current)
            current = b""
        current += enc
    parts.append(current)
    return "\r\n ".join(p.decode() for p in parts)


def build_ics(booking: Booking, timezone_name: str) -> str:
    start = datetime.combine(booking.event_date, booking.start_time).strftime("%Y%m%dT%H%M%S")
    end = datetime.combine(booking.event_date, booking.end_time).strftime("%Y%m%dT%H%M%S")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Chanthans//Event share//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:{booking.id}@chanthans",
        f"DTSTAMP:{stamp}",
        f"DTSTART;TZID={timezone_name}:{start}",
        f"DTEND;TZID={timezone_name}:{end}",
        f"SUMMARY:{_escape(f'{booking.service_type} — {booking.customer_name}')}",
    ]
    if booking.location:
        lines.append(f"LOCATION:{_escape(booking.location)}")
    if booking.status == BookingStatus.CANCELLED.value:
        lines.append("STATUS:CANCELLED")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
