#!/usr/bin/env python3
"""Send a phone alert the evening before Basingstoke and Deane bins are collected.

Uses only the Python standard library. It connects to two places:
  1. basingstoke.gov.uk, sending your UPRN in a cookie, to read your dates
  2. ntfy.sh, sending only the bin types and dates, to alert your phone

Settings come from environment variables:
  BINS_UPRN    your property's UPRN
  NTFY_TOPIC   your long, random ntfy topic name
  NTFY_SERVER  optional; defaults to https://ntfy.sh

Run with --test to print the dates found and send a test alert.
"""

import http.client
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

COUNCIL_URL = "https://www.basingstoke.gov.uk/bincollections"
DEFAULT_NTFY_SERVER = "https://ntfy.sh"
USER_AGENT = "Mozilla/5.0 (compatible; personal-bin-reminder/1.0)"
LONDON = ZoneInfo("Europe/London")
NETWORK_ERRORS = (OSError, http.client.HTTPException)  # includes URLError and timeouts

# If nothing is due this far ahead, assume the page has changed. Three weeks
# leaves room for fortnightly rounds and Christmas changes.
MAX_DAYS_TO_NEXT_COLLECTION = 21

# The page's section names, shown here in the order used in alerts. Any new
# section the council adds is still picked up, using its own name.
LABELS = {
    "Refuse": "Rubbish",
    "Recycling": "Recycling",
    "Glass": "Glass",
    "Food": "Food waste",
    "GardenWaste": "Garden waste",
}
SECTION_ID = re.compile(r"pnlCollections_(\w+)$")
DATE_TEXT = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})\b")


class BinError(Exception):
    """A problem worth telling you about. Messages must not contain the UPRN."""


@dataclass(frozen=True)
class Collection:
    bin_type: str
    when: date


class _SectionParser(HTMLParser):
    """Collect the text of each list item inside the collection sections."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []
        self._section = None
        self._div_depth = 0
        self._in_item = False
        self._text = []

    def handle_starttag(self, tag, attrs):
        if self._section is None:
            if tag == "div":
                match = SECTION_ID.search(dict(attrs).get("id") or "")
                if match:
                    self._section = match.group(1)
                    self._div_depth = 1
            return
        if tag == "div":
            self._div_depth += 1
        elif tag == "li":
            self._finish_item()
            self._in_item = True

    def handle_endtag(self, tag):
        if self._section is None:
            return
        if tag == "li":
            self._finish_item()
        elif tag == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self._finish_item()
                self._section = None

    def handle_data(self, data):
        if self._in_item:
            self._text.append(data)

    def _finish_item(self):
        if self._in_item:
            self.items.append((self._section, " ".join(self._text)))
        self._in_item = False
        self._text = []


def _parse_date(text):
    match = DATE_TEXT.search(text)
    if not match:
        return None
    value = " ".join(match.groups())
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def parse_collections(html):
    """Return every dated collection on the page, without duplicates."""
    parser = _SectionParser()
    parser.feed(html)
    parser.close()

    found = []
    for section, text in parser.items:
        when = _parse_date(text)
        if when:
            collection = Collection(LABELS.get(section, section), when)
            if collection not in found:
                found.append(collection)
    return found


def _with_retries(action):
    delays = (5, 30)
    for delay in delays:
        try:
            return action()
        except NETWORK_ERRORS:
            time.sleep(delay)
    return action()


def fetch_page(uprn):
    request = urllib.request.Request(
        COUNCIL_URL,
        headers={
            "Accept": "text/html",
            "Cookie": f"cookie_control_popup=A; WhenAreMyBinsCollected={uprn}",
            "Referer": "https://www.basingstoke.gov.uk/",
            "User-Agent": USER_AGENT,
        },
    )

    def read():
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")

    try:
        return _with_retries(read)
    except urllib.error.HTTPError as error:
        raise BinError(f"The council site returned HTTP {error.code}.") from None
    except NETWORK_ERRORS as error:
        reason = type(getattr(error, "reason", error)).__name__
        raise BinError(f"Could not reach the council site ({reason}).") from None


def send_alert(topic, title, message, tags="wastebasket", priority="default"):
    server = os.environ.get("NTFY_SERVER") or DEFAULT_NTFY_SERVER
    url = f"{server.rstrip('/')}/{urllib.parse.quote(topic, safe='')}"
    request = urllib.request.Request(
        url,
        data=message.encode("utf-8"),
        method="POST",
        headers={"Title": title, "Tags": tags, "Priority": priority},
    )

    def post():
        with urllib.request.urlopen(request, timeout=30):
            return None

    _with_retries(post)


def _join(names):
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def _sort_key(name):
    order = list(LABELS.values())
    return (order.index(name) if name in order else len(order), name)


def bins_on(collections, day):
    return sorted({c.bin_type for c in collections if c.when == day}, key=_sort_key)


def _pretty(day):
    return f"{day:%a} {day.day} {day:%b}"


def check_upcoming(collections, today):
    """Raise if the dates suggest the page has changed; otherwise return them."""
    if not collections:
        raise BinError("No collection dates were found on the council page.")
    upcoming = [c for c in collections if c.when >= today]
    if not upcoming:
        raise BinError("The council page only showed past collection dates.")
    next_date = min(c.when for c in upcoming)
    if (next_date - today).days > MAX_DAYS_TO_NEXT_COLLECTION:
        raise BinError(f"The next date found is unusually far away ({_pretty(next_date)}).")
    return upcoming


def run(uprn, topic, test=False, today=None):
    today = today or datetime.now(LONDON).date()
    upcoming = check_upcoming(parse_collections(fetch_page(uprn)), today)

    if test:
        for collection in sorted(upcoming, key=lambda c: (c.when, _sort_key(c.bin_type))):
            print(f"{_pretty(collection.when)}: {collection.bin_type}")
        next_date = min(c.when for c in upcoming)
        send_alert(
            topic,
            "Bin alert test",
            f"Alerts are working. Next: {_join(bins_on(upcoming, next_date))} on {_pretty(next_date)}.",
        )
        print("Test alert sent.")
        return

    due = bins_on(upcoming, today + timedelta(days=1))
    if due:
        send_alert(topic, "Bins tomorrow", _join(due))
        print("Alert sent:", _join(due))
    else:
        print("Nothing due tomorrow.")


def main():
    uprn = os.environ.get("BINS_UPRN", "").strip()
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC is not set.", file=sys.stderr)
        return 2

    try:
        if not re.fullmatch(r"\d{1,12}", uprn):
            raise BinError("The BINS_UPRN setting should be a number of up to 12 digits.")
        run(uprn, topic, test="--test" in sys.argv[1:])
    except BinError as error:
        print(f"Problem: {error}", file=sys.stderr)
        try:
            send_alert(
                topic,
                "Bin alert problem",
                f"{error} Check your collection day on the council site.",
                tags="warning",
                priority="high",
            )
        except NETWORK_ERRORS as alert_error:
            print(f"Could not send the alert: {type(alert_error).__name__}", file=sys.stderr)
        return 1
    except NETWORK_ERRORS as error:
        print(f"Could not send the alert: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
