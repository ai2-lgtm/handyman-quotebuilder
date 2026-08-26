# -*- coding: utf-8 -*-
"""
Migration safety check - NOT part of the regular test suite (not named
test_*.py's usual pytest-style, run manually): builds a SQLite file matching
the *pre-v2* production schema (the one already live on Railway, with old
markup% columns and no status/quote_no-sequence/items-cost-sell columns),
seeds it with a couple of realistic quotes, then runs the real init_db()
against it and asserts the backfill did the right thing - this is the only
way to gain confidence on production-data safety without touching production.
"""
import os
import shutil
import sqlite3
import sys

TEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_migration_test_data")


def build_old_schema_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE categories (id TEXT PRIMARY KEY, name TEXT NOT NULL, sort_order INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE subcategories (
            id TEXT PRIMARY KEY, category_id TEXT NOT NULL, name TEXT NOT NULL,
            standard_price REAL, amc_price REAL, typical_hours REAL, sort_order INTEGER NOT NULL DEFAULT 0
        );
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
            created_at TEXT, updated_at TEXT, created_by_email TEXT
        );
        CREATE TABLE quote_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('material','labour','subcontractor','other')),
            description TEXT, price REAL, qty REAL, sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, created_at TEXT, last_login_at TEXT);
        CREATE TABLE sessions (token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT, expires_at TEXT);
        CREATE TABLE oauth_states (state TEXT PRIMARY KEY, created_at TEXT);
    """)

    # Two realistic pre-existing quotes with real markup percentages and a
    # known persisted grand_total, so we can assert the rebuilt quote_items'
    # reconstructed cost/sell lines sum back to the same totals.
    conn.execute(
        "INSERT INTO quotes (id, quote_no, quote_date, client_name, client_address, client_email, "
        "labour_margin_pct, markup_material_pct, markup_labour_pct, markup_subcontractor_pct, vat_pct, "
        "transport_qty, transport_fee, call_out, call_out_fee, discount_pct, override_price, "
        "material_cost, labour_cost, sub_cost, other_cost, vehicle, call_out_amount, "
        "material_sell, labour_sell, sub_sell, cost_price, gross, discount_amount, net_selling, "
        "selling_price, profit, margin_pct, margin_band, vat_amount, grand_total, "
        "created_at, updated_at, created_by_email) VALUES ("
        "'q1','HM-Q-20260101-11','2026-01-01','Jaycee Wolfssinkel','Villa 12, JVC','jaycee@example.com',"
        "50,50,0,15,5,"
        "1,125,1,150,0,0,"
        "500,450,0,0,125,150,"
        "750,900,0,1075,1925,0,1925,"
        "1925,850,0.4416,'ON TARGET',96.25,2021.25,"
        "'2026-01-01T10:00:00','2026-01-01T10:00:00','tech@handyman.ae')"
    )
    conn.execute("INSERT INTO quote_items (quote_id, kind, description, price, qty, sort_order) VALUES ('q1','material','Paint',500,1,0)")
    conn.execute("INSERT INTO quote_items (quote_id, kind, description, price, qty, sort_order) VALUES ('q1','labour','Painter',900,1,1)")

    conn.execute(
        "INSERT INTO quotes (id, quote_no, quote_date, client_name, client_address, client_email, "
        "labour_margin_pct, markup_material_pct, markup_labour_pct, markup_subcontractor_pct, vat_pct, "
        "transport_qty, transport_fee, call_out, call_out_fee, discount_pct, override_price, "
        "material_cost, labour_cost, sub_cost, other_cost, vehicle, call_out_amount, "
        "material_sell, labour_sell, sub_sell, cost_price, gross, discount_amount, net_selling, "
        "selling_price, profit, margin_pct, margin_band, vat_amount, grand_total, "
        "created_at, updated_at, created_by_email) VALUES ("
        "'q2','HM-Q-20260105-22','2026-01-05','Second Client','Apt 4, Marina','second@example.com',"
        "50,50,0,15,5,"
        "0,125,0,150,0,0,"
        "0,0,1000,0,0,0,"
        "0,0,1150,1000,1150,0,1150,"
        "1150,150,0.1304,'CRITICAL',57.5,1207.5,"
        "'2026-01-05T09:00:00','2026-01-05T09:00:00','tech2@handyman.ae')"
    )
    conn.execute("INSERT INTO quote_items (quote_id, kind, description, price, qty, sort_order) VALUES ('q2','subcontractor','Mason',1000,1,0)")

    conn.execute("INSERT INTO users (id, email, name, created_at, last_login_at) VALUES ('u1','tech@handyman.ae','Tech','2026-01-01T09:00:00','2026-01-01T09:00:00')")
    conn.commit()
    conn.close()


def main():
    if os.path.isdir(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR)
    db_path = os.path.join(TEST_DIR, "handyman.db")
    build_old_schema_db(db_path)

    os.environ["DATA_DIR"] = TEST_DIR
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import importlib
    import server
    importlib.reload(server)
    server.DATA_DIR = TEST_DIR
    server.DB_PATH = db_path

    server.init_db()

    conn = server.get_conn()
    failures = []

    q1 = conn.execute("SELECT * FROM quotes WHERE id='q1'").fetchone()
    q2 = conn.execute("SELECT * FROM quotes WHERE id='q2'").fetchone()

    for q, label in ((q1, "q1"), (q2, "q2")):
        if q["status"] != "Sent to Jobber":
            failures.append(f"{label}: expected status 'Sent to Jobber', got {q['status']!r}")
        if not q["quote_no"] or not q["quote_no"].startswith("QB-"):
            failures.append(f"{label}: expected a QB-###### quote_no, got {q['quote_no']!r}")
        if q["root_quote_id"] != q["id"]:
            failures.append(f"{label}: root_quote_id should equal id")

    if q1["quote_seq"] >= q2["quote_seq"]:
        failures.append("q1 (created first) should have a lower quote_seq than q2")

    items1 = conn.execute("SELECT * FROM quote_items WHERE quote_id='q1' ORDER BY sort_order").fetchall()
    items2 = conn.execute("SELECT * FROM quote_items WHERE quote_id='q2' ORDER BY sort_order").fetchall()

    if len(items1) != 2 or len(items2) != 1:
        failures.append(f"expected 2 items for q1 and 1 for q2, got {len(items1)} and {len(items2)}")

    material_item = next((i for i in items1 if i["kind"] == "material"), None)
    labour_item = next((i for i in items1 if i["kind"] == "staff_labour"), None)
    if not material_item or material_item["cost"] != 500 or abs(material_item["sell"] - 750) > 0.01:
        failures.append(f"q1 material line reconstructed wrong: {dict(material_item) if material_item else None}")
    if not labour_item or abs(labour_item["cost"] - 450) > 0.01 or labour_item["sell"] != 900:
        failures.append(f"q1 labour line reconstructed wrong (expected cost=450 [900*(1-50%)], sell=900): {dict(labour_item) if labour_item else None}")

    outside_item = next((i for i in items2 if i["kind"] == "outside_labour"), None)
    if not outside_item or outside_item["cost"] != 1000 or abs(outside_item["sell"] - 1150) > 0.01:
        failures.append(f"q2 subcontractor->outside_labour line reconstructed wrong: {dict(outside_item) if outside_item else None}")

    reconstructed_cost_q1 = sum((i["cost"] or 0) * (i["qty"] or 0) for i in items1) + (q1["vehicle"] or 0)
    if abs(reconstructed_cost_q1 - q1["cost_price"]) > 0.5:
        failures.append(f"q1 reconstructed cost {reconstructed_cost_q1} doesn't match persisted cost_price {q1['cost_price']}")

    admin_row = conn.execute("SELECT role FROM users WHERE email='ai2@legacygroup.me'").fetchone()
    conn.close()

    if failures:
        print("MIGRATION SAFETY CHECK: FAILED")
        for f in failures:
            print(" -", f)
        sys.exit(1)
    else:
        print("MIGRATION SAFETY CHECK: ALL PASSED")
        print(f"  q1 -> status={q1['status']} quote_no={q1['quote_no']} items={len(items1)}")
        print(f"  q2 -> status={q2['status']} quote_no={q2['quote_no']} items={len(items2)}")
        shutil.rmtree(TEST_DIR)


if __name__ == "__main__":
    main()
