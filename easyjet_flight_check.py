"""
Check easyJet flights between Aberdeen (ABZ) and Luton (LTN) around
University of Aberdeen term dates, and report 3 candidate-day options
per leg (email or local HTML report).

Data source note
-----------------
easyjet.com itself blocks automated browser requests to its flight-search
endpoint (confirmed "Access Denied" from their Akamai bot protection, both
headless and headed). This script instead reads live easyJet-operated fares
from Google Flights, which does not block this kind of automated, low-volume,
personal-use access. Every result is filtered to airline == "easyJet".

Term dates source: University of Aberdeen academic calendar
https://www.abdn.ac.uk/students/academic-life/semester-dates/academic-calendar/

Usage
-----
    python easyjet_flight_check.py --leg-set winter
    python easyjet_flight_check.py --leg-set spring
    python easyjet_flight_check.py --leg-set winter --email

Configuration is read from a local .env file (see .env.example), or from
real environment variables of the same names:
    RECIPIENTS           comma-separated email addresses to send the report to
    GMAIL_ADDRESS        the sending Gmail address (only needed for --email)
    GMAIL_APP_PASSWORD   a 16-character Google App Password (only needed for --email)
Without --email, or without the Gmail variables set, results are written to a
local HTML report only and nothing is sent. .env is gitignored - it is never
committed, so personal details never reach the (public) repo.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import smtplib
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

AIRLINE = "easyJet"


def load_dotenv(path: Path = Path(__file__).with_name(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv()

RECIPIENTS = [addr.strip() for addr in os.environ.get("RECIPIENTS", "").split(",") if addr.strip()]

FLIGHT_TIME_RE = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)\s*[–-]\s*\n?\s*(\d{1,2}:\d{2}\s*[AP]M)")
PRICE_RE = re.compile(r"£\s*([\d,]+)")
DURATION_RE = re.compile(r"(\d+\s*hr(?:\s*\d+\s*min)?|\d+\s*min)")
STOPS_RE = re.compile(r"(Nonstop|\d+\s*stop[s]?)")


@dataclass
class Leg:
    label: str
    origin: str
    destination: str
    anchor_date: dt.date
    anchor_description: str
    direction: str  # "after" the anchor date, or "before" it
    window_days: int = 7
    num_options: int = 3


LEG_SETS: dict[str, list[Leg]] = {
    "winter": [
        Leg(
            label="Outbound: Aberdeen (ABZ) -> Luton (LTN), shortly after Term 1 close",
            origin="ABZ",
            destination="LTN",
            anchor_date=dt.date(2026, 12, 18),
            anchor_description="Term 1 close (Fri 18 Dec 2026)",
            direction="after",
        ),
        Leg(
            label="Return: Luton (LTN) -> Aberdeen (ABZ), shortly before Term 2 open",
            origin="LTN",
            destination="ABZ",
            anchor_date=dt.date(2027, 1, 18),
            anchor_description="Term 2 open (Mon 18 Jan 2027)",
            direction="before",
        ),
    ],
    "spring": [
        Leg(
            label="Outbound: Aberdeen (ABZ) -> Luton (LTN), shortly after Spring Break starts",
            origin="ABZ",
            destination="LTN",
            anchor_date=dt.date(2027, 3, 22),
            anchor_description="Spring Break start (Mon 22 Mar 2027)",
            direction="after",
        ),
        Leg(
            label="Return: Luton (LTN) -> Aberdeen (ABZ), shortly before Spring Break ends",
            origin="LTN",
            destination="ABZ",
            anchor_date=dt.date(2027, 4, 9),
            anchor_description="Spring Break end (Fri 9 Apr 2027)",
            direction="before",
        ),
    ],
}


@dataclass
class FlightOption:
    date: dt.date
    depart_time: str
    arrive_time: str
    duration: str
    stops: str
    price_gbp: int


def search_order_dates(leg: Leg) -> list[dt.date]:
    """Dates to try, closest to the anchor date first."""
    offsets = range(1, leg.window_days + 1)
    sign = 1 if leg.direction == "after" else -1
    return [leg.anchor_date + dt.timedelta(days=sign * d) for d in offsets]


def extract_easyjet_options(page: Page, the_date: dt.date) -> list[FlightOption]:
    lis = page.locator("li")
    count = lis.count()
    seen: set[tuple[str, str, str]] = set()
    options: list[FlightOption] = []
    for i in range(count):
        try:
            text = lis.nth(i).inner_text()
        except Exception:
            continue
        if AIRLINE not in text or "Select flight" in text:
            continue
        m_time = FLIGHT_TIME_RE.search(text)
        m_price = PRICE_RE.search(text)
        if not (m_time and m_price):
            continue
        key = (m_time.group(1), m_time.group(2), m_price.group(1))
        if key in seen:
            continue
        seen.add(key)
        m_dur = DURATION_RE.search(text)
        m_stops = STOPS_RE.search(text)
        options.append(
            FlightOption(
                date=the_date,
                depart_time=m_time.group(1),
                arrive_time=m_time.group(2),
                duration=m_dur.group(1) if m_dur else "?",
                stops=m_stops.group(1) if m_stops else "?",
                price_gbp=int(m_price.group(1).replace(",", "")),
            )
        )
    return options


def accept_google_consent_if_present(page: Page) -> None:
    try:
        page.get_by_role("button", name="Accept all").click(timeout=4000)
    except PlaywrightTimeoutError:
        pass


def search_leg(page: Page, leg: Leg) -> list[FlightOption]:
    results: list[FlightOption] = []
    for the_date in search_order_dates(leg):
        if len(results) >= leg.num_options:
            break
        query = f"one way flights from {leg.origin} to {leg.destination} on {the_date.isoformat()}"
        url = "https://www.google.com/travel/flights?q=" + query.replace(" ", "%20")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        accept_google_consent_if_present(page)
        try:
            page.wait_for_selector(f"li:has-text('{AIRLINE}')", timeout=12000)
        except PlaywrightTimeoutError:
            continue  # no easyJet service that day
        page.wait_for_timeout(1200)
        day_options = extract_easyjet_options(page, the_date)
        results.extend(day_options)
    results = results[: leg.num_options] if len(results) > leg.num_options else results
    results.sort(key=lambda o: (o.date, dt.datetime.strptime(o.depart_time, "%I:%M %p").time()))
    return results


def run_search(leg_set_name: str, headless: bool = True) -> dict[str, list[FlightOption]]:
    legs = LEG_SETS[leg_set_name]
    report: dict[str, list[FlightOption]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        for leg in legs:
            report[leg.label] = search_leg(page, leg)
        browser.close()
    return report, legs


def format_price(o: FlightOption) -> str:
    return f"£{o.price_gbp}"


def build_html_report(leg_set_name: str, legs: list[Leg], report: dict[str, list[FlightOption]]) -> str:
    parts = [
        "<html><head><meta charset='utf-8'>",
        "<title>easyJet ABZ &lt;-&gt; LTN flight options</title>",
        "<style>",
        "body{font-family:Arial,Helvetica,sans-serif;margin:2em;color:#222}",
        "h1{color:#FF6600}h2{margin-top:2em;border-bottom:2px solid #FF6600;padding-bottom:4px}",
        "table{border-collapse:collapse;width:100%;margin-top:0.5em}",
        "th,td{border:1px solid #ddd;padding:8px;text-align:left}",
        "th{background:#FF6600;color:white}",
        "tr:nth-child(even){background:#f7f7f7}",
        ".muted{color:#666;font-size:0.9em}",
        "</style></head><body>",
        f"<h1>easyJet ABZ &lt;-&gt; LTN flight options ({leg_set_name})</h1>",
        f"<p class='muted'>Generated {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}. "
        "Fares are live snapshots from Google Flights (easyJet-operated only) and can change; "
        "confirm the final price on easyjet.com before booking.</p>",
    ]
    for leg in legs:
        options = report.get(leg.label, [])
        parts.append(f"<h2>{leg.label}</h2>")
        parts.append(f"<p class='muted'>Anchor: {leg.anchor_description} &mdash; showing dates {leg.direction} this.</p>")
        if not options:
            parts.append("<p><em>No easyJet flights found in the search window.</em></p>")
            continue
        parts.append("<table><tr><th>Date</th><th>Departs</th><th>Arrives</th><th>Duration</th><th>Stops</th><th>Price</th></tr>")
        for o in options:
            parts.append(
                "<tr>"
                f"<td>{o.date.strftime('%a %d %b %Y')}</td>"
                f"<td>{o.depart_time}</td>"
                f"<td>{o.arrive_time}</td>"
                f"<td>{o.duration}</td>"
                f"<td>{o.stops}</td>"
                f"<td>{format_price(o)}</td>"
                "</tr>"
            )
        parts.append("</table>")
    parts.append("</body></html>")
    return "\n".join(parts)


def send_email(html_body: str, subject: str) -> None:
    address = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not address or not app_password:
        raise RuntimeError(
            "GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment variables must both be set to send email."
        )
    if not RECIPIENTS:
        raise RuntimeError("RECIPIENTS environment variable must be set (comma-separated email addresses) to send email.")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = address
    msg["To"] = ", ".join(RECIPIENTS)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(address, app_password)
        server.sendmail(address, RECIPIENTS, msg.as_string())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--leg-set", choices=sorted(LEG_SETS.keys()), default="winter", help="Which pair of legs to search (default: winter)")
    parser.add_argument("--email", action="store_true", help="Also email the report to the configured recipients (requires GMAIL_ADDRESS / GMAIL_APP_PASSWORD env vars)")
    parser.add_argument("--headed", action="store_true", help="Run the browser headed (visible) instead of headless")
    parser.add_argument("--out-dir", default="reports", help="Directory to write the HTML report into (default: ./reports)")
    args = parser.parse_args()

    report, legs = run_search(args.leg_set, headless=not args.headed)

    html = build_html_report(args.leg_set, legs, report)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"flight_options_{args.leg_set}_{dt.date.today().isoformat()}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"Report written to {out_path.resolve()}")

    for leg in legs:
        options = report.get(leg.label, [])
        print(f"\n{leg.label}")
        if not options:
            print("  No easyJet flights found in the search window.")
        for o in options:
            print(f"  {o.date.strftime('%a %d %b %Y')}  {o.depart_time} -> {o.arrive_time}  {o.duration}  {o.stops}  {format_price(o)}")

    if args.email:
        subject = f"easyJet ABZ/LTN flight options ({args.leg_set})"
        send_email(html, subject)
        print(f"\nEmail sent to: {', '.join(RECIPIENTS)}")
    else:
        print("\n--email not passed: report saved locally only, nothing was sent.")


if __name__ == "__main__":
    main()
