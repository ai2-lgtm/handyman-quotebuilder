"""
Runs the same 24 test cases from Pricing-Engine-Test-Cases.xlsx (Core Engine Tests
sheet) against the ACTUAL pricing.py module used by this app's server, so the
quote builder's own code is verified against the same hand-checked numbers.

Run:  python test_pricing.py
"""
from decimal import Decimal, ROUND_HALF_UP
from pricing import compute

BLANK = None


def round2(n):
    """Round half-away-from-zero to 2dp, matching Excel's ROUND() and the app's JS
    display formatting - NOT Python's built-in round(), which uses banker's rounding
    and would turn an exact tie like 1063.125 into 1063.12 instead of 1063.13."""
    return float(Decimal(str(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def row(materials_cost=0, labour_sell=0, sub_cost=0, transport_qty=0, call_out=0, other=0,
        discount_pct=0, override_price=0, labour_margin_ovr=BLANK, markup_mat_ovr=BLANK,
        markup_lab_ovr=BLANK, markup_sub_ovr=BLANK, vat_ovr=BLANK):
    """Builds a v2 items-list quote from the same v1-style knobs the original
    24 test cases were authored against (a single global markup% per type,
    a single labour margin% - v1's whole input model), so the same verified
    historical numbers keep regression-checking the v2 per-line engine. The
    knobs themselves are resolved to concrete per-line cost/sell values HERE,
    not passed to compute() - v2's compute() only ever sees resolved items."""
    labour_margin = 50 if labour_margin_ovr is None else labour_margin_ovr
    markup_mat = 50 if markup_mat_ovr is None else markup_mat_ovr
    markup_lab = 0 if markup_lab_ovr is None else markup_lab_ovr
    markup_sub = 15 if markup_sub_ovr is None else markup_sub_ovr

    items = []
    if materials_cost:
        items.append({"kind": "material", "cost": materials_cost, "sell": materials_cost * (1 + markup_mat / 100), "qty": 1})
    if labour_sell:
        items.append({"kind": "staff_labour", "cost": labour_sell * (1 - labour_margin / 100), "sell": labour_sell * (1 + markup_lab / 100), "qty": 1})
    if sub_cost:
        items.append({"kind": "outside_labour", "cost": sub_cost, "sell": sub_cost * (1 + markup_sub / 100), "qty": 1})
    if other:
        items.append({"kind": "other", "cost": other, "sell": other, "qty": 1})

    q = {
        "items": items,
        "transportQty": transport_qty,
        "callOut": bool(call_out),
        "discountPct": discount_pct,
        "overridePrice": override_price,
    }
    if vat_ovr is not None:
        q["vatPct"] = vat_ovr
    return q


TESTS = [
    ("TC-01", row(labour_sell=1000, transport_qty=1), 1181.25, None),
    ("TC-02", row(transport_qty=1), 131.25, None),
    ("TC-03", row(), 0.00, None),
    ("TC-04", row(materials_cost=1000, transport_qty=1), 1706.25, None),
    ("TC-05", row(sub_cost=1000, transport_qty=1), 1338.75, None),
    ("TC-06", row(transport_qty=1, call_out=1), 288.75, None),
    # v1 allowed an uncapped discount% and silently clamped the result to 0;
    # v2 rejects any discountPct outside [0,100] instead (see test_discount_pct_out_of_range_rejected
    # below) - 100% is the boundary value that still produces the same
    # historical zero-total result this case was designed to check.
    ("TC-07", row(labour_sell=1000, transport_qty=1, discount_pct=100), 0.00, None),
    ("TC-08", row(labour_sell=1000, transport_qty=1, discount_pct=20, override_price=2000), 2100.00, None),
    ("TC-09", row(labour_sell=1750, transport_qty=1), 1968.75, "No"),
    ("TC-10", row(labour_sell=2000, transport_qty=1), 2231.25, "Yes"),
    ("TC-11", row(labour_sell=1800, transport_qty=1), 2021.25, "No"),
    ("TC-12", row(labour_sell=500, transport_qty=1), 656.25, None),
    ("TC-13", row(labour_sell=6000, transport_qty=1), 6431.25, None),
    ("TC-14", row(other=5440, discount_pct=70), 1713.60, None),
    ("TC-15", row(labour_sell=1000, transport_qty=1, labour_margin_ovr=0), 1181.25, None),
    ("TC-16", row(labour_sell=1000, transport_qty=1, labour_margin_ovr=100), 1181.25, None),
    ("TC-17", row(labour_sell=1000, transport_qty=1, vat_ovr=0), 1125.00, None),
    ("TC-18", row(labour_sell=1000, transport_qty=1, vat_ovr=12), 1260.00, None),
    ("TC-19", row(materials_cost=2000, labour_sell=1500, sub_cost=500, transport_qty=2,
                  call_out=1, other=100, discount_pct=5), 5561.06, None),
    ("TC-20", row(materials_cost=700, override_price=1000), 1050.00, None),
    ("TC-21", row(materials_cost=600, override_price=1000), 1050.00, None),
    ("TC-22", row(materials_cost=500, override_price=1000), 1050.00, None),
    ("TC-23", row(labour_sell=1000, transport_qty=1, discount_pct=10), 1063.13, None),
    ("TC-24", row(labour_sell=1000, transport_qty=1, discount_pct=15), 1004.06, None),
]

EXPECTED_BAND = {
    "TC-01": "ON TARGET", "TC-02": "CRITICAL", "TC-03": "CRITICAL", "TC-04": "WARN",
    "TC-05": "CRITICAL", "TC-06": "ABOVE TARGET", "TC-07": "CRITICAL", "TC-08": "ABOVE TARGET",
    "TC-09": "ON TARGET", "TC-10": "ON TARGET", "TC-11": "ON TARGET", "TC-12": "ON TARGET",
    "TC-13": "ON TARGET", "TC-14": "CRITICAL", "TC-15": "CRITICAL", "TC-16": "ABOVE TARGET",
    "TC-17": "ON TARGET", "TC-18": "ON TARGET", "TC-19": "WARN", "TC-20": "WARN",
    "TC-21": "ON TARGET", "TC-22": "ON TARGET", "TC-23": "WARN", "TC-24": "WARN",
}


def test_validation_rules():
    from pricing import ValidationError

    checks = []

    def expect_rejected(label, q):
        try:
            compute(q)
            checks.append((label, False, "did not raise"))
        except ValidationError:
            checks.append((label, True, None))

    def expect_ok(label, q):
        try:
            compute(q)
            checks.append((label, True, None))
        except ValidationError as e:
            checks.append((label, False, str(e)))

    expect_rejected("negative item cost rejected", {"items": [{"kind": "material", "cost": -1, "sell": 10, "qty": 1}]})
    expect_rejected("negative item sell rejected", {"items": [{"kind": "material", "cost": 10, "sell": -1, "qty": 1}]})
    expect_rejected("negative overridePrice rejected", {"items": [], "overridePrice": -5})
    expect_rejected("discountPct > 100 rejected", {"items": [], "discountPct": 150})
    expect_rejected("discountPct < 0 rejected", {"items": [], "discountPct": -1})
    expect_ok("discountPct = 100 allowed (boundary)", {"items": [], "discountPct": 100})
    expect_ok("discountPct = 0 allowed (boundary)", {"items": [], "discountPct": 0})

    failures = [c for c in checks if not c[1]]
    for label, ok, detail in checks:
        print(f"{label:42} {'PASS' if ok else 'FAIL ' + str(detail)}")
    return failures


def test_markup_vs_margin_distinct():
    # profit shared numerator, deliberately different denominators - the fix
    # for "markup and margin were being read as the same number".
    r = compute({"items": [{"kind": "material", "cost": 100, "sell": 150, "qty": 1}]})
    assert abs(r["markupPct"] - 0.5) < 1e-9, r["markupPct"]   # 50/100 (cost-based)
    assert abs(r["marginPct"] - (1 / 3)) < 1e-9, r["marginPct"]  # 50/150 (sell-based)
    print(f"{'markupPct vs marginPct are distinct':42} PASS")
    return []


def main():
    failures = []
    for tid, q, expected_grand, expected_nudge in TESTS:
        r = compute(q)
        grand = round2(r["grandTotal"])
        band = r["marginBand"]
        exp_band = EXPECTED_BAND[tid]
        ok_grand = abs(grand - expected_grand) < 0.01
        ok_band = band == exp_band
        status = "PASS" if (ok_grand and ok_band) else "FAIL"
        print(f"{tid}  grand={grand:>10}  expected={expected_grand:>10}  band={band:14} expected_band={exp_band:14}  {status}")
        if status == "FAIL":
            failures.append(tid)

    print()
    failures += [f[0] for f in test_validation_rules()]
    failures += [f[0] for f in test_markup_vs_margin_distinct()]

    print()
    if failures:
        print(f"{len(failures)} FAILURES:", failures)
        raise SystemExit(1)
    else:
        print(f"ALL {len(TESTS)} PRICING SCENARIOS + VALIDATION/MARKUP CHECKS PASSED")


if __name__ == "__main__":
    main()
