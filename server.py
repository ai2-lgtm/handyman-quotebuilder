"""
Handyman.ae Quote Builder - backend.

A WSGI application (pure standard library - sqlite3, wsgiref, etc. - no pip
installs required), so it runs both:
  - locally, via `python server.py` (uses wsgiref's built-in dev server), and
  - on Passenger-based hosts (e.g. SiteGround shared/GrowBig Python App tool),
    via passenger_wsgi.py, which just imports `application` from this file.

Passenger expects a WSGI callable, not a script that binds its own socket -
that's the entire reason this file is structured as request-in/response-out
functions around one `application(environ, start_response)` entry point,
rather than a http.server.BaseHTTPRequestHandler subclass.
"""
import http.cookies
import json
import os
import re
import socketserver
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import auth
import pricing

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
# DATA_DIR can be overridden (e.g. DATA_DIR=/data on Railway) to point at a
# mounted persistent volume, since container filesystems are otherwise wiped
# on every redeploy.
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "handyman.db")

# HOST/PORT only matter for the local dev server (main() below) - a Passenger
# deployment ignores them entirely, since Passenger itself owns the socket.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8743"))

SESSION_COOKIE_NAME = "hm_session"
SESSION_TTL_DAYS = 30
# Set FORCE_SECURE_COOKIE=1 once this is served over HTTPS (e.g. behind the
# reverse proxy on your production host) so the session cookie is marked
# Secure. Leave it unset for local http:// testing, or browsers will refuse
# to send the cookie back and every request will look logged-out.
FORCE_SECURE_COOKIE = os.environ.get("FORCE_SECURE_COOKIE", "0") == "1"

# ---------------------------------------------------------------------------
# Google "Sign in with Google" OAuth config - all three are set up in Google
# Cloud Console (see README.txt) and passed in as environment variables, not
# hardcoded, since GOOGLE_CLIENT_SECRET is a real secret.
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", f"http://127.0.0.1:{PORT}/api/auth/google/callback")
GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v3/userinfo"
OAUTH_STATE_TTL_MINUTES = 10

# ---------------------------------------------------------------------------
# Seed data: the category / subcategory taxonomy, ported from the original
# Handyman-Quoting-Suite app, plus two extra catalogs populated with real
# historical prices pulled from past job costings (materials & labour rates).
# Standard/AMC price and typical hours are left blank (None) for the original
# 13 trade categories, matching that app's own convention ("leave blank where
# a job is always quoted from scratch") - editable later from the Price Book.
# ---------------------------------------------------------------------------
SEED_CATEGORIES = [
    ("ac", "AC / HVAC", ["Split AC", "Cassette", "Ducted", "VRF", "Chilled Water", "Ventilation", "Duct Cleaning"]),
    ("plumbing", "Plumbing", ["Leak", "Water Heater", "Pump", "Drainage", "Bathroom Renovation", "Water Tank", "Mixer / Tap", "Valve", "Pipework", "Emergency Call-Out"]),
    ("electrical", "Electrical", ["Lighting", "Socket / Switch", "DB Board", "Rewiring", "Fault Finding", "Appliance Connection"]),
    ("handyman", "Handyman", ["General Repairs", "Furniture Assembly", "TV Mounting", "Curtain / Blind Fitting", "Door Repair", "Shelving & Fixings"]),
    ("carpentry", "Carpentry", ["Door Repair / Replacement", "Cabinet Fabrication", "Skirting", "Wardrobe", "Veneer / Laminate Repair"]),
    ("civil", "Civil Works", ["Tiling", "Demolition", "Gypsum / Ceiling", "Blockwork / Plaster", "Screed"]),
    ("waterproofing", "Waterproofing", ["Bathroom", "Balcony / Terrace", "Roof", "Water Tank", "Leak Injection"]),
    ("painting", "Painting", ["Touch-Up", "Room Repaint", "Full Apartment", "Villa Exterior", "Specialist Finish"]),
    ("cleaning", "Cleaning", ["Deep Clean", "Post-Construction", "AC Duct Clean", "Water Tank Clean", "Facade / External"]),
    ("pest", "Pest Control", ["General Disinfection", "Cockroach", "Bed Bugs", "Termite", "Rodent"]),
    ("amc", "Annual Maintenance", ["AMC Basic", "AMC Standard", "AMC Premium"]),
    ("thirdparty", "Third Party / Outside Works", ["Subcontracted Works"]),
    ("custom", "Custom Quote", ["Blank Quote"]),
]

# name -> standard price (AED), from real past job costings
SEED_MATERIALS = [
    ("Exterior Paint - Colour Matched (Gallon)", 384),
    ("Emulsion Paint - Colour Matched (1L)", 60),
    ("Emulsion Paint - Dulux Colours of the World (Gallon)", 135),
    ("Paint Drum - White (20L)", 420),
    ("Door Paint (1L)", 65),
    ("Wall Putty (Drum)", 100),
    ("Steel Putty", 35),
    ("Sanding Paper #150 (sheet)", 1),
    ('Roller 9"', 12),
    ('Roller 4"', 10),
    ('Paint Brush 2"', 8),
    ("Masking Tape (Roll)", 45),
    ("Masking Tape (piece)", 2),
    ("Polythene Sheet (roll)", 16),
    ("Carton Roll (floor protection)", 85),
    ("Cement (bag)", 25),
    ("Gypsum Compound", 45),
    ("Tile Grout - White", 45),
    ("Tile Grout - Light Grey", 65),
    ("Waterproof Skirting (per meter)", 200),
    ("Scaffolding Rental (per day)", 50),
    ("Wrapping - Small Cabinet/Door", 375),
    ("Wrapping - Big Cabinet/Door", 750),
    ("Door Veneer / Vinyl Wrapping (per door)", 1600),
    ("Plumbing Fittings (assorted)", 50),
    ("Silicon Sealant (tube)", 20),
    ("Shower Glass - Supply & Fit", 2400),
    ("Ceiling Access Panel (60x60cm)", 95),
    ("Miscellaneous Consumables", 500),
]

# AC/plumbing equipment names + prices pulled from 3 more real job-costing
# sheets (Full Pump System Work, MBR Compressor/Fanmotor replacement, Water
# heater/thermostat/actuator valve replacement) - added after SEED_MATERIALS
# above was already seeded into production, so this list is migrated in
# separately with a per-item existence check rather than folded into
# SEED_MATERIALS (which only ever seeds once, on an empty table).
SEED_MATERIALS_V2 = [
    ("Booster Pump 1.5HP (Supply & Fit)", 1200),
    ("Pressure Control Kit System", 195),
    ("Drainage Pump 1HP", 800),
    ('Exhaust Fan 6" (Pump Room)', 230),
    ("Pump Room Alarm System", 175),
    ("PVC Fittings (assorted)", 100),
    ("Pump Platform Blocks & Rubber Pads", 125),
    ("Float Valve", 55),
    ("AC Compressor (with gas & filter drier)", 1400),
    ("AC Outdoor Fan Motor", 210),
    ("AC Capacitor & Contactor", 50),
    ("Water Heater 80L (Supply & Fit)", 545),
    ("Angle Valve", 15),
    ("AC Thermostat", 135),
    ("Actuator Valve", 250),
]

SEED_LABOR = [
    ("In-house Labour (per day)", 1800),
    ("In-house Labour (per hour)", 250),
    ("Extended Area / Additional Painter (per day)", 1800),
    ("Mason Work (per job)", 3500),
    ("Electrician (per day)", 1800),
    ("Plumber (per day)", 1800),
    ("AC Technician (per day)", 1800),
    ("Carpenter (per day)", 1800),
    ("Admin Fee", 250),
]
# NOTE: Transportation and Call-out Fee are intentionally NOT in this catalog -
# the Pricing card has dedicated Transport Qty / Call-Out fields for those, so a
# catalog line for them would double-count against the dedicated fields.

SEED_SUBCONTRACTORS = [
    ("Mason", 0),
    ("Tiler", 0),
    ("Gypsum / Ceiling", 0),
    ("Glass / Aluminium", 0),
    ("Steel Fabrication", 0),
    ("Specialist AC", 0),
]


ADMIN_SEED_EMAILS = ["ai2@legacygroup.me", "richard@handyman.ae", "tegan@handyman.ae"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{6,20}$")
APPROVAL_THRESHOLD_AED = 2000


def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def next_quote_seq(conn):
    """Allocate the next sequential quote number atomically. BEGIN IMMEDIATE
    grabs SQLite's write lock up front so two threads can't both read the
    same counter value before either writes it back (a plain SELECT MAX()+1
    would race under this app's real multi-threaded WSGI server)."""
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE counters SET value = value + 1 WHERE name = 'quote_no'")
    seq = conn.execute("SELECT value FROM counters WHERE name = 'quote_no'").fetchone()["value"]
    conn.commit()
    return seq


def format_quote_no(seq):
    return "QB-%06d" % seq


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS categories (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS subcategories (
            id TEXT PRIMARY KEY,
            category_id TEXT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            standard_price REAL,
            amc_price REAL,
            typical_hours REAL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS quotes (
            id TEXT PRIMARY KEY,
            quote_no TEXT,
            quote_date TEXT,
            valid_until TEXT,
            staff TEXT,
            client_name TEXT,
            client_phone TEXT,
            client_address TEXT,
            client_email TEXT,
            duration TEXT,
            scope TEXT,
            terms TEXT,
            transport_qty REAL,
            transport_fee REAL,
            call_out INTEGER,
            call_out_fee REAL,
            discount_pct REAL,
            override_price REAL,
            labour_margin_pct REAL,
            markup_material_pct REAL,
            markup_labour_pct REAL,
            markup_subcontractor_pct REAL,
            vat_pct REAL,
            material_cost REAL,
            labour_sell_base REAL,
            labour_cost REAL,
            sub_cost REAL,
            other_cost REAL,
            vehicle REAL,
            call_out_amount REAL,
            material_sell REAL,
            labour_sell REAL,
            sub_sell REAL,
            cost_price REAL,
            gross REAL,
            discount_amount REAL,
            net_selling REAL,
            selling_price REAL,
            profit REAL,
            margin_pct REAL,
            margin_band TEXT,
            vat_amount REAL,
            grand_total REAL,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS quote_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('material','labour','subcontractor','other')),
            description TEXT,
            price REAL,
            qty REAL,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            created_at TEXT,
            last_login_at TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT,
            expires_at TEXT
        );
        CREATE TABLE IF NOT EXISTS oauth_states (
            state TEXT PRIMARY KEY,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS admin_allowlist (
            email TEXT PRIMARY KEY,
            added_by_email TEXT,
            added_at TEXT
        );
        CREATE TABLE IF NOT EXISTS counters (
            name TEXT PRIMARY KEY,
            value INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL,
            updated_by_email TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS quote_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_id TEXT REFERENCES quotes(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            actor_user_id TEXT,
            actor_email TEXT,
            at TEXT NOT NULL,
            summary TEXT,
            before_json TEXT,
            after_json TEXT
        );
        CREATE TABLE IF NOT EXISTS quote_templates (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            created_by_email TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS quote_template_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id TEXT NOT NULL REFERENCES quote_templates(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            description TEXT,
            default_cost REAL,
            default_sell REAL,
            default_qty REAL NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pb_materials (
            id TEXT PRIMARY KEY,
            category TEXT,
            item_name TEXT NOT NULL,
            brand TEXT,
            model_or_size TEXT,
            unit TEXT,
            cost REAL,
            default_sell REAL,
            supplier TEXT,
            last_updated TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pb_labour (
            id TEXT PRIMARY KEY,
            role_name TEXT NOT NULL,
            labour_type TEXT CHECK(labour_type IN ('staff','outside')),
            cost REAL,
            default_sell REAL,
            unit TEXT,
            last_updated TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pb_fixed_services (
            id TEXT PRIMARY KEY,
            service_name TEXT NOT NULL,
            category TEXT,
            estimated_cost REAL,
            standard_sell REAL,
            last_updated TEXT,
            created_at TEXT
        );
    """)
    conn.commit()

    # migration: the very first version of login used email+password; that
    # was replaced with Google Sign-In before any real account existed, so
    # it's safe to drop the old password columns rather than carry them
    # forward unused.
    existing_user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "password_hash" in existing_user_cols:
        cur.executescript("DROP TABLE IF EXISTS sessions; DROP TABLE IF EXISTS users;")
        conn.commit()
        cur.executescript("""
            CREATE TABLE users (
                id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT,
                created_at TEXT, last_login_at TEXT
            );
            CREATE TABLE sessions (
                token TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT, expires_at TEXT
            );
        """)
        conn.commit()

    # migration: older DBs had the pre-pricing-engine schema (no cost_price column).
    # quotes/quote_items only ever held throwaway test data, so it's safe to rebuild
    # them fresh with the new schema rather than hand-migrate each row.
    existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(quotes)").fetchall()}
    if "cost_price" not in existing_cols:
        cur.executescript("DROP TABLE IF EXISTS quote_items; DROP TABLE IF EXISTS quotes;")
        conn.commit()
        cur.executescript("""
            CREATE TABLE quotes (
                id TEXT PRIMARY KEY, quote_no TEXT, quote_date TEXT, valid_until TEXT, staff TEXT,
                client_name TEXT, client_phone TEXT, client_address TEXT, client_email TEXT,
                duration TEXT, scope TEXT, terms TEXT,
                transport_qty REAL, transport_fee REAL, call_out INTEGER, call_out_fee REAL,
                discount_pct REAL, override_price REAL,
                labour_margin_pct REAL, markup_material_pct REAL, markup_labour_pct REAL, markup_subcontractor_pct REAL,
                vat_pct REAL,
                material_cost REAL, labour_sell_base REAL, labour_cost REAL, sub_cost REAL, other_cost REAL,
                vehicle REAL, call_out_amount REAL, material_sell REAL, labour_sell REAL, sub_sell REAL,
                cost_price REAL, gross REAL, discount_amount REAL, net_selling REAL, selling_price REAL,
                profit REAL, margin_pct REAL, margin_band TEXT, vat_amount REAL, grand_total REAL,
                created_at TEXT, updated_at TEXT
            );
            CREATE TABLE quote_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('material','labour','subcontractor','other')),
                description TEXT, price REAL, qty REAL, sort_order INTEGER NOT NULL DEFAULT 0
            );
        """)
        conn.commit()

    # migration: drop catalog items superseded by the dedicated Transport/Call-out
    # fields, so they can't be double-counted.
    conn.execute("DELETE FROM subcategories WHERE id IN ('labour.transportation-per-trip','labour.call-out-fee','labour.outside-worker-subcontractor')")
    conn.commit()

    # migration: tag each quote with who created it (added when login/auth was
    # introduced) - existing quotes just get a blank creator, nothing is lost.
    existing_quote_cols = {r["name"] for r in conn.execute("PRAGMA table_info(quotes)").fetchall()}
    if "created_by_email" not in existing_quote_cols:
        conn.execute("ALTER TABLE quotes ADD COLUMN created_by_email TEXT")
        conn.commit()

    # migration: roles. users.role defaults every existing row to 'staff' for
    # free; admin_allowlist is consulted only at the moment a brand-new user
    # row is created (see find_or_create_user), so it's re-seeded (INSERT OR
    # IGNORE) every boot rather than gated on "is this the first run" - safe
    # to run forever, and lets ADMIN_SEED_EMAILS grow later without a new
    # migration block.
    existing_user_cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "role" not in existing_user_cols2:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'staff'")
        conn.commit()
    for admin_email in ADMIN_SEED_EMAILS:
        conn.execute(
            "INSERT OR IGNORE INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?)",
            (admin_email, "system-seed", now_iso()),
        )
    conn.execute(
        "UPDATE users SET role='admin' WHERE email IN (SELECT email FROM admin_allowlist) AND role != 'admin'"
    )
    conn.commit()

    # migration: sequential quote numbering. The counters row is created once;
    # quote_no allocation always goes through next_quote_seq() from here on.
    if not conn.execute("SELECT 1 FROM counters WHERE name='quote_no'").fetchone():
        conn.execute("INSERT INTO counters (name, value) VALUES ('quote_no', 0)")
        conn.commit()

    # migration: approval workflow / revisions / notes fields on quotes, plus
    # a one-time backfill for rows that predate this column set. The backfill
    # runs INSIDE this same "column is missing" guard - not as a separately
    # gated step - specifically so there's no ambiguous window where a
    # pre-existing row has the column (with its bare SQL DEFAULT) but hasn't
    # been backfilled yet; if that were a separate guard, there would be no
    # reliable way to tell "a legitimately-blank field on an old row" apart
    # from "a row this backfill simply hasn't reached yet".
    existing_quote_cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(quotes)").fetchall()}
    if "status" not in existing_quote_cols2:
        conn.execute("ALTER TABLE quotes ADD COLUMN status TEXT NOT NULL DEFAULT 'Draft'")
        conn.execute("ALTER TABLE quotes ADD COLUMN parent_quote_id TEXT")
        conn.execute("ALTER TABLE quotes ADD COLUMN root_quote_id TEXT")
        conn.execute("ALTER TABLE quotes ADD COLUMN revision_number INTEGER NOT NULL DEFAULT 1")
        conn.execute("ALTER TABLE quotes ADD COLUMN quote_seq INTEGER")
        conn.execute("ALTER TABLE quotes ADD COLUMN internal_notes TEXT")
        conn.execute("ALTER TABLE quotes ADD COLUMN technician TEXT")
        conn.execute("ALTER TABLE quotes ADD COLUMN prepared_by_email TEXT")
        conn.execute("ALTER TABLE quotes ADD COLUMN markup_pct REAL")
        conn.commit()

        # Every row that existed before this migration predates the whole
        # approval workflow - treat it as already-historical/finalized rather
        # than guessing at a Draft/Approval status that was never tracked.
        conn.execute("UPDATE quotes SET status='Sent to Jobber', root_quote_id=id, prepared_by_email=created_by_email WHERE quote_seq IS NULL")
        conn.commit()

        # Assign sequential quote numbers to every pre-existing row, oldest
        # first, through the same counter new quotes will use - this runs
        # inside init_db(), before the WSGI server accepts its first request,
        # so there's no concurrency to worry about here.
        rows_needing_no = conn.execute(
            "SELECT id FROM quotes WHERE quote_seq IS NULL ORDER BY created_at ASC, id ASC"
        ).fetchall()
        for row in rows_needing_no:
            seq = next_quote_seq(conn)
            conn.execute("UPDATE quotes SET quote_seq=?, quote_no=? WHERE id=?", (seq, format_quote_no(seq), row["id"]))
            conn.commit()

    # migration: quote_items needs a 6-value kind CHECK (was 4) plus cost/
    # sell/markup_pct columns instead of one shared 'price' column. SQLite
    # can't ALTER a CHECK constraint in place, so this is the one necessary
    # table rebuild in this file that actually preserves data (unlike the two
    # old DROP-and-recreate migrations above, which predate any real data
    # existing) - every existing row is copied forward, reconstructing cost/
    # sell from that row's own quote's old global markup percentages so
    # historical totals stay intact for what are now all locked, historical
    # ('Sent to Jobber') quotes.
    quote_items_cols = {r["name"] for r in conn.execute("PRAGMA table_info(quote_items)").fetchall()}
    if "cost" not in quote_items_cols:
        conn.execute("""
            CREATE TABLE quote_items_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK(kind IN ('material','staff_labour','outside_labour','fixed_service','project_management','other')),
                description TEXT,
                cost REAL NOT NULL DEFAULT 0,
                sell REAL NOT NULL DEFAULT 0,
                markup_pct REAL,
                qty REAL NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                price_book_ref_id TEXT
            )
        """)
        old_items = conn.execute("SELECT * FROM quote_items").fetchall()
        quotes_by_id = {r["id"]: r for r in conn.execute("SELECT * FROM quotes").fetchall()}
        for it in old_items:
            qrow = quotes_by_id.get(it["quote_id"])
            mm = (qrow["markup_material_pct"] if qrow and qrow["markup_material_pct"] is not None else 50.0)
            ml = (qrow["markup_labour_pct"] if qrow and qrow["markup_labour_pct"] is not None else 0.0)
            ms = (qrow["markup_subcontractor_pct"] if qrow and qrow["markup_subcontractor_pct"] is not None else 15.0)
            lm = (qrow["labour_margin_pct"] if qrow and qrow["labour_margin_pct"] is not None else 50.0)
            old_price = it["price"] or 0.0
            if it["kind"] == "material":
                new_kind, cost, markup = "material", old_price, mm
                sell = cost * (1 + markup / 100)
            elif it["kind"] == "labour":
                new_kind, markup = "staff_labour", None
                sell = old_price
                cost = old_price * (1 - lm / 100)
            elif it["kind"] == "subcontractor":
                new_kind, markup = "outside_labour", None
                cost = old_price
                sell = cost * (1 + ms / 100)
            else:
                new_kind, markup = "other", None
                cost = sell = old_price
            conn.execute(
                "INSERT INTO quote_items_v2 (quote_id, kind, description, cost, sell, markup_pct, qty, sort_order) VALUES (?,?,?,?,?,?,?,?)",
                (it["quote_id"], new_kind, it["description"], cost, sell, markup, it["qty"] or 0, it["sort_order"]),
            )
        conn.execute("DROP TABLE quote_items")
        conn.execute("ALTER TABLE quote_items_v2 RENAME TO quote_items")
        conn.commit()

    seeded = cur.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
    if seeded == 0:
        for order, (cat_id, cat_name, subs) in enumerate(SEED_CATEGORIES):
            cur.execute("INSERT INTO categories (id, name, sort_order) VALUES (?,?,?)", (cat_id, cat_name, order))
            for s_order, sub_name in enumerate(subs):
                sub_id = cat_id + "." + re.sub(r"[^a-z0-9]+", "-", sub_name.lower()).strip("-")
                cur.execute(
                    "INSERT INTO subcategories (id, category_id, name, standard_price, amc_price, typical_hours, sort_order) VALUES (?,?,?,?,?,?,?)",
                    (sub_id, cat_id, sub_name, None, None, None, s_order),
                )

        cur.execute("INSERT INTO categories (id, name, sort_order) VALUES (?,?,?)", ("materials", "Materials & Consumables", 100))
        for order, (name, price) in enumerate(SEED_MATERIALS):
            sub_id = "materials." + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            cur.execute(
                "INSERT INTO subcategories (id, category_id, name, standard_price, amc_price, typical_hours, sort_order) VALUES (?,?,?,?,?,?,?)",
                (sub_id, "materials", name, price, None, None, order),
            )

        cur.execute("INSERT INTO categories (id, name, sort_order) VALUES (?,?,?)", ("labour", "General Labour & Admin", 101))
        for order, (name, price) in enumerate(SEED_LABOR):
            sub_id = "labour." + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            cur.execute(
                "INSERT INTO subcategories (id, category_id, name, standard_price, amc_price, typical_hours, sort_order) VALUES (?,?,?,?,?,?,?)",
                (sub_id, "labour", name, price, None, None, order),
            )
        conn.commit()

    # migration: add the Subcontractors catalog if it's missing (e.g. an existing
    # DB seeded before the pricing-engine upgrade added a dedicated subcontractors
    # cost type).
    if not conn.execute("SELECT 1 FROM categories WHERE id='subcontractors'").fetchone():
        max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) AS m FROM categories").fetchone()["m"]
        conn.execute("INSERT INTO categories (id, name, sort_order) VALUES (?,?,?)", ("subcontractors", "Subcontractors", max_order + 1))
        for order, (name, price) in enumerate(SEED_SUBCONTRACTORS):
            sub_id = "subcontractors." + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
            conn.execute(
                "INSERT INTO subcategories (id, category_id, name, standard_price, amc_price, typical_hours, sort_order) VALUES (?,?,?,?,?,?,?)",
                (sub_id, "subcontractors", name, price, None, None, order),
            )
        conn.commit()

    # migration: seed the v2 Price Book (Materials/Labour) from the same real
    # historical job-costing prices as SEED_MATERIALS/SEED_LABOR above, so the
    # office isn't starting from a completely blank catalog. Deliberately
    # conservative: these old records only ever stored ONE number per item
    # (what was charged), so it's stored here as Default Sell, never
    # fabricated as Cost - Cost is left blank for the office to fill in as
    # they go, exactly as Tegan's spec expects ("office will build out
    # missing... information over time"). Fixed Services has no equivalent
    # historical data at all, so it's seeded empty, not guessed at.
    if not conn.execute("SELECT 1 FROM pb_materials LIMIT 1").fetchone():
        ts = now_iso()
        material_categories = {
            "Exterior Paint - Colour Matched (Gallon)": "Paint", "Emulsion Paint - Colour Matched (1L)": "Paint",
            "Emulsion Paint - Dulux Colours of the World (Gallon)": "Paint", "Paint Drum - White (20L)": "Paint",
            "Door Paint (1L)": "Paint",
            "Wall Putty (Drum)": "Painting Supplies", "Steel Putty": "Painting Supplies",
            "Sanding Paper #150 (sheet)": "Painting Supplies", 'Roller 9"': "Painting Supplies",
            'Roller 4"': "Painting Supplies", 'Paint Brush 2"': "Painting Supplies",
            "Masking Tape (Roll)": "Painting Supplies", "Masking Tape (piece)": "Painting Supplies",
            "Polythene Sheet (roll)": "Painting Supplies", "Carton Roll (floor protection)": "Painting Supplies",
            "Scaffolding Rental (per day)": "Painting Supplies",
            "Cement (bag)": "Tiling & Civil", "Gypsum Compound": "Tiling & Civil",
            "Tile Grout - White": "Tiling & Civil", "Tile Grout - Light Grey": "Tiling & Civil",
            "Waterproof Skirting (per meter)": "Tiling & Civil",
            "Wrapping - Small Cabinet/Door": "Carpentry", "Wrapping - Big Cabinet/Door": "Carpentry",
            "Door Veneer / Vinyl Wrapping (per door)": "Carpentry",
            "Plumbing Fittings (assorted)": "Plumbing", "Silicon Sealant (tube)": "Plumbing",
            "Shower Glass - Supply & Fit": "Plumbing",
            "Ceiling Access Panel (60x60cm)": "General", "Miscellaneous Consumables": "General",
        }
        for name, price in SEED_MATERIALS:
            conn.execute(
                "INSERT INTO pb_materials (id, category, item_name, brand, model_or_size, unit, cost, default_sell, supplier, last_updated, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, material_categories.get(name, "General"), name, None, None, None, None, price, None, ts, ts),
            )
        labour_units = {
            "In-house Labour (per day)": "per day", "In-house Labour (per hour)": "per hour",
            "Extended Area / Additional Painter (per day)": "per day", "Mason Work (per job)": "per job",
            "Electrician (per day)": "per day", "Plumber (per day)": "per day",
            "AC Technician (per day)": "per day", "Carpenter (per day)": "per day", "Admin Fee": "per job",
        }
        for name, price in SEED_LABOR:
            conn.execute(
                "INSERT INTO pb_labour (id, role_name, labour_type, cost, default_sell, unit, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, name, "staff", None, price, labour_units.get(name), ts, ts),
            )
        conn.commit()

    # migration: add the AC/plumbing equipment items from SEED_MATERIALS_V2
    # (real prices from 3 more job-costing sheets). Guarded per-item rather
    # than by "table empty" so it also backfills a pb_materials table that
    # was already seeded/populated in production.
    ts = now_iso()
    for name, price in SEED_MATERIALS_V2:
        exists = conn.execute("SELECT 1 FROM pb_materials WHERE item_name=?", (name,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO pb_materials (id, category, item_name, brand, model_or_size, unit, cost, default_sell, supplier, last_updated, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, "AC & Plumbing Equipment", name, None, None, None, None, price, None, ts, ts),
            )
    conn.commit()

    conn.close()


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or uuid.uuid4().hex[:8]


# ---------------------------------------------------------------------------
# Auth / sessions
# ---------------------------------------------------------------------------

def create_session(user_id):
    token = auth.generate_session_token()
    ts = now_iso()
    expires = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + SESSION_TTL_DAYS * 86400))
    conn = get_conn()
    conn.execute("INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)", (token, user_id, ts, expires))
    conn.commit()
    conn.close()
    return token


def session_cookie_header(token):
    attrs = f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_DAYS * 86400}"
    if FORCE_SECURE_COOKIE:
        attrs += "; Secure"
    return attrs


def clear_cookie_header():
    attrs = f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
    if FORCE_SECURE_COOKIE:
        attrs += "; Secure"
    return attrs


def parse_cookies(environ):
    raw = environ.get("HTTP_COOKIE")
    if not raw:
        return {}
    jar = http.cookies.SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return {}
    return {k: v.value for k, v in jar.items()}


def get_session_user(environ):
    token = parse_cookies(environ).get(SESSION_COOKIE_NAME)
    if not token:
        return None
    conn = get_conn()
    row = conn.execute(
        "SELECT u.id AS user_id, u.email AS email, u.role AS role, s.expires_at AS expires_at "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token=?",
        (token,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    if row["expires_at"] and row["expires_at"] < now_iso():
        return None
    return {"id": row["user_id"], "email": row["email"], "role": row["role"]}


def is_admin(user):
    return bool(user) and user.get("role") == "admin"


def forbidden():
    return json_response(403, {"error": "admins only"})


def create_oauth_state():
    state = auth.generate_state_token()
    conn = get_conn()
    conn.execute("INSERT INTO oauth_states (state, created_at) VALUES (?,?)", (state, now_iso()))
    # sweep anything older than the TTL so this table can't grow forever
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - OAUTH_STATE_TTL_MINUTES * 60))
    conn.execute("DELETE FROM oauth_states WHERE created_at < ?", (cutoff,))
    conn.commit()
    conn.close()
    return state


def consume_oauth_state(state):
    """Returns True exactly once for a state we issued within the TTL - used
    so a captured/replayed callback URL can't be reused to force a login."""
    if not state:
        return False
    conn = get_conn()
    row = conn.execute("SELECT created_at FROM oauth_states WHERE state=?", (state,)).fetchone()
    if row:
        conn.execute("DELETE FROM oauth_states WHERE state=?", (state,))
        conn.commit()
    conn.close()
    if not row:
        return False
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - OAUTH_STATE_TTL_MINUTES * 60))
    return row["created_at"] >= cutoff


def find_or_create_user(email, name):
    conn = get_conn()
    row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    ts = now_iso()
    if row:
        conn.execute("UPDATE users SET last_login_at=?, name=? WHERE id=?", (ts, name, row["id"]))
        conn.commit()
        user_id = row["id"]
    else:
        user_id = uuid.uuid4().hex
        role = "admin" if conn.execute("SELECT 1 FROM admin_allowlist WHERE email=?", (email,)).fetchone() else "staff"
        conn.execute(
            "INSERT INTO users (id, email, name, role, created_at, last_login_at) VALUES (?,?,?,?,?,?)",
            (user_id, email, name, role, ts, ts),
        )
        conn.commit()
    conn.close()
    return user_id


# ---------------------------------------------------------------------------
# Company-wide settings - pricing.DEFAULTS is the fallback; any key an admin
# has explicitly saved in app_settings overrides it. Kept as a simple
# key/value table (not a single JSON blob) so a missing key just falls back
# to its DEFAULTS value automatically, with no migration needed when a new
# setting is added later.
# ---------------------------------------------------------------------------

# Which pricing.DEFAULTS keys an admin is allowed to change. Deliberately
# excludes the AED 2,000 approval threshold (APPROVAL_THRESHOLD_AED) - that's
# a specific figure from the business requirement, not a tunable price knob,
# so it stays a code constant rather than something editable from the UI.
SETTABLE_KEYS = (
    "hourlyRate", "transportFee", "callOutFee", "vatPct",
    "marginMinPct", "marginTargetPct", "marginUpperPct", "maxDiscountPct",
    "defaultMaterialMarkupPct",
)


def get_effective_settings():
    settings = dict(pricing.DEFAULTS)
    conn = get_conn()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    conn.close()
    for r in rows:
        if r["key"] in settings:
            settings[r["key"]] = r["value"]
    return settings


def update_settings(body, actor_email):
    updates = {k: v for k, v in (body or {}).items() if k in SETTABLE_KEYS}
    if not updates:
        return json_response(400, {"error": "No recognised settings in request body."})
    for k, v in updates.items():
        try:
            v = float(v)
        except (TypeError, ValueError):
            return json_response(400, {"error": f"{k} must be a number."})
        if v < 0:
            return json_response(400, {"error": f"{k} cannot be negative."})
        updates[k] = v
    conn = get_conn()
    ts = now_iso()
    for k, v in updates.items():
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_by_email, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by_email=excluded.updated_by_email, updated_at=excluded.updated_at",
            (k, v, actor_email, ts),
        )
    conn.commit()
    conn.close()
    return json_response(200, get_effective_settings())


# ---------------------------------------------------------------------------
# Data access helpers
# ---------------------------------------------------------------------------

def fetch_pricebook():
    conn = get_conn()
    cats = conn.execute("SELECT * FROM categories ORDER BY sort_order, name").fetchall()
    subs = conn.execute("SELECT * FROM subcategories ORDER BY sort_order, name").fetchall()
    conn.close()
    by_cat = {}
    for s in subs:
        by_cat.setdefault(s["category_id"], []).append({
            "id": s["id"], "categoryId": s["category_id"], "name": s["name"],
            "standardPrice": s["standard_price"], "amcPrice": s["amc_price"],
            "typicalHours": s["typical_hours"], "sortOrder": s["sort_order"],
        })
    return [{
        "id": c["id"], "name": c["name"], "sortOrder": c["sort_order"],
        "subcategories": by_cat.get(c["id"], []),
    } for c in cats]


def quote_row_to_dict(row, items=None):
    quote_no = row["quote_no"]
    if row["revision_number"] and row["revision_number"] > 1:
        quote_no = (quote_no or "") + "-R" + str(row["revision_number"])
    d = {
        "id": row["id"], "quoteNo": quote_no, "quoteSeq": row["quote_seq"], "date": row["quote_date"],
        "validUntil": row["valid_until"], "staff": row["staff"],
        "status": row["status"], "revisionNumber": row["revision_number"],
        "parentQuoteId": row["parent_quote_id"], "rootQuoteId": row["root_quote_id"],
        "technician": row["technician"], "internalNotes": row["internal_notes"],
        "preparedBy": row["prepared_by_email"] or row["created_by_email"],
        "client": {
            "name": row["client_name"], "phone": row["client_phone"],
            "address": row["client_address"], "email": row["client_email"],
        },
        "duration": row["duration"], "scope": row["scope"], "terms": row["terms"],
        "transportQty": row["transport_qty"], "transportFee": row["transport_fee"],
        "callOut": bool(row["call_out"]), "callOutFee": row["call_out_fee"],
        "discountPct": row["discount_pct"], "overridePrice": row["override_price"],
        "vatPct": row["vat_pct"],
        "vehicle": row["vehicle"], "callOutAmount": row["call_out_amount"],
        "costPrice": row["cost_price"], "gross": row["gross"], "discountAmount": row["discount_amount"],
        "netSelling": row["net_selling"], "sellingPrice": row["selling_price"], "profit": row["profit"],
        "markupPct": row["markup_pct"], "marginPct": row["margin_pct"], "marginBand": row["margin_band"],
        "vatAmount": row["vat_amount"], "grandTotal": row["grand_total"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "createdBy": row["created_by_email"],
    }
    if items is not None:
        d["items"] = [{
            "id": i["id"], "kind": i["kind"], "desc": i["description"],
            "cost": i["cost"], "sell": i["sell"], "markupPct": i["markup_pct"],
            "qty": i["qty"], "priceBookRefId": i["price_book_ref_id"],
        } for i in items]
    return d


class SaveError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def save_quote(payload, existing_id=None, created_by_email=None):
    items = payload.get("items") or []
    client = payload.get("client") or {}

    if not (client.get("name") or "").strip():
        raise SaveError(400, "Client name is required.")
    if not any(_item_sell_positive(i) for i in items):
        raise SaveError(400, "At least one priced line item is required.")
    for field, regex in (("email", EMAIL_RE), ("phone", PHONE_RE)):
        val = (client.get(field) or "").strip()
        if val and not regex.match(val):
            raise SaveError(400, "Client %s doesn't look valid." % field)

    conn = get_conn()
    existing_row = None
    if existing_id:
        existing_row = conn.execute("SELECT * FROM quotes WHERE id=?", (existing_id,)).fetchone()
        if not existing_row:
            conn.close()
            raise SaveError(404, "Quote not found.")
        if existing_row["status"] == "Sent to Jobber":
            conn.close()
            raise SaveError(409, "This quote is locked (Sent to Jobber). Create a revision to change it.")

    effective = get_effective_settings()
    calc_input = dict(payload)
    calc_input["items"] = items
    calc_input["transportFee"] = payload.get("transportFee") if payload.get("transportFee") is not None else effective["transportFee"]
    calc_input["callOutFee"] = payload.get("callOutFee") if payload.get("callOutFee") is not None else effective["callOutFee"]
    calc_input["vatPct"] = payload.get("vatPct") if payload.get("vatPct") is not None else effective["vatPct"]
    calc_input["maxDiscountPct"] = effective["maxDiscountPct"]
    calc_input["marginMinPct"] = effective["marginMinPct"]
    calc_input["marginTargetPct"] = effective["marginTargetPct"]
    calc_input["marginUpperPct"] = effective["marginUpperPct"]

    try:
        r = pricing.compute(calc_input)
    except pricing.ValidationError as e:
        conn.close()
        raise SaveError(400, str(e))

    transport_qty = float(payload.get("transportQty") or 0)
    transport_fee = float(calc_input["transportFee"])
    call_out = 1 if payload.get("callOut") else 0
    call_out_fee = float(calc_input["callOutFee"])
    discount_pct = float(payload.get("discountPct") or 0)
    override_price = float(payload.get("overridePrice") or 0)
    vat_pct = float(calc_input["vatPct"])

    # Approval auto-escalation: a Draft quote that crosses the AED threshold
    # on save automatically becomes Approval Required - this only ever
    # escalates, never de-escalates, so it can't silently undo a deliberate
    # "Submit for Approval Anyway" on a smaller quote, or a quote an admin
    # has already started reviewing. Moving back to Draft is only ever done
    # explicitly via the return_to_draft action. A quote Sent to Jobber never
    # reaches here at all (blocked above).
    if existing_row is None:
        new_status = "Approval Required" if r["sellingPrice"] > APPROVAL_THRESHOLD_AED else "Draft"
    else:
        current_status = existing_row["status"]
        if current_status == "Draft" and r["sellingPrice"] > APPROVAL_THRESHOLD_AED:
            new_status = "Approval Required"
        else:
            new_status = current_status

    quote_id = existing_id or uuid.uuid4().hex
    ts = now_iso()

    fields = (
        payload.get("date"), payload.get("validUntil"), payload.get("staff"),
        client.get("name"), client.get("phone"), client.get("address"), client.get("email"),
        payload.get("duration"), payload.get("scope"), payload.get("terms"),
        payload.get("internalNotes"), payload.get("technician"),
        transport_qty, transport_fee, call_out, call_out_fee,
        discount_pct, override_price, vat_pct,
        r["vehicle"], r["callOutAmount"],
        r["costPrice"], r["gross"], r["discountAmount"], r["netSelling"], r["sellingPrice"],
        r["profit"], r["markupPct"], r["marginPct"], r["marginBand"], r["vatAmount"], r["grandTotal"],
        new_status,
    )
    field_cols = [
        "quote_date", "valid_until", "staff", "client_name", "client_phone",
        "client_address", "client_email", "duration", "scope", "terms",
        "internal_notes", "technician",
        "transport_qty", "transport_fee", "call_out", "call_out_fee",
        "discount_pct", "override_price", "vat_pct",
        "vehicle", "call_out_amount",
        "cost_price", "gross", "discount_amount", "net_selling", "selling_price",
        "profit", "markup_pct", "margin_pct", "margin_band", "vat_amount", "grand_total",
        "status",
    ]
    assert len(field_cols) == len(fields), (len(field_cols), len(fields))

    cur = conn.cursor()
    before_json = json.dumps(quote_row_to_dict(existing_row)) if existing_row else None
    status_changed = existing_row is not None and existing_row["status"] != new_status
    old_item_count = (
        conn.execute("SELECT COUNT(*) AS c FROM quote_items WHERE quote_id=?", (existing_id,)).fetchone()["c"]
        if existing_id else 0
    )

    if existing_id:
        set_clause = ",".join(f"{c}=?" for c in field_cols) + ",updated_at=?"
        cur.execute(f"UPDATE quotes SET {set_clause} WHERE id=?", fields + (ts, quote_id))
        cur.execute("DELETE FROM quote_items WHERE quote_id=?", (quote_id,))
        event_type = "update"
    else:
        seq = next_quote_seq(conn)
        quote_no = format_quote_no(seq)
        insert_cols = ["id", "quote_no", "quote_seq", "root_quote_id", "revision_number",
                       "prepared_by_email"] + field_cols + ["created_by_email", "created_at", "updated_at"]
        insert_values = (quote_id, quote_no, seq, quote_id, 1, created_by_email) + fields + (created_by_email, ts, ts)
        placeholders = ",".join(["?"] * len(insert_cols))
        cur.execute(f"INSERT INTO quotes ({','.join(insert_cols)}) VALUES ({placeholders})", insert_values)
        event_type = "create"

    for order, it in enumerate(items):
        cur.execute(
            "INSERT INTO quote_items (quote_id, kind, description, cost, sell, markup_pct, qty, sort_order, price_book_ref_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (quote_id, it.get("kind"), it.get("desc"), it.get("cost") or 0, it.get("sell") or 0,
             it.get("markupPct"), it.get("qty") or 1, order, it.get("priceBookRefId")),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    saved_items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order", (quote_id,)).fetchall()
    result = quote_row_to_dict(row, saved_items)
    update_summary = pricing_change_summary(existing_row, r, discount_pct, old_item_count, len(items)) if event_type == "update" else None
    write_audit_log(conn, quote_id, event_type, created_by_email, before_json, json.dumps(result), summary=update_summary)
    if status_changed:
        write_audit_log(
            conn, quote_id, "status_change", created_by_email,
            json.dumps({"status": existing_row["status"]}), json.dumps({"status": new_status}),
            summary=f"Auto-escalated to Approval Required (selling price AED {r['sellingPrice']:,.2f} over threshold)",
        )
    conn.close()
    return result


def _item_sell_positive(item):
    try:
        return float(item.get("sell") or 0) > 0
    except (TypeError, ValueError):
        return False


def pricing_change_summary(existing_row, r, new_discount_pct, old_item_count, new_item_count):
    """Human-readable summary of what actually changed price-wise on an edit,
    so the History panel shows more than just 'update' - the audit log needs
    to surface pricing changes, not just record that *something* changed."""
    if existing_row is None:
        return None
    parts = []
    old_sell, new_sell = existing_row["selling_price"] or 0, r["sellingPrice"] or 0
    if abs(old_sell - new_sell) > 0.01:
        parts.append(f"Selling Price AED {old_sell:,.2f} -> AED {new_sell:,.2f}")
    old_grand, new_grand = existing_row["grand_total"] or 0, r["grandTotal"] or 0
    if abs(old_grand - new_grand) > 0.01:
        parts.append(f"Grand Total AED {old_grand:,.2f} -> AED {new_grand:,.2f}")
    old_discount = existing_row["discount_pct"] or 0
    if abs(old_discount - new_discount_pct) > 0.01:
        parts.append(f"Discount {old_discount:.0f}% -> {new_discount_pct:.0f}%")
    if old_item_count != new_item_count:
        parts.append(f"Items {old_item_count} -> {new_item_count}")
    return "; ".join(parts) if parts else None


def write_audit_log(conn, quote_id, event_type, actor_email, before_json, after_json, summary=None):
    actor = conn.execute("SELECT id FROM users WHERE email=?", (actor_email,)).fetchone() if actor_email else None
    conn.execute(
        "INSERT INTO quote_audit_log (quote_id, event_type, actor_user_id, actor_email, at, summary, before_json, after_json) VALUES (?,?,?,?,?,?,?,?)",
        (quote_id, event_type, actor["id"] if actor else None, actor_email, now_iso(), summary, before_json, after_json),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Response helpers - every route handler below returns (status, headers,
# body_bytes); application() is the only place that actually calls
# start_response(). This is what makes the whole thing a plain WSGI app.
# ---------------------------------------------------------------------------

STATUS_TEXT = {
    200: "OK", 201: "Created", 302: "Found", 400: "Bad Request",
    401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    409: "Conflict", 500: "Internal Server Error",
}


def json_response(status, obj, extra_headers=None):
    body = json.dumps(obj).encode("utf-8")
    headers = [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body)))]
    headers.extend(extra_headers or [])
    return status, headers, body


def redirect_response(location, extra_headers=None):
    headers = [("Location", location), ("Content-Length", "0")]
    headers.extend(extra_headers or [])
    return 302, headers, b""


def not_found():
    return json_response(404, {"error": "not found"})


def unauthorized():
    return json_response(401, {"error": "not authenticated"})


def read_json_body(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0:
        return {}
    raw = environ["wsgi.input"].read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def serve_static(path):
    if path == "/":
        path = "/index.html"
    full = os.path.normpath(os.path.join(PUBLIC_DIR, path.lstrip("/")))
    if not full.startswith(PUBLIC_DIR):
        body = b"Forbidden"
        return 403, [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))], body
    if not os.path.isfile(full):
        body = b"Not Found"
        return 404, [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))], body
    ctype = "text/html"
    if full.endswith(".js"):
        ctype = "application/javascript"
    elif full.endswith(".css"):
        ctype = "text/css"
    elif full.endswith(".json"):
        ctype = "application/json"
    with open(full, "rb") as f:
        body = f.read()
    return 200, [("Content-Type", ctype + "; charset=utf-8"), ("Content-Length", str(len(body)))], body


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

def auth_google_start():
    if not GOOGLE_CLIENT_ID:
        return json_response(500, {"error": "Google sign-in isn't configured on this server yet (missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET). See README.txt."})
    state = create_oauth_state()
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return redirect_response(GOOGLE_AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params))


def auth_google_callback(query):
    def fail(message):
        return redirect_response("/login.html?error=" + urllib.parse.quote(message))

    if query.get("error"):
        return fail("Google sign-in was cancelled or failed.")
    if not consume_oauth_state(query.get("state")):
        return fail("Sign-in expired or was invalid - please try again.")
    code = query.get("code")
    if not code:
        return fail("Google didn't return an authorization code.")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return fail("Google sign-in isn't configured on this server yet.")

    token_body = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode("utf-8")
    try:
        token_req = urllib.request.Request(GOOGLE_TOKEN_ENDPOINT, data=token_body, method="POST")
        with urllib.request.urlopen(token_req, timeout=10) as resp:
            token_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError:
        return fail("Could not reach Google to complete sign-in.")
    except urllib.error.HTTPError as e:
        return fail("Google rejected the sign-in request (" + str(e.code) + ").")

    access_token = token_data.get("access_token")
    if not access_token:
        return fail("Google did not return an access token.")

    try:
        info_req = urllib.request.Request(GOOGLE_USERINFO_ENDPOINT, headers={"Authorization": "Bearer " + access_token})
        with urllib.request.urlopen(info_req, timeout=10) as resp:
            profile = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError):
        return fail("Could not verify your Google account.")

    email = auth.normalize_email(profile.get("email"))
    email_verified = profile.get("email_verified") in (True, "true")
    name = profile.get("name") or email

    if not email or not email_verified:
        return fail("Your Google account's email isn't verified.")
    if not auth.is_allowed_email(email):
        return fail("This account (" + email + ") isn't approved for access. Contact your administrator if you believe this is a mistake.")

    user_id = find_or_create_user(email, name)
    token = create_session(user_id)
    return redirect_response("/", extra_headers=[("Set-Cookie", session_cookie_header(token))])


def auth_logout(environ):
    token = parse_cookies(environ).get(SESSION_COOKIE_NAME)
    if token:
        conn = get_conn()
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        conn.close()
    return json_response(200, {"ok": True}, extra_headers=[("Set-Cookie", clear_cookie_header())])


def auth_me(environ):
    user = get_session_user(environ)
    if not user:
        return unauthorized()
    return json_response(200, {"email": user["email"], "role": user["role"]})


# ---------------------------------------------------------------------------
# Price book routes
# ---------------------------------------------------------------------------

def create_category(body):
    name = (body.get("name") or "").strip()
    if not name:
        return json_response(400, {"error": "name is required"})
    conn = get_conn()
    cat_id = slugify(name)
    if conn.execute("SELECT 1 FROM categories WHERE id=?", (cat_id,)).fetchone():
        cat_id = cat_id + "-" + uuid.uuid4().hex[:4]
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) AS m FROM categories").fetchone()["m"]
    conn.execute("INSERT INTO categories (id, name, sort_order) VALUES (?,?,?)", (cat_id, name, max_order + 1))
    conn.commit()
    conn.close()
    return json_response(201, {"id": cat_id, "name": name})


def update_category(cat_id, body):
    name = (body.get("name") or "").strip()
    if not name:
        return json_response(400, {"error": "name is required"})
    conn = get_conn()
    conn.execute("UPDATE categories SET name=? WHERE id=?", (name, cat_id))
    conn.commit()
    conn.close()
    return json_response(200, {"id": cat_id, "name": name})


def create_subcategory(cat_id, body):
    name = (body.get("name") or "").strip()
    if not name:
        return json_response(400, {"error": "name is required"})
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM categories WHERE id=?", (cat_id,)).fetchone():
        conn.close()
        return json_response(404, {"error": "category not found"})
    sub_id = cat_id + "." + slugify(name)
    if conn.execute("SELECT 1 FROM subcategories WHERE id=?", (sub_id,)).fetchone():
        sub_id = sub_id + "-" + uuid.uuid4().hex[:4]
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order),0) AS m FROM subcategories WHERE category_id=?", (cat_id,)).fetchone()["m"]
    conn.execute(
        "INSERT INTO subcategories (id, category_id, name, standard_price, amc_price, typical_hours, sort_order) VALUES (?,?,?,?,?,?,?)",
        (sub_id, cat_id, name, body.get("standardPrice"), body.get("amcPrice"), body.get("typicalHours"), max_order + 1),
    )
    conn.commit()
    conn.close()
    return json_response(201, {"id": sub_id, "categoryId": cat_id, "name": name})


def update_subcategory(sub_id, body):
    conn = get_conn()
    row = conn.execute("SELECT * FROM subcategories WHERE id=?", (sub_id,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "subcategory not found"})
    name = body.get("name", row["name"])
    standard_price = body.get("standardPrice", row["standard_price"])
    amc_price = body.get("amcPrice", row["amc_price"])
    typical_hours = body.get("typicalHours", row["typical_hours"])
    conn.execute(
        "UPDATE subcategories SET name=?, standard_price=?, amc_price=?, typical_hours=? WHERE id=?",
        (name, standard_price, amc_price, typical_hours, sub_id),
    )
    conn.commit()
    conn.close()
    return json_response(200, {"id": sub_id, "name": name, "standardPrice": standard_price, "amcPrice": amc_price, "typicalHours": typical_hours})


# ---------------------------------------------------------------------------
# Quotes routes
# ---------------------------------------------------------------------------

def list_quotes(query):
    conn = get_conn()
    sql = "SELECT * FROM quotes WHERE 1=1"
    params = []
    if query.get("from"):
        sql += " AND quote_date >= ?"
        params.append(query["from"])
    if query.get("to"):
        sql += " AND quote_date <= ?"
        params.append(query["to"])
    if query.get("status"):
        sql += " AND status = ?"
        params.append(query["status"])
    if query.get("preparedBy"):
        sql += " AND (prepared_by_email = ? OR created_by_email = ?)"
        params.extend([query["preparedBy"], query["preparedBy"]])
    if query.get("q"):
        sql += " AND (client_name LIKE ? OR quote_no LIKE ? OR client_address LIKE ?)"
        like = "%" + query["q"] + "%"
        params.extend([like, like, like])
    sql += " ORDER BY quote_date DESC, created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return json_response(200, {"quotes": [quote_row_to_dict(r) for r in rows]})


def quote_detail(quote_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "quote not found"})
    items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order", (quote_id,)).fetchall()
    conn.close()
    return json_response(200, quote_row_to_dict(row, items))


def quote_audit(quote_id):
    conn = get_conn()
    if not conn.execute("SELECT 1 FROM quotes WHERE id=?", (quote_id,)).fetchone():
        conn.close()
        return json_response(404, {"error": "quote not found"})
    rows = conn.execute(
        "SELECT event_type, actor_email, at, summary FROM quote_audit_log WHERE quote_id=? ORDER BY at ASC, id ASC",
        (quote_id,),
    ).fetchall()
    conn.close()
    return json_response(200, {"entries": [
        {"eventType": r["event_type"], "actorEmail": r["actor_email"], "at": r["at"], "summary": r["summary"]}
        for r in rows
    ]})


# ---------------------------------------------------------------------------
# Approval / revision state machine
#
#   Draft --submit_for_approval--> Approval Required --approve_and_send--> Sent to Jobber
#   Draft ----------send_to_jobber (only if sellingPrice <= threshold)----> Sent to Jobber
#   Approval Required --return_to_draft--> Draft
#   Sent to Jobber --revise--> a new linked Draft (source row never changes)
#   any status --duplicate--> a new unlinked Draft with its own quote_no
# ---------------------------------------------------------------------------

def quote_status_action(quote_id, action, user):
    conn = get_conn()
    row = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "quote not found"})

    status = row["status"]
    selling_price = row["selling_price"] or 0

    def transition(new_status):
        conn.execute("UPDATE quotes SET status=?, updated_at=? WHERE id=?", (new_status, now_iso(), quote_id))
        conn.commit()
        write_audit_log(conn, quote_id, "status_change", user["email"], json.dumps({"status": status}), json.dumps({"status": new_status}), summary=f"{status} -> {new_status}")

    if action == "submit_for_approval":
        if status != "Draft":
            conn.close()
            return json_response(400, {"error": "Only a Draft quote can be submitted for approval."})
        transition("Approval Required")
    elif action == "send_to_jobber":
        if status != "Draft":
            conn.close()
            return json_response(400, {"error": "Only a Draft quote can be sent directly to Jobber."})
        if selling_price > APPROVAL_THRESHOLD_AED:
            conn.close()
            return json_response(400, {"error": f"Quotes over AED {APPROVAL_THRESHOLD_AED:,} require approval - submit for approval instead."})
        transition("Sent to Jobber")
        write_audit_log(conn, quote_id, "send_to_jobber", user["email"], None, None)
    elif action == "approve_and_send":
        if not is_admin(user):
            conn.close()
            return forbidden()
        if status != "Approval Required":
            conn.close()
            return json_response(400, {"error": "Only a quote awaiting approval can be approved."})
        transition("Sent to Jobber")
        write_audit_log(conn, quote_id, "approve", user["email"], None, None)
        write_audit_log(conn, quote_id, "send_to_jobber", user["email"], None, None)
    elif action == "return_to_draft":
        if status != "Approval Required":
            conn.close()
            return json_response(400, {"error": "Only a quote awaiting approval can be returned to Draft."})
        if not (is_admin(user) or user["email"] == row["created_by_email"]):
            conn.close()
            return forbidden()
        transition("Draft")
    else:
        conn.close()
        return json_response(400, {"error": "Unknown action."})

    updated = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order", (quote_id,)).fetchall()
    conn.close()
    return json_response(200, quote_row_to_dict(updated, items))


def quote_revise(quote_id, user):
    conn = get_conn()
    src = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not src:
        conn.close()
        return json_response(404, {"error": "quote not found"})
    if src["status"] != "Sent to Jobber":
        conn.close()
        return json_response(400, {"error": "Only a quote that has been Sent to Jobber can be revised."})
    src_items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order", (quote_id,)).fetchall()

    max_rev = conn.execute("SELECT COALESCE(MAX(revision_number),1) AS m FROM quotes WHERE root_quote_id=?", (src["root_quote_id"],)).fetchone()["m"]
    new_id = uuid.uuid4().hex
    ts = now_iso()
    cols = [k for k in src.keys() if k not in ("id",)]
    values = [src[c] for c in cols]
    cols += ["id"]
    values += [new_id]
    placeholders = ",".join(["?"] * len(cols))
    conn.execute(f"INSERT INTO quotes ({','.join(cols)}) VALUES ({placeholders})", values)
    conn.execute(
        "UPDATE quotes SET status='Draft', parent_quote_id=?, revision_number=?, created_at=?, updated_at=?, created_by_email=?, prepared_by_email=? WHERE id=?",
        (quote_id, max_rev + 1, ts, ts, user["email"], user["email"], new_id),
    )
    for it in src_items:
        conn.execute(
            "INSERT INTO quote_items (quote_id, kind, description, cost, sell, markup_pct, qty, sort_order, price_book_ref_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id, it["kind"], it["description"], it["cost"], it["sell"], it["markup_pct"], it["qty"], it["sort_order"], it["price_book_ref_id"]),
        )
    conn.commit()
    write_audit_log(conn, new_id, "revision_created", user["email"], None, None, summary="Revised from " + quote_id)
    row = conn.execute("SELECT * FROM quotes WHERE id=?", (new_id,)).fetchone()
    items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order", (new_id,)).fetchall()
    conn.close()
    return json_response(201, quote_row_to_dict(row, items))


def quote_duplicate(quote_id, user):
    conn = get_conn()
    src = conn.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    if not src:
        conn.close()
        return json_response(404, {"error": "quote not found"})
    src_items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order", (quote_id,)).fetchall()

    new_id = uuid.uuid4().hex
    ts = now_iso()
    seq = next_quote_seq(conn)
    cols = [k for k in src.keys() if k not in ("id",)]
    values = [src[c] for c in cols]
    cols += ["id"]
    values += [new_id]
    placeholders = ",".join(["?"] * len(cols))
    conn.execute(f"INSERT INTO quotes ({','.join(cols)}) VALUES ({placeholders})", values)
    conn.execute(
        "UPDATE quotes SET status='Draft', parent_quote_id=NULL, root_quote_id=?, revision_number=1, quote_seq=?, quote_no=?, "
        "created_at=?, updated_at=?, created_by_email=?, prepared_by_email=? WHERE id=?",
        (new_id, seq, format_quote_no(seq), ts, ts, user["email"], user["email"], new_id),
    )
    for it in src_items:
        conn.execute(
            "INSERT INTO quote_items (quote_id, kind, description, cost, sell, markup_pct, qty, sort_order, price_book_ref_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (new_id, it["kind"], it["description"], it["cost"], it["sell"], it["markup_pct"], it["qty"], it["sort_order"], it["price_book_ref_id"]),
        )
    conn.commit()
    write_audit_log(conn, new_id, "duplicated", user["email"], None, None, summary="Duplicated from " + quote_id)
    row = conn.execute("SELECT * FROM quotes WHERE id=?", (new_id,)).fetchone()
    items = conn.execute("SELECT * FROM quote_items WHERE quote_id=? ORDER BY sort_order", (new_id,)).fetchall()
    conn.close()
    return json_response(201, quote_row_to_dict(row, items))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def dashboard_data():
    conn = get_conn()
    approval_rows = conn.execute(
        "SELECT id, quote_no, client_name, selling_price, discount_pct, prepared_by_email, created_by_email, created_at "
        "FROM quotes WHERE status='Approval Required' ORDER BY created_at ASC"
    ).fetchall()

    def approval_reason(r):
        reasons = []
        if r["discount_pct"] and r["discount_pct"] > 0:
            reasons.append(f"Discount {r['discount_pct']:.0f}%")
        if r["selling_price"] and r["selling_price"] > APPROVAL_THRESHOLD_AED:
            reasons.append(f"Over AED {APPROVAL_THRESHOLD_AED:,} threshold")
        return ", ".join(reasons) or "Submitted for approval"

    approval_required = [{
        "id": r["id"], "quoteNo": r["quote_no"], "client": r["client_name"],
        "reason": approval_reason(r),
        "value": r["selling_price"], "preparedBy": r["prepared_by_email"] or r["created_by_email"],
    } for r in approval_rows]

    stats_row = conn.execute(
        "SELECT AVG(markup_pct) AS avg_markup, AVG(margin_pct) AS avg_margin, "
        "SUM(CASE WHEN status='Approval Required' THEN 1 ELSE 0 END) AS awaiting "
        "FROM quotes WHERE status IN ('Approval Required','Sent to Jobber')"
    ).fetchone()
    item_count = (
        conn.execute("SELECT COUNT(*) AS c FROM pb_materials").fetchone()["c"]
        + conn.execute("SELECT COUNT(*) AS c FROM pb_labour").fetchone()["c"]
        + conn.execute("SELECT COUNT(*) AS c FROM pb_fixed_services").fetchone()["c"]
    )

    month_prefix = time.strftime("%Y-%m")
    quotes_this_month = conn.execute(
        "SELECT COUNT(*) AS c FROM quotes WHERE created_at LIKE ?", (month_prefix + "%",)
    ).fetchone()["c"]
    sent_to_jobber_this_month = conn.execute(
        "SELECT COUNT(*) AS c FROM quotes WHERE status='Sent to Jobber' AND created_at LIKE ?", (month_prefix + "%",)
    ).fetchone()["c"]
    drafts_pending = conn.execute("SELECT COUNT(*) AS c FROM quotes WHERE status='Draft'").fetchone()["c"]

    conn.close()
    return json_response(200, {
        "approvalRequired": approval_required,
        "stats": {
            "avgMarkupPct": (stats_row["avg_markup"] or 0) * 100,
            "avgGrossMarginPct": (stats_row["avg_margin"] or 0) * 100,
            "quotesAwaitingApprovalCount": stats_row["awaiting"] or 0,
            "priceBookItemCount": item_count,
            "quotesThisMonth": quotes_this_month,
            "sentToJobberThisMonth": sent_to_jobber_this_month,
            "draftsPending": drafts_pending,
        },
    })


# ---------------------------------------------------------------------------
# Price Book v2 - Materials / Labour / Fixed Services (separate from the
# original categories/subcategories tables, which remain untouched and keep
# serving the unrelated Guided Wizard taxonomy).
# ---------------------------------------------------------------------------

def pb_material_to_dict(r):
    return {"id": r["id"], "category": r["category"], "itemName": r["item_name"], "brand": r["brand"],
            "modelOrSize": r["model_or_size"], "unit": r["unit"], "cost": r["cost"], "defaultSell": r["default_sell"],
            "supplier": r["supplier"], "lastUpdated": r["last_updated"]}


def pb_labour_to_dict(r):
    return {"id": r["id"], "roleName": r["role_name"], "labourType": r["labour_type"], "cost": r["cost"],
            "defaultSell": r["default_sell"], "unit": r["unit"], "lastUpdated": r["last_updated"]}


def pb_fixed_service_to_dict(r):
    return {"id": r["id"], "serviceName": r["service_name"], "category": r["category"],
            "estimatedCost": r["estimated_cost"], "standardSell": r["standard_sell"], "lastUpdated": r["last_updated"]}


def list_pb_materials(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_materials WHERE 1=1"
    params = []
    q = query.get("q")
    if q:
        like = "%" + q + "%"
        sql += " AND (item_name LIKE ? OR category LIKE ? OR brand LIKE ? OR model_or_size LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY category, item_name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return json_response(200, {"materials": [pb_material_to_dict(r) for r in rows]})


def create_pb_material(body):
    conn = get_conn()
    mid = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        "INSERT INTO pb_materials (id, category, item_name, brand, model_or_size, unit, cost, default_sell, supplier, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (mid, body.get("category"), body.get("itemName") or "Untitled", body.get("brand"), body.get("modelOrSize"),
         body.get("unit"), body.get("cost"), body.get("defaultSell"), body.get("supplier"), ts, ts),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_materials WHERE id=?", (mid,)).fetchone()
    conn.close()
    return json_response(201, pb_material_to_dict(row))


def update_pb_material(mid, body):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pb_materials WHERE id=?", (mid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    fields = {
        "category": body.get("category", row["category"]), "item_name": body.get("itemName", row["item_name"]),
        "brand": body.get("brand", row["brand"]), "model_or_size": body.get("modelOrSize", row["model_or_size"]),
        "unit": body.get("unit", row["unit"]), "cost": body.get("cost", row["cost"]),
        "default_sell": body.get("defaultSell", row["default_sell"]), "supplier": body.get("supplier", row["supplier"]),
    }
    conn.execute(
        "UPDATE pb_materials SET category=?, item_name=?, brand=?, model_or_size=?, unit=?, cost=?, default_sell=?, supplier=?, last_updated=? WHERE id=?",
        (fields["category"], fields["item_name"], fields["brand"], fields["model_or_size"], fields["unit"],
         fields["cost"], fields["default_sell"], fields["supplier"], now_iso(), mid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_materials WHERE id=?", (mid,)).fetchone()
    conn.close()
    return json_response(200, pb_material_to_dict(row))


def list_pb_labour(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_labour WHERE 1=1"
    params = []
    if query.get("q"):
        like = "%" + query["q"] + "%"
        sql += " AND role_name LIKE ?"
        params.append(like)
    sql += " ORDER BY role_name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return json_response(200, {"labour": [pb_labour_to_dict(r) for r in rows]})


def create_pb_labour(body):
    conn = get_conn()
    lid = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        "INSERT INTO pb_labour (id, role_name, labour_type, cost, default_sell, unit, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (lid, body.get("roleName") or "Untitled", body.get("labourType") or "staff", body.get("cost"),
         body.get("defaultSell"), body.get("unit"), ts, ts),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_labour WHERE id=?", (lid,)).fetchone()
    conn.close()
    return json_response(201, pb_labour_to_dict(row))


def update_pb_labour(lid, body):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pb_labour WHERE id=?", (lid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    conn.execute(
        "UPDATE pb_labour SET role_name=?, labour_type=?, cost=?, default_sell=?, unit=?, last_updated=? WHERE id=?",
        (body.get("roleName", row["role_name"]), body.get("labourType", row["labour_type"]),
         body.get("cost", row["cost"]), body.get("defaultSell", row["default_sell"]),
         body.get("unit", row["unit"]), now_iso(), lid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_labour WHERE id=?", (lid,)).fetchone()
    conn.close()
    return json_response(200, pb_labour_to_dict(row))


def list_pb_fixed_services(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_fixed_services WHERE 1=1"
    params = []
    if query.get("q"):
        like = "%" + query["q"] + "%"
        sql += " AND (service_name LIKE ? OR category LIKE ?)"
        params.extend([like, like])
    sql += " ORDER BY category, service_name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return json_response(200, {"fixedServices": [pb_fixed_service_to_dict(r) for r in rows]})


def create_pb_fixed_service(body):
    conn = get_conn()
    fid = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        "INSERT INTO pb_fixed_services (id, service_name, category, estimated_cost, standard_sell, last_updated, created_at) VALUES (?,?,?,?,?,?,?)",
        (fid, body.get("serviceName") or "Untitled", body.get("category"), body.get("estimatedCost"), body.get("standardSell"), ts, ts),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_fixed_services WHERE id=?", (fid,)).fetchone()
    conn.close()
    return json_response(201, pb_fixed_service_to_dict(row))


def update_pb_fixed_service(fid, body):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pb_fixed_services WHERE id=?", (fid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    conn.execute(
        "UPDATE pb_fixed_services SET service_name=?, category=?, estimated_cost=?, standard_sell=?, last_updated=? WHERE id=?",
        (body.get("serviceName", row["service_name"]), body.get("category", row["category"]),
         body.get("estimatedCost", row["estimated_cost"]), body.get("standardSell", row["standard_sell"]), now_iso(), fid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_fixed_services WHERE id=?", (fid,)).fetchone()
    conn.close()
    return json_response(200, pb_fixed_service_to_dict(row))


# ---------------------------------------------------------------------------
# Quote Templates
# ---------------------------------------------------------------------------

def template_to_dict(row, items=None):
    d = {"id": row["id"], "name": row["name"], "description": row["description"], "createdBy": row["created_by_email"]}
    if items is not None:
        d["items"] = [{"kind": i["kind"], "desc": i["description"], "cost": i["default_cost"],
                        "sell": i["default_sell"], "qty": i["default_qty"]} for i in items]
    return d


def list_templates(query):
    conn = get_conn()
    sql = "SELECT * FROM quote_templates WHERE 1=1"
    params = []
    if query.get("q"):
        like = "%" + query["q"] + "%"
        sql += " AND name LIKE ?"
        params.append(like)
    sql += " ORDER BY name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return json_response(200, {"templates": [template_to_dict(r) for r in rows]})


def template_detail(tid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM quote_templates WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    items = conn.execute("SELECT * FROM quote_template_items WHERE template_id=? ORDER BY sort_order", (tid,)).fetchall()
    conn.close()
    return json_response(200, template_to_dict(row, items))


def create_template(body, user):
    name = (body.get("name") or "").strip()
    if not name:
        return json_response(400, {"error": "name is required"})
    conn = get_conn()
    tid = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        "INSERT INTO quote_templates (id, name, description, created_by_email, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (tid, name, body.get("description"), user["email"], ts, ts),
    )
    for order, it in enumerate(body.get("items") or []):
        conn.execute(
            "INSERT INTO quote_template_items (template_id, kind, description, default_cost, default_sell, default_qty, sort_order) VALUES (?,?,?,?,?,?,?)",
            (tid, it.get("kind"), it.get("desc"), it.get("cost"), it.get("sell"), it.get("qty") or 1, order),
        )
    conn.commit()
    conn.close()
    return template_detail(tid)


def update_template(tid, body):
    conn = get_conn()
    row = conn.execute("SELECT * FROM quote_templates WHERE id=?", (tid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    conn.execute(
        "UPDATE quote_templates SET name=?, description=?, updated_at=? WHERE id=?",
        (body.get("name", row["name"]), body.get("description", row["description"]), now_iso(), tid),
    )
    if body.get("items") is not None:
        conn.execute("DELETE FROM quote_template_items WHERE template_id=?", (tid,))
        for order, it in enumerate(body["items"]):
            conn.execute(
                "INSERT INTO quote_template_items (template_id, kind, description, default_cost, default_sell, default_qty, sort_order) VALUES (?,?,?,?,?,?,?)",
                (tid, it.get("kind"), it.get("desc"), it.get("cost"), it.get("sell"), it.get("qty") or 1, order),
            )
    conn.commit()
    conn.close()
    return template_detail(tid)


def delete_template(tid):
    conn = get_conn()
    conn.execute("DELETE FROM quote_templates WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True})


# ---------------------------------------------------------------------------
# User role management (admin only)
# ---------------------------------------------------------------------------

def list_users():
    conn = get_conn()
    rows = conn.execute("SELECT id, email, name, role, last_login_at FROM users ORDER BY email").fetchall()
    conn.close()
    return json_response(200, {"users": [dict(r) for r in rows]})


# Creates a real users row up front, before that person has ever signed in.
# find_or_create_user() looks a person up by email on their first Google
# sign-in, so if a row already exists here, sign-in just fills in their name
# and last_login_at and leaves the role alone - it never overwrites it.
def create_user(body, actor_email):
    email = (body.get("email") or "").strip().lower()
    name = (body.get("name") or "").strip() or None
    role = body.get("role")
    if role not in ("staff", "admin"):
        return json_response(400, {"error": "role must be 'staff' or 'admin'"})
    if not email or not EMAIL_RE.match(email):
        return json_response(400, {"error": "a valid email is required"})
    if not auth.is_allowed_email(email):
        return json_response(400, {"error": f"email domain not allowed to sign in (allowed: {', '.join(auth.ALLOWED_DOMAINS)})"})
    conn = get_conn()
    if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        conn.close()
        return json_response(409, {"error": "a user with that email already exists"})
    uid = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        "INSERT INTO users (id, email, name, role, created_at) VALUES (?,?,?,?,?)",
        (uid, email, name, role, ts),
    )
    if role == "admin":
        conn.execute("INSERT OR IGNORE INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?)", (email, actor_email, ts))
    conn.commit()
    conn.close()
    return json_response(201, {"id": uid, "email": email, "name": name, "role": role, "last_login_at": None})


def delete_user(uid, actor_user):
    if uid == actor_user["id"]:
        return json_response(400, {"error": "you cannot remove your own account"})
    conn = get_conn()
    row = conn.execute("SELECT id FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "user not found"})
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True})


def update_user_role(uid, body, actor_email):
    role = body.get("role")
    if role not in ("staff", "admin"):
        return json_response(400, {"error": "role must be 'staff' or 'admin'"})
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "user not found"})
    conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    if role == "admin":
        conn.execute("INSERT OR IGNORE INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?)", (row["email"], actor_email, now_iso()))
    conn.commit()
    conn.close()
    return json_response(200, {"id": uid, "role": role})


# admin_allowlist doubles as a pre-authorization list: adding an email here
# grants admin immediately to a matching EXISTING user, and also guarantees
# admin role the first time that email ever signs in (see find_or_create_user)
# - this is how an admin can "add" a teammate who hasn't logged in yet.
def list_admin_allowlist():
    conn = get_conn()
    rows = conn.execute("SELECT email, added_by_email, added_at FROM admin_allowlist ORDER BY added_at DESC").fetchall()
    conn.close()
    return json_response(200, {"allowlist": [dict(r) for r in rows]})


def add_admin_allowlist_email(body, actor_email):
    email = (body.get("email") or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        return json_response(400, {"error": "a valid email is required"})
    if not auth.is_allowed_email(email):
        return json_response(400, {"error": f"email domain not allowed to sign in (allowed: {', '.join(auth.ALLOWED_DOMAINS)})"})
    conn = get_conn()
    conn.execute("INSERT OR IGNORE INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?)", (email, actor_email, now_iso()))
    conn.execute("UPDATE users SET role='admin' WHERE email=? AND role != 'admin'", (email,))
    conn.commit()
    conn.close()
    return json_response(200, {"email": email})


# ---------------------------------------------------------------------------
# Routing - one function per HTTP method, dispatching on path. Every /api/
# route requires a valid session EXCEPT /api/auth/* (you have to be able to
# reach those while logged out, or no one could log in).
# ---------------------------------------------------------------------------

def handle_get(environ, path, query):
    if path == "/api/auth/me":
        return auth_me(environ)
    if path == "/api/auth/google/start":
        return auth_google_start()
    if path == "/api/auth/google/callback":
        return auth_google_callback(query)

    if path.startswith("/api/"):
        user = get_session_user(environ)
        if not user:
            return unauthorized()
        if path == "/api/pricebook":
            return json_response(200, {"categories": fetch_pricebook()})
        if path == "/api/settings":
            return json_response(200, get_effective_settings())
        if path == "/api/dashboard":
            return dashboard_data()
        if path == "/api/quotes":
            return list_quotes(query)
        m = re.match(r"^/api/quotes/([\w-]+)$", path)
        if m:
            return quote_detail(m.group(1))
        m = re.match(r"^/api/quotes/([\w-]+)/audit$", path)
        if m:
            return quote_audit(m.group(1))
        if path == "/api/pricebook/materials":
            return list_pb_materials(query)
        if path == "/api/pricebook/labour":
            return list_pb_labour(query)
        if path == "/api/pricebook/fixed-services":
            return list_pb_fixed_services(query)
        if path == "/api/templates":
            return list_templates(query)
        m = re.match(r"^/api/templates/([\w-]+)$", path)
        if m:
            return template_detail(m.group(1))
        if path == "/api/users":
            if not is_admin(user):
                return forbidden()
            return list_users()
        if path == "/api/admin-allowlist":
            if not is_admin(user):
                return forbidden()
            return list_admin_allowlist()
        return not_found()

    return serve_static(path)


def handle_post(environ, path, body):
    if path == "/api/auth/logout":
        return auth_logout(environ)

    if path.startswith("/api/"):
        user = get_session_user(environ)
        if not user:
            return unauthorized()

        if path == "/api/pricebook/categories":
            if not is_admin(user):
                return forbidden()
            return create_category(body)
        m = re.match(r"^/api/pricebook/categories/([\w.-]+)/subcategories$", path)
        if m:
            if not is_admin(user):
                return forbidden()
            return create_subcategory(m.group(1), body)
        if path == "/api/pricebook/materials":
            if not is_admin(user):
                return forbidden()
            return create_pb_material(body)
        if path == "/api/pricebook/labour":
            if not is_admin(user):
                return forbidden()
            return create_pb_labour(body)
        if path == "/api/pricebook/fixed-services":
            if not is_admin(user):
                return forbidden()
            return create_pb_fixed_service(body)
        if path == "/api/templates":
            if not is_admin(user):
                return forbidden()
            return create_template(body, user)
        if path == "/api/quotes":
            try:
                quote = save_quote(body, created_by_email=user["email"])
            except SaveError as e:
                return json_response(e.status, {"error": e.message})
            return json_response(201, quote)
        m = re.match(r"^/api/quotes/([\w-]+)/status$", path)
        if m:
            return quote_status_action(m.group(1), body.get("action"), user)
        m = re.match(r"^/api/quotes/([\w-]+)/revise$", path)
        if m:
            return quote_revise(m.group(1), user)
        m = re.match(r"^/api/quotes/([\w-]+)/duplicate$", path)
        if m:
            return quote_duplicate(m.group(1), user)
        if path == "/api/admin-allowlist":
            if not is_admin(user):
                return forbidden()
            return add_admin_allowlist_email(body, user["email"])
        if path == "/api/users":
            if not is_admin(user):
                return forbidden()
            return create_user(body, user["email"])
        return not_found()

    return not_found()


def handle_put(environ, path, body):
    if not path.startswith("/api/"):
        return not_found()
    user = get_session_user(environ)
    if not user:
        return unauthorized()

    m = re.match(r"^/api/pricebook/subcategories/([\w.-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_subcategory(m.group(1), body)

    m = re.match(r"^/api/pricebook/categories/([\w.-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_category(m.group(1), body)

    m = re.match(r"^/api/pricebook/materials/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_pb_material(m.group(1), body)

    m = re.match(r"^/api/pricebook/labour/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_pb_labour(m.group(1), body)

    m = re.match(r"^/api/pricebook/fixed-services/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_pb_fixed_service(m.group(1), body)

    m = re.match(r"^/api/templates/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_template(m.group(1), body)

    m = re.match(r"^/api/users/([\w-]+)/role$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_user_role(m.group(1), body, user["email"])

    if path == "/api/settings":
        if not is_admin(user):
            return forbidden()
        return update_settings(body, user["email"])

    m = re.match(r"^/api/quotes/([\w-]+)$", path)
    if m:
        try:
            quote = save_quote(body, existing_id=m.group(1))
        except SaveError as e:
            return json_response(e.status, {"error": e.message})
        return json_response(200, quote)

    return not_found()


def handle_delete(environ, path):
    if not path.startswith("/api/"):
        return not_found()
    user = get_session_user(environ)
    if not user:
        return unauthorized()

    m = re.match(r"^/api/pricebook/categories/([\w.-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        conn = get_conn()
        conn.execute("DELETE FROM categories WHERE id=?", (m.group(1),))
        conn.commit()
        conn.close()
        return json_response(200, {"ok": True})

    m = re.match(r"^/api/pricebook/subcategories/([\w.-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        conn = get_conn()
        conn.execute("DELETE FROM subcategories WHERE id=?", (m.group(1),))
        conn.commit()
        conn.close()
        return json_response(200, {"ok": True})

    m = re.match(r"^/api/templates/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return delete_template(m.group(1))

    m = re.match(r"^/api/users/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return delete_user(m.group(1), user)

    m = re.match(r"^/api/quotes/([\w-]+)$", path)
    if m:
        conn = get_conn()
        row = conn.execute("SELECT status FROM quotes WHERE id=?", (m.group(1),)).fetchone()
        if not row:
            conn.close()
            return json_response(404, {"error": "quote not found"})
        if row["status"] != "Draft":
            conn.close()
            return json_response(409, {"error": "Only a Draft quote can be deleted - the database is the permanent record once a quote moves past Draft."})
        conn.execute("DELETE FROM quotes WHERE id=?", (m.group(1),))
        conn.commit()
        conn.close()
        return json_response(200, {"ok": True})

    return not_found()


# ---------------------------------------------------------------------------
# The WSGI entry point. This is the only function Passenger (or wsgiref's
# dev server) ever calls directly.
# ---------------------------------------------------------------------------

def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/") or "/"
    query = {k: v[0] for k, v in urllib.parse.parse_qs(environ.get("QUERY_STRING", "")).items()}

    try:
        if method == "GET":
            status, headers, body = handle_get(environ, path, query)
        elif method == "POST":
            status, headers, body = handle_post(environ, path, read_json_body(environ))
        elif method == "PUT":
            status, headers, body = handle_put(environ, path, read_json_body(environ))
        elif method == "DELETE":
            status, headers, body = handle_delete(environ, path)
        else:
            status, headers, body = json_response(405, {"error": "method not allowed"})
    except Exception:
        status, headers, body = json_response(500, {"error": "internal server error"})

    start_response(f"{status} {STATUS_TEXT.get(status, 'OK')}", headers)
    return [body]


# ---------------------------------------------------------------------------
# Local dev server. NOT used in production on Passenger - passenger_wsgi.py
# imports `application` directly and Passenger runs its own server around it.
# This just gives you the same "python server.py" experience as before.
# ---------------------------------------------------------------------------

class ThreadingWSGIServer(socketserver.ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietWSGIRequestHandler(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        pass


def main():
    init_db()
    httpd = make_server(HOST, PORT, application, server_class=ThreadingWSGIServer, handler_class=QuietWSGIRequestHandler)
    display_host = "127.0.0.1" if HOST == "0.0.0.0" else HOST
    url = f"http://{display_host}:{PORT}"
    print(f"Handyman Quote Builder running at {url} (bound to {HOST}:{PORT})")
    print(f"Sign-in restricted to: {', '.join('@' + d for d in auth.ALLOWED_DOMAINS)}")
    if FORCE_SECURE_COOKIE:
        print("FORCE_SECURE_COOKIE=1 - the session cookie will only be sent over HTTPS.")
    print("Press Ctrl+C to stop.")
    if HOST in ("127.0.0.1", "localhost"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
        httpd.shutdown()


if __name__ == "__main__":
    main()
