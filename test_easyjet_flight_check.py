"""Offline parsing tests. No network and no browser.

The US-English cards reproduce the saved Google Flights text from a US-located
run (ABZ -> LTN on 2027-03-23): 12-hour times, "Nonstop", prices in "$".
The UK-English cards are the same itineraries as rendered when the search URL
asks for GBP / en-GB: 24-hour times, "Non-stop", prices in "£".
"""

from __future__ import annotations

import datetime as dt
import unittest
from urllib.parse import parse_qs, urlparse

from easyjet_flight_check import (
    build_flights_url,
    collect_easyjet_options,
    format_price,
    has_non_gbp_price,
    parse_clock,
    parse_flight_times,
    parse_gbp_price,
    parse_listing,
    parse_stops,
)

DAY = dt.date(2027, 3, 23)

# Exact characters from the saved page: U+202F before AM/PM, NBSP around the en-dash.
US_MORNING = (
    "8:15\u202fAM\n"
    "\xa0–\xa0\n"
    "9:40\u202fAM\n"
    "easyJet\n"
    "1 hr 25 min\n"
    "ABZ–LTN\n"
    "Nonstop\n"
    "72 kg CO2e\n"
    "-9% emissions\n"
    "$63"
)
US_EVENING = (
    "8:25\u202fPM\n"
    "\xa0–\xa0\n"
    "9:50\u202fPM\n"
    "easyJet\n"
    "1 hr 25 min\n"
    "ABZ–LTN\n"
    "Nonstop\n"
    "72 kg CO2e\n"
    "-9% emissions\n"
    "$63"
)
# Same two flights if `curr=GBP` is honored but the page stays en-US.
US_MORNING_GBP = US_MORNING.replace("$63", "£63")
US_EVENING_GBP = US_EVENING.replace("$63", "£63")

UK_MORNING = (
    "08:15\n"
    "\xa0–\xa0\n"
    "09:40\n"
    "easyJet\n"
    "1 hr 25 min\n"
    "ABZ–LTN\n"
    "Non-stop\n"
    "72 kg CO2e\n"
    "-9% emissions\n"
    "£47"
)
# 20:25/21:50 is the 24-hour form of the saved 8:25 PM / 9:50 PM flight.
UK_EVENING = (
    "20:25\n"
    "\xa0–\xa0\n"
    "21:50\n"
    "easyJet\n"
    "1 hr 25 min\n"
    "ABZ–LTN\n"
    "Non-stop\n"
    "72 kg CO2e\n"
    "-9% emissions\n"
    "£47"
)


class ParseUsEnglishTests(unittest.TestCase):
    def test_morning_time_and_stops(self) -> None:
        self.assertEqual(parse_flight_times(US_MORNING), ("8:15 AM", "9:40 AM"))
        self.assertEqual(parse_stops(US_MORNING), "Nonstop")
        self.assertEqual(parse_clock("8:15\u202fAM"), dt.time(8, 15))
        self.assertEqual(parse_clock("9:40\u202fAM"), dt.time(9, 40))

    def test_evening_time(self) -> None:
        self.assertEqual(parse_flight_times(US_EVENING), ("8:25 PM", "9:50 PM"))
        self.assertEqual(parse_stops(US_EVENING), "Nonstop")
        self.assertEqual(parse_clock("8:25 PM"), dt.time(20, 25))
        self.assertEqual(parse_clock("9:50 PM"), dt.time(21, 50))

    def test_dollar_price_is_not_treated_as_pounds(self) -> None:
        self.assertIsNone(parse_gbp_price(US_MORNING))
        self.assertIsNone(parse_gbp_price(US_EVENING))
        self.assertIsNone(parse_gbp_price("$63"))
        self.assertIsNone(parse_gbp_price("$76"))
        self.assertTrue(has_non_gbp_price(US_MORNING))
        self.assertIsNone(parse_listing(US_MORNING, DAY))

    def test_dollar_cards_are_wrong_currency_not_zero_parsed(self) -> None:
        options, status = collect_easyjet_options([US_MORNING, US_EVENING], DAY)
        self.assertEqual(options, [])
        self.assertEqual(status, "wrong-currency")

    def test_us_layout_with_a_pound_price_parses(self) -> None:
        # curr=GBP can be honored while the rest of the page stays en-US.
        self.assertEqual(parse_flight_times(US_MORNING_GBP), ("8:15 AM", "9:40 AM"))
        self.assertEqual(parse_stops(US_MORNING_GBP), "Nonstop")
        self.assertEqual(parse_gbp_price(US_MORNING_GBP), 63)
        options, status = collect_easyjet_options([US_MORNING_GBP, US_EVENING_GBP], DAY)
        self.assertEqual(status, "ok")
        self.assertEqual(
            [(o.depart_time, o.arrive_time, o.stops, o.price_gbp) for o in options],
            [("8:15 AM", "9:40 AM", "Nonstop", 63), ("8:25 PM", "9:50 PM", "Nonstop", 63)],
        )
        self.assertEqual(format_price(options[0]), "£63")


class ParseUkEnglishTests(unittest.TestCase):
    def test_morning_time_stops_and_price(self) -> None:
        self.assertEqual(parse_flight_times(UK_MORNING), ("08:15", "09:40"))
        self.assertEqual(parse_stops(UK_MORNING), "Non-stop")
        self.assertEqual(parse_gbp_price(UK_MORNING), 47)
        self.assertFalse(has_non_gbp_price(UK_MORNING))
        self.assertEqual(parse_clock("08:15"), dt.time(8, 15))
        self.assertEqual(parse_clock("09:40"), dt.time(9, 40))

    def test_evening_24_hour_time(self) -> None:
        self.assertEqual(parse_flight_times(UK_EVENING), ("20:25", "21:50"))
        self.assertEqual(parse_stops(UK_EVENING), "Non-stop")
        self.assertEqual(parse_gbp_price(UK_EVENING), 47)
        self.assertEqual(parse_clock("20:25"), dt.time(20, 25))
        self.assertEqual(parse_clock("21:50"), dt.time(21, 50))

    def test_listing_keeps_the_pound_amount(self) -> None:
        option = parse_listing(UK_MORNING, DAY)
        self.assertIsNotNone(option)
        assert option is not None
        self.assertEqual(option.depart_time, "08:15")
        self.assertEqual(option.arrive_time, "09:40")
        self.assertEqual(option.stops, "Non-stop")
        self.assertEqual(option.duration, "1 hr 25 min")
        self.assertEqual(option.price_gbp, 47)
        self.assertEqual(format_price(option), "£47")

    def test_both_uk_flights_parse(self) -> None:
        options, status = collect_easyjet_options([UK_MORNING, UK_EVENING], DAY)
        self.assertEqual(status, "ok")
        self.assertEqual([o.depart_time for o in options], ["08:15", "20:25"])
        self.assertTrue(all(o.price_gbp == 47 and o.stops == "Non-stop" for o in options))

    def test_inline_24_hour_and_12_hour_separators(self) -> None:
        inline_uk = "08:15 – 09:40\neasyJet\nNon-stop\n£47"
        inline_us = "8:15 AM - 9:40 AM\neasyJet\nNonstop\n£63"
        self.assertEqual(parse_flight_times(inline_uk), ("08:15", "09:40"))
        self.assertEqual(parse_stops(inline_uk), "Non-stop")
        self.assertEqual(parse_gbp_price(inline_uk), 47)
        self.assertEqual(parse_flight_times(inline_us), ("8:15 AM", "9:40 AM"))
        self.assertEqual(parse_stops(inline_us), "Nonstop")
        self.assertEqual(parse_gbp_price(inline_us), 63)


class ParseSharedTests(unittest.TestCase):
    def test_clocks_sort_across_both_formats(self) -> None:
        # search_leg sorts on parse_clock, which has to order 24-hour times
        # as well as the 12-hour times "%I:%M %p" used to assume.
        self.assertEqual(parse_clock("8:15 AM"), parse_clock("08:15"))
        self.assertEqual(parse_clock("8:25 PM"), parse_clock("20:25"))
        self.assertEqual(parse_clock("7:05 AM"), parse_clock("07:05"))
        self.assertLess(parse_clock("08:15"), parse_clock("8:25 PM"))
        self.assertLess(parse_clock("9:40 AM"), parse_clock("21:50"))
        ordered = sorted(["9:50 PM", "08:15", "7:05 AM", "20:25"], key=parse_clock)
        self.assertEqual(ordered, ["7:05 AM", "08:15", "20:25", "9:50 PM"])

    def test_numbered_stops_still_parse(self) -> None:
        self.assertEqual(parse_stops("easyJet\n1 stop\n£100"), "1 stop")
        self.assertEqual(parse_stops("easyJet\n2 stops\n£100"), "2 stops")

    def test_comma_separated_pound_price(self) -> None:
        self.assertEqual(parse_gbp_price("£1,234"), 1234)
        self.assertEqual(parse_gbp_price("GBP 47"), 47)

    def test_other_currency_is_not_converted(self) -> None:
        euro = "08:15\n\xa0–\xa0\n09:40\neasyJet\nNon-stop\n€80"
        self.assertIsNone(parse_gbp_price(euro))
        self.assertTrue(has_non_gbp_price(euro))
        options, status = collect_easyjet_options([euro], DAY)
        self.assertEqual(options, [])
        self.assertEqual(status, "wrong-currency")

    def test_easyjet_without_a_price_stays_zero_parsed(self) -> None:
        text = "08:15\n\xa0–\xa0\n09:40\neasyJet\nNon-stop\nprice unavailable"
        options, status = collect_easyjet_options([text], DAY)
        self.assertEqual(options, [])
        self.assertEqual(status, "zero-parsed")

    def test_search_url_requests_gbp(self) -> None:
        url = build_flights_url("ABZ", "LTN", DAY)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["curr"], ["GBP"])
        self.assertEqual(query["hl"], ["en-GB"])
        self.assertEqual(query["gl"], ["GB"])
        self.assertIn("ABZ", query["q"][0])
        self.assertIn("LTN", query["q"][0])
        self.assertIn("2027-03-23", query["q"][0])


if __name__ == "__main__":
    unittest.main()
