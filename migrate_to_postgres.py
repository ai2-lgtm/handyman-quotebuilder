"""
One-time data migration: copies every row out of the local SQLite database
(data/handyman.db) into a Postgres database, in FK-safe order, then resets
each SERIAL sequence so new rows keep numbering from where SQLite left off.

Usage:
    DATABASE_URL=postgres://user:pass@host:port/dbname python migrate_to_postgres.py

Options:
    --sqlite-path PATH   defaults to data/handyman.db
    --force              proceed even if the target already has rows in `quotes`
                          (by default this refuses, so it can't be run twice by
                          accident and double-insert everything)

Run this ONCE, against a target Postgres database that's either empty or has
never seen this app's schema before - it creates the schema itself (by
calling server.init_db()) before copying any data.
"""
import argparse
import os
import sqlite3
import sys

import psycopg2

import server  # reuses init_db() + DATABASE_URL against the target Postgres


# Tables in FK-safe order (parents before children). amc_proposals and
# amc_contracts are mutually referential, so they're handled separately,
# after everything else.
TABLE_ORDER = [
    "categories", "subcategories", "quotes", "quote_items", "users", "sessions",
    "oauth_states", "admin_allowlist", "counters", "app_settings",
    "quote_audit_log", "quote_templates", "quote_template_items",
    "pb_materials", "pb_labour", "pb_fixed_services", "pb_suppliers",
    "pb_contractors", "pb_contractor_pricing", "amc_clients", "amc_history",
    "amc_action_handled", "amc_commercial_rates",
]

SERIAL_TABLES = ["quote_items", "quote_audit_log", "quote_template_items", "pb_contractor_pricing", "amc_history"]


def copy_table(sconn, tconn, table):
    scur = sconn.execute("SELECT * FROM %s" % table)
    cols = [d[0] for d in scur.description]
    rows = scur.fetchall()
    if not rows:
        print(f"  {table}: 0 rows")
        return
    col_list = ",".join(cols)
    placeholders = ",".join(["%s"] * len(cols))
    tcur = tconn.cursor()
    for row in rows:
        tcur.execute(f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", [row[c] for c in cols])
    tconn.commit()
    print(f"  {table}: {len(rows)} rows")


def copy_amc_proposals_and_contracts(sconn, tconn):
    """amc_proposals.converted_to_contract_id -> amc_contracts, and
    amc_contracts.proposal_id -> amc_proposals: a circular reference, so
    proposals are inserted first with that one column forced NULL, then
    contracts (which only ever point at already-inserted proposals), then
    the deferred proposal -> contract links are filled in."""
    scur = sconn.execute("SELECT * FROM amc_proposals")
    cols = [d[0] for d in scur.description]
    rows = scur.fetchall()
    deferred_links = []
    tcur = tconn.cursor()
    for row in rows:
        values = []
        for c in cols:
            v = row[c]
            if c == "converted_to_contract_id":
                if v:
                    deferred_links.append((row["id"], v))
                v = None
            elif c == "pdf" and v is not None:
                v = psycopg2.Binary(v)
            values.append(v)
        col_list = ",".join(cols)
        placeholders = ",".join(["%s"] * len(cols))
        tcur.execute(f"INSERT INTO amc_proposals ({col_list}) VALUES ({placeholders})", values)
    tconn.commit()
    print(f"  amc_proposals: {len(rows)} rows")

    scur = sconn.execute("SELECT * FROM amc_contracts")
    cols = [d[0] for d in scur.description]
    rows = scur.fetchall()
    for row in rows:
        values = []
        for c in cols:
            v = row[c]
            if c == "pdf" and v is not None:
                v = psycopg2.Binary(v)
            values.append(v)
        col_list = ",".join(cols)
        placeholders = ",".join(["%s"] * len(cols))
        tcur.execute(f"INSERT INTO amc_contracts ({col_list}) VALUES ({placeholders})", values)
    tconn.commit()
    print(f"  amc_contracts: {len(rows)} rows")

    for proposal_id, contract_id in deferred_links:
        tcur.execute("UPDATE amc_proposals SET converted_to_contract_id=%s WHERE id=%s", (contract_id, proposal_id))
    tconn.commit()
    if deferred_links:
        print(f"  amc_proposals -> amc_contracts links restored: {len(deferred_links)}")


def reset_sequences(tconn):
    """Rows above were inserted with their original SQLite integer ids, so
    each SERIAL sequence has to be moved past the highest one copied in -
    otherwise the first INSERT the app makes after this migration would
    collide with a migrated row's id."""
    tcur = tconn.cursor()
    for table in SERIAL_TABLES:
        tcur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )
    tconn.commit()
    print("Sequences reset for:", ", ".join(SERIAL_TABLES))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite-path", default=os.path.join("data", "handyman.db"))
    parser.add_argument("--force", action="store_true", help="proceed even if the target already has data")
    args = parser.parse_args()

    if not server.DATABASE_URL:
        sys.exit("DATABASE_URL is not set - point it at the target Postgres database first.")
    if not os.path.exists(args.sqlite_path):
        sys.exit(f"SQLite source not found: {args.sqlite_path}")

    print(f"Source: {args.sqlite_path}")
    print("Creating schema on target (if not already there)...")
    server.init_db()

    sconn = sqlite3.connect(args.sqlite_path)
    sconn.row_factory = sqlite3.Row
    tconn = psycopg2.connect(server.DATABASE_URL)

    tcur = tconn.cursor()
    tcur.execute("SELECT COUNT(*) FROM quotes")
    existing = tcur.fetchone()[0]
    if existing and not args.force:
        sys.exit(f"Target already has {existing} row(s) in quotes - refusing to double-insert. Pass --force to proceed anyway.")

    print("Copying tables...")
    for table in TABLE_ORDER:
        copy_table(sconn, tconn, table)
    copy_amc_proposals_and_contracts(sconn, tconn)
    reset_sequences(tconn)

    sconn.close()
    tconn.close()
    print("Done.")


if __name__ == "__main__":
    main()
