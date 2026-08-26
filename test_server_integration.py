"""
Integration test: saves each of the pricing test cases as a REAL quote through
server.save_quote() (the exact code path the running app uses), reads it back from
SQLite, and checks the persisted numbers match the same hand-verified values as
test_pricing.py / Pricing-Engine-Test-Cases.xlsx. Cleans up every row it creates.

TC-02/03/06 (transport/call-out-only or fully blank quotes, with no material/
labour/subcontractor/other line at all) are skipped here - they still pass in
test_pricing.py against pricing.compute() directly, but save_quote() now
enforces "at least one priced item required" as a save-time business rule
(compute() itself has no opinion on that - a quote that's just a transport
charge isn't a real quote), so they can no longer be persisted.

Run:  python test_server_integration.py
"""
from decimal import Decimal, ROUND_HALF_UP

import server
from test_pricing import TESTS

SKIP = {"TC-02", "TC-03", "TC-06"}  # see module docstring


def round2(n):
    return float(Decimal(str(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def to_payload(tid, q):
    return {
        "date": "2026-08-06", "validUntil": "2026-08-20", "staff": "",
        "client": {"name": f"QA-INTEGRATION-TEST {tid}", "phone": "", "address": "", "email": ""},
        "duration": "", "scope": "", "terms": "",
        "items": q.get("items") or [],
        "transportQty": q.get("transportQty", 0),
        "transportFee": q.get("transportFee"),
        "callOut": q.get("callOut", False),
        "callOutFee": q.get("callOutFee"),
        "discountPct": q.get("discountPct", 0),
        "overridePrice": q.get("overridePrice", 0),
        "vatPct": q.get("vatPct"),
    }


def main():
    server.init_db()
    conn = server.get_conn()
    conn.execute("DELETE FROM quotes WHERE client_name LIKE 'QA-INTEGRATION-TEST%'")
    conn.commit()
    conn.close()

    failures = []
    saved_ids = []
    for tid, q, expected_grand, _nudge in TESTS:
        if tid in SKIP:
            print(f"{tid}  SKIPPED (see module docstring)")
            continue
        payload = to_payload(tid, q)
        saved = server.save_quote(payload, created_by_email="qa@handyman.ae")
        saved_ids.append(saved["id"])

        reloaded_row = server.get_conn().execute("SELECT * FROM quotes WHERE id=?", (saved["id"],)).fetchone()
        reloaded = server.quote_row_to_dict(reloaded_row)

        grand = round2(reloaded["grandTotal"])
        ok = abs(grand - expected_grand) < 0.01
        # A brand-new quote auto-escalates straight to Approval Required if its
        # selling price is over the AED 2,000 threshold (see save_quote()) -
        # several of these hand-verified scenarios land above that on purpose,
        # so the expected status has to follow the same rule the server uses.
        expected_status = "Approval Required" if reloaded["sellingPrice"] > server.APPROVAL_THRESHOLD_AED else "Draft"
        ok_status = reloaded["status"] == expected_status
        ok_no = bool(reloaded["quoteNo"]) and reloaded["quoteNo"].startswith("QB-")
        status = "PASS" if (ok and ok_status and ok_no) else "FAIL"
        print(f"{tid}  saved_grand={round2(saved['grandTotal']):>10}  reloaded_grand={grand:>10}  expected={expected_grand:>10}  status={reloaded['status']:17} quoteNo={reloaded['quoteNo']}  {status}")
        if status == "FAIL":
            failures.append(tid)

    # A quote over the approval threshold must be blocked from a direct
    # Draft -> Sent to Jobber transition, and unblocked once approved by an admin.
    big = server.save_quote(to_payload("BIG", {"items": [{"kind": "material", "cost": 1000, "sell": 5000, "qty": 1}]}), created_by_email="qa@handyman.ae")
    saved_ids.append(big["id"])
    conn = server.get_conn()
    if not conn.execute("SELECT 1 FROM users WHERE email='qa-admin@handyman.ae'").fetchone():
        conn.execute("INSERT INTO users (id,email,name,role,created_at,last_login_at) VALUES ('qa-admin','qa-admin@handyman.ae','QA Admin','admin','x','x')")
        conn.commit()
    conn.close()
    admin_user = {"id": "qa-admin", "email": "qa-admin@handyman.ae", "role": "admin"}
    staff_user = {"id": "qa-staff", "email": "qa@handyman.ae", "role": "staff"}

    import json
    status_code, _headers, body = server.quote_status_action(big["id"], "send_to_jobber", staff_user)
    blocked_ok = status_code == 400
    print(f"{'BIG-BLOCK':10} direct send_to_jobber over threshold -> {status_code}  {'PASS' if blocked_ok else 'FAIL'}")
    if not blocked_ok:
        failures.append("BIG-BLOCK")

    status_code, _headers, body = server.quote_status_action(big["id"], "submit_for_approval", staff_user)
    status_code2, _headers2, body2 = server.quote_status_action(big["id"], "approve_and_send", admin_user)
    approved_ok = status_code2 == 200 and json.loads(body2)["status"] == "Sent to Jobber"
    print(f"{'BIG-APPROVE':10} submit + admin approve -> {status_code2}  {'PASS' if approved_ok else 'FAIL'}")
    if not approved_ok:
        failures.append("BIG-APPROVE")

    conn = server.get_conn()
    conn.executemany("DELETE FROM quotes WHERE id=?", [(i,) for i in saved_ids])
    conn.commit()
    remaining = conn.execute("SELECT COUNT(*) AS c FROM quotes WHERE client_name LIKE 'QA-INTEGRATION-TEST%'").fetchone()["c"]
    conn.close()
    print(f"\nCleanup: {remaining} QA test quotes remain in the database (should be 0).")

    if failures or remaining:
        print(f"\n{len(failures)} FAILED: {failures}")
        raise SystemExit(1)
    print(f"\nALL {len(TESTS) - len(SKIP)} INTEGRATION TESTS + approval-workflow checks PASSED (saved to SQLite, reloaded, verified, cleaned up)")


if __name__ == "__main__":
    main()
