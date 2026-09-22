"""
AMC Tracker business logic - ported 1:1 from the standalone "Handyman.ae AMC
Tracker" prototype (a client-side, localStorage-only tool). The scheduling
and status rules below are copied exactly from that file's JS; only the
storage layer changed (SQLite behind our normal session/role auth, instead
of a password-derived AES key over localStorage - see server.py's amc_*
functions).

Every AMC package carries a fixed AC PPM (planned preventive maintenance)
visit schedule, computed as an offset from the contract's start date - never
typed in by hand:

    Basic:    visit 1 at start+14 days, visit 2 at start+6 months
    Standard: + a visit 3 at start+8 months
    Premium:  + a visit 4 at start+9 months

Handyman visits (separate from AC PPM) are an allowance, not a schedule -
0/1/2 per year depending on package - logged manually as they happen.

A visit's live status (never stored - always recomputed against "today"):
    Done       - a completion date has been logged
    OVERDUE    - due date has passed, nothing logged
    Due Soon   - due within 14 days
    Scheduled  - due, but more than 14 days out
    N/A        - this package doesn't include this visit number
"""
from datetime import date, timedelta
import calendar

PACKAGE_SCHEDULE = {
    "Basic": [(1, {"d": 14}), (2, {"m": 6})],
    "Standard": [(1, {"d": 14}), (2, {"m": 4}), (3, {"m": 8})],
    "Premium": [(1, {"d": 14}), (2, {"m": 3}), (3, {"m": 6}), (4, {"m": 9})],
}
HANDYMAN_VISITS = {"Basic": 0, "Standard": 1, "Premium": 2}
PACKAGE_PRICES = {"Basic": 3255, "Standard": 4893, "Premium": 6531}
PACKAGES = ("Basic", "Standard", "Premium")
OWNER_TENANT_OPTIONS = ("Owner", "Tenant", "Commercial")
PAY_TYPE_OPTIONS = ("Upfront", "50/50", "Monthly", "FOC")
PAY_STATUS_OPTIONS = ("Paid", "50% Paid", "Unpaid", "FOC")
VISIT_STATUS_OPTIONS = ("Pending", "Scheduled", "Done", "Overdue", "N/A")
HISTORY_STATUS_OPTIONS = ("Active", "Expired", "Cancelled")


def parse_date(s):
    if not s:
        return None
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def add_months(d, n):
    """Adds n calendar months, clamping the day into the target month (e.g.
    31 Jan + 1 month -> 28/29 Feb) - matches the JS addMonths() exactly."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def visit_due_date(start_iso, offset):
    start = parse_date(start_iso)
    if not start:
        return None
    if "d" in offset:
        return start + timedelta(days=offset["d"])
    return add_months(start, offset["m"])


def get_visits(client, today=None):
    """Returns a list of 4 dicts {n, due, done, status} - always 4 entries
    (N/A for visit numbers this package doesn't include), matching the
    fixed 4-column V1-V4 layout in the Tracker table."""
    t = today or date.today()
    sched = {n: off for n, off in PACKAGE_SCHEDULE.get(client.get("package"), [])}
    visits = []
    for n in range(1, 5):
        done_iso = client.get("v%dDone" % n) or ""
        off = sched.get(n)
        if not off:
            visits.append({"n": n, "due": None, "done": done_iso, "status": "N/A"})
            continue
        due = visit_due_date(client.get("start"), off)
        if done_iso:
            status = "Done"
        elif due and t > due:
            status = "OVERDUE"
        elif due and (due - t).days <= 14:
            status = "Due Soon"
        else:
            status = "Scheduled"
        visits.append({"n": n, "due": due, "done": done_iso, "status": status})
    return visits


def on_track_status(client, today=None):
    visits = get_visits(client, today)
    if any(v["status"] == "OVERDUE" for v in visits):
        return "behind"
    applicable = [v for v in visits if v["status"] != "N/A"]
    if applicable and all(v["status"] == "Done" for v in applicable):
        return "complete"
    return "ontrack"


def days_left(client, today=None):
    end = parse_date(client.get("end"))
    if not end:
        return None
    t = today or date.today()
    return (end - t).days


def renewal_alert(dl):
    if dl is None:
        return ""
    if dl < 0:
        return "Expired"
    if dl <= 14:
        return "Send now"
    if dl <= 30:
        return "Due soon"
    return ""


def pay_flag(client):
    if client.get("payStatus") == "Unpaid":
        return "unpaid"
    if client.get("payStatus") == "50% Paid":
        return "partial"
    return "paid"


def client_computed(client, today=None):
    """Every derived field the frontend needs for one client row/detail
    panel, bundled alongside the stored fields - so the API response is
    self-contained and the frontend never re-implements this math."""
    t = today or date.today()
    visits = get_visits(client, t)
    dl = days_left(client, t)
    return {
        "visits": [
            {"n": v["n"], "due": v["due"].isoformat() if v["due"] else None,
             "done": v["done"], "status": v["status"]}
            for v in visits
        ],
        "daysLeft": dl,
        "renewalAlert": renewal_alert(dl),
        "trackStatus": on_track_status(client, t),
        "payFlag": pay_flag(client),
        "handymanVisitsAllowed": HANDYMAN_VISITS.get(client.get("package"), 0),
    }
