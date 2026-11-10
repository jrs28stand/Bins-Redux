"""Run with: python3 -m unittest -v test_bins

The sample page copies the section IDs the open-source Basingstoke scraper
expects. It is not a capture of the real page.
"""

import os
import threading
import unittest
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import bins

UPRN = "100060000001"


def section(name, *items):
    lis = "".join(f"<li>{item}</li>" for item in items)
    return (
        f'<div id="rteelem_ctl03_pnlCollections_{name}"><h3>{name}</h3>'
        f"<div class='inner'><ul>{lis}</ul></div></div>"
    )


PAGE = (
    "<html><body><ul><li>Home</li><li>9 November 2026 outside any section</li></ul>"
    + section("Refuse", "Friday, 6 November 2026", "Friday, 20 November 2026")
    + section("Recycling", "Friday, 13 November 2026")
    + section("Food", "Thursday, 5 November 2026", "Friday, 6 November 2026",
              "Friday, 6 November 2026")
    + section("WeeklyTextiles", "<strong>Friday 6th Nov 2026</strong>")
    + section("Glass", "Date to be confirmed")
    + "</body></html>"
)


class Parsing(unittest.TestCase):
    def setUp(self):
        self.found = bins.parse_collections(PAGE)

    def test_only_reads_dates_inside_sections(self):
        self.assertNotIn(date(2026, 11, 9), {c.when for c in self.found})

    def test_nested_markup_and_new_section_names(self):
        self.assertIn(bins.Collection("WeeklyTextiles", date(2026, 11, 6)), self.found)

    def test_duplicates_removed(self):
        matches = [c for c in self.found if c == bins.Collection("Food waste", date(2026, 11, 6))]
        self.assertEqual(len(matches), 1)

    def test_bins_on_a_day_are_named_and_ordered(self):
        self.assertEqual(
            bins.bins_on(self.found, date(2026, 11, 6)),
            ["Rubbish", "Food waste", "WeeklyTextiles"],
        )

    def test_unparseable_item_ignored(self):
        self.assertFalse([c for c in self.found if c.bin_type == "Glass"])

    def test_invalid_date_ignored(self):
        self.assertIsNone(bins._parse_date("31 February 2026"))


class SanityChecks(unittest.TestCase):
    today = date(2026, 11, 3)

    def test_no_dates(self):
        with self.assertRaisesRegex(bins.BinError, "No collection dates"):
            bins.check_upcoming([], self.today)

    def test_only_past_dates(self):
        with self.assertRaisesRegex(bins.BinError, "past"):
            bins.check_upcoming([bins.Collection("Food waste", date(2026, 11, 1))], self.today)

    def test_next_date_too_far_away(self):
        with self.assertRaisesRegex(bins.BinError, "far away"):
            bins.check_upcoming([bins.Collection("Food waste", date(2026, 12, 1))], self.today)

    def test_three_weeks_is_allowed(self):
        collection = bins.Collection("Food waste", date(2026, 11, 24))
        self.assertEqual(bins.check_upcoming([collection], self.today), [collection])


class LocalServer(BaseHTTPRequestHandler):
    """Plays both the council site and ntfy, recording everything it receives."""

    page = PAGE
    status = 200
    requests = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        LocalServer.requests.append(("GET", self.path, dict(self.headers), b""))
        self.send_response(LocalServer.status)
        self.end_headers()
        self.wfile.write(LocalServer.page.encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        LocalServer.requests.append(("POST", self.path, dict(self.headers), body))
        self.send_response(200)
        self.end_headers()


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), LocalServer)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        LocalServer.requests = []
        LocalServer.page = PAGE
        LocalServer.status = 200
        self.patches = [
            mock.patch.object(bins, "COUNCIL_URL", f"{self.base}/bincollections"),
            mock.patch.object(bins.time, "sleep"),
            mock.patch.dict(
                os.environ,
                {
                    "NTFY_SERVER": self.base,
                    "BINS_UPRN": UPRN,
                    "NTFY_TOPIC": "my-topic",
                    "no_proxy": "*",
                    "NO_PROXY": "*",
                },
            ),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def alerts(self):
        return [r for r in LocalServer.requests if r[0] == "POST"]

    def assert_uprn_never_sent_to_ntfy(self):
        for _, path, headers, body in self.alerts():
            self.assertNotIn(UPRN, path + str(headers) + body.decode())

    def test_sends_uprn_to_council_as_cookie_only(self):
        bins.run(UPRN, "my-topic", now=datetime(2026, 11, 5, 19, 0))
        get = [r for r in LocalServer.requests if r[0] == "GET"][0]
        self.assertIn(f"WhenAreMyBinsCollected={UPRN}", get[2]["Cookie"])
        self.assertNotIn(UPRN, get[1])

    def test_alert_when_bins_due_tomorrow(self):
        bins.run(UPRN, "my-topic", now=datetime(2026, 11, 5, 19, 0))
        (_, path, headers, body), = self.alerts()
        self.assertEqual(path, "/my-topic")
        self.assertEqual(headers["Title"], "Bins tomorrow")
        self.assertEqual(
            body.decode(),
            "Rubbish, Food waste and WeeklyTextiles\nThen: Recycling on Fri 13 Nov",
        )
        self.assert_uprn_never_sent_to_ntfy()

    def test_no_then_line_when_nothing_later_is_listed(self):
        LocalServer.page = section("Refuse", "Friday, 6 November 2026")
        bins.run(UPRN, "my-topic", now=datetime(2026, 11, 5, 19, 0))
        (_, _, _, body), = self.alerts()
        self.assertEqual(body.decode(), "Rubbish")

    def test_then_line_skips_collection_being_alerted(self):
        bins.run(UPRN, "my-topic", now=datetime(2026, 11, 4, 19, 0))
        (_, _, _, body), = self.alerts()
        self.assertEqual(body.decode(), "Food waste\nThen: Rubbish, Food waste and WeeklyTextiles on Fri 6 Nov")

    def test_silent_when_nothing_due(self):
        bins.run(UPRN, "my-topic", now=datetime(2026, 11, 3, 19, 0))
        self.assertEqual(self.alerts(), [])

    def assert_alert_at(self, now, title):
        bins.run(UPRN, "my-topic", now=now)
        (_, _, headers, body), = self.alerts()
        self.assertEqual(headers["Title"], title)
        self.assertEqual(body.decode().split("\n")[0], "Rubbish, Food waste and WeeklyTextiles")

    def test_morning_alert_on_the_day_before(self):
        self.assert_alert_at(datetime(2026, 11, 5, 7, 38), "Bins tomorrow")

    def test_morning_run_delayed_six_hours_still_means_tomorrow(self):
        self.assert_alert_at(datetime(2026, 11, 5, 13, 38), "Bins tomorrow")

    def test_nothing_sent_on_collection_morning(self):
        bins.run(UPRN, "my-topic", now=datetime(2026, 11, 6, 7, 38))
        self.assertEqual(self.alerts(), [])

    def test_late_evening_run_before_midnight_still_means_tomorrow(self):
        self.assert_alert_at(datetime(2026, 11, 5, 23, 41), "Bins tomorrow")

    def test_evening_run_delayed_past_midnight_means_today(self):
        self.assert_alert_at(datetime(2026, 11, 6, 0, 30), "Bins today")

    def test_silent_in_morning_when_nothing_due_tomorrow(self):
        bins.run(UPRN, "my-topic", now=datetime(2026, 11, 3, 7, 38))
        self.assertEqual(self.alerts(), [])

    def test_test_mode_always_alerts(self):
        bins.run(UPRN, "my-topic", test=True, now=datetime(2026, 11, 3, 19, 0))
        (_, _, headers, body), = self.alerts()
        self.assertEqual(headers["Title"], "Bin alert test")
        self.assertIn("Food waste on Thu 5 Nov", body.decode())

    def test_changed_page_triggers_problem_alert(self):
        LocalServer.page = "<html><body>Redesigned!</body></html>"
        with mock.patch.object(bins.sys, "argv", ["bins.py"]):
            self.assertEqual(bins.main(), 1)
        (_, _, headers, body), = self.alerts()
        self.assertEqual(headers["Title"], "Bin alert problem")
        self.assertIn("No collection dates", body.decode())
        self.assert_uprn_never_sent_to_ntfy()

    def test_blocked_site_triggers_problem_alert(self):
        LocalServer.status = 403
        with mock.patch.object(bins.sys, "argv", ["bins.py"]):
            self.assertEqual(bins.main(), 1)
        (_, _, _, body), = self.alerts()
        self.assertIn("HTTP 403", body.decode())
        self.assert_uprn_never_sent_to_ntfy()

    def test_dropped_connection_triggers_problem_alert(self):
        error = bins.http.client.RemoteDisconnected("closed")
        with mock.patch.object(bins, "fetch_page", side_effect=bins.BinError("Could not reach the council site (RemoteDisconnected).")):
            with mock.patch.object(bins.sys, "argv", ["bins.py"]):
                self.assertEqual(bins.main(), 1)
        self.assertEqual(len(self.alerts()), 1)
        real = mock.Mock(side_effect=error)
        with mock.patch.object(bins.urllib.request, "urlopen", real):
            with self.assertRaisesRegex(bins.BinError, "RemoteDisconnected"):
                bins.fetch_page(UPRN)

    def test_bad_uprn_setting_triggers_problem_alert_without_echoing_it(self):
        with mock.patch.dict(os.environ, {"BINS_UPRN": "12 High Street"}):
            with mock.patch.object(bins.sys, "argv", ["bins.py"]):
                self.assertEqual(bins.main(), 1)
        (_, _, _, body), = self.alerts()
        self.assertNotIn("High Street", body.decode())


if __name__ == "__main__":
    unittest.main()
