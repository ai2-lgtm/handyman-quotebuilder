"""
AMC Proposal & Contract business logic - ported 1:1 from the standalone
"Handyman.ae AMC Proposal & Contract Builder" prototype (a client-side,
localStorage/File-System-Access-only tool). Every pricing table, formula,
and package-inclusion rule below is copied exactly from that file's JS
(PRICES, COMM_DEFAULTS, comparisonRows(), alwaysIncluded(), cardHighlights(),
calloutsFor(), contractPriceRow(), MAT_THRESHOLD) - only the storage layer
and PDF-generation approach changed: a real generated PDF stored as a
permanent record in SQLite behind our normal session auth, instead of the
browser's Save-as-PDF print dialog plus a JSON blob re-written into a shared
HTML file on disk. See amc_pdf.py for PDF layout and server.py's
amc_proposal_*/amc_contract_* functions for persistence.
"""

TIERS = ("Basic", "Standard", "Premium")
TAGS = ("Essential cover", "Most popular", "Complete cover")
PROPERTY_TYPES = ("apartment", "villa", "commercial")
TYPE_LABEL = {"apartment": "Apartment", "villa": "Villa / Townhouse", "commercial": "Commercial / Retail"}
PAY_PLANS = ("onetime", "monthly")
SIGNATORY_KEYS = ("tegan", "kristofer", "govind")

SIGNATORIES = {
    "tegan": {"name": "Tegan Bradley", "title": "Operations Manager, Handyman.ae", "signature": True},
    "kristofer": {"name": "Kristofer Miranda", "title": "Customer Service Coordinator, Handyman.ae", "signature": False},
    "govind": {"name": "Govind Singh Rawat", "title": "Customer Service Coordinator, Handyman.ae", "signature": False},
}

# Annual price incl. 5% VAT. Order per row: (Basic, Standard, Premium).
APARTMENT_PRICES = {
    1: (1470, 2636, 3801),
    2: (1995, 3318, 4641),
    3: (2520, 4000, 5481),
    4: (3045, 4683, 6321),
}
VILLA_PRICES = {
    2: (2625, 3948, 5271),
    3: (2940, 4420, 5901),
    4: (3255, 4893, 6531),
    5: (3570, 5366, 7161),
    6: (3885, 5838, 7791),
    7: (4200, 6310, 8421),
    8: (4515, 6783, 9051),
    9: (4830, 7256, 9681),
    10: (5145, 7729, 10311),
    11: (5460, 8201, 10941),
    12: (5775, 8674, 11571),
}

# Commercial: price = base + (per-unit rate x units). Defaults are "villa
# rate + 35%" for commercial usage/access/longer hours - each team can
# override its own rates (admin only) via /api/amc-proposals/commercial-rates.
COMMERCIAL_RATE_DEFAULTS = {
    "basic": {"base": 2700, "per": 425},
    "standard": {"base": 4050, "per": 640},
    "premium": {"base": 5400, "per": 850},
}

UNIT_RANGE = {"apartment": (1, 4), "villa": (2, 12), "commercial": (1, 20)}
DEFAULT_UNITS = {"apartment": 2, "villa": 3, "commercial": 10}

MARKUP = 1.08   # Monthly Plan admin charge (8%)
DEPOSIT = 0.25  # counts as month 1
INSTALMENTS = 11

MAT_THRESHOLD = (50, 100, 150)  # AED per visit, Basic/Standard/Premium
HANDYMAN_VISITS_LABEL = ("Not included", "1x / year", "2x / year")

VALIDITY_OPTIONS = (7, 14, 30)


def price_for(property_type, units, commercial_rates=None, override=None):
    """override is a (basic, standard, premium) sequence of AED-or-None -
    any non-None entry replaces that tier's price outright, exactly like the
    original tool's per-proposal 'negotiated price' box. A contract should
    never pass an override - see contract_price_for()."""
    if property_type == "commercial":
        rates = commercial_rates or COMMERCIAL_RATE_DEFAULTS
        row = [round(rates[t]["base"] + rates[t]["per"] * units) for t in ("basic", "standard", "premium")]
    else:
        table = APARTMENT_PRICES if property_type == "apartment" else VILLA_PRICES
        row = list(table[units])
    if override:
        row = [o if o is not None else v for o, v in zip(override, row)]
    return row


def contract_price_for(property_type, units, commercial_rates=None):
    """A contract always reflects the true rate book - never the proposal's
    screen-only negotiated-price override."""
    return price_for(property_type, units, commercial_rates=commercial_rates)


def monthly_plan(annual):
    plan_total = annual * MARKUP
    deposit = plan_total * DEPOSIT
    monthly = (plan_total - deposit) / INSTALMENTS
    return {"planTotal": plan_total, "deposit": deposit, "monthly": monthly}


def callouts_for(property_type):
    if property_type == "commercial":
        return {"basic": 12, "standard": 24}
    if property_type == "villa":
        return {"basic": 6, "standard": 12}
    return {"basic": 8, "standard": 12}  # apartment


def comparison_rows(property_type):
    co = callouts_for(property_type)
    callouts = ("%d per year" % co["basic"], "%d per year" % co["standard"], "Unlimited")
    return [
        ("AC PPM visits", ("2 per unit, per year", "3 per unit, per year", "4 per unit, per year")),
        ("Plumbing PPM visits", ("2 per year", "3 per year", "4 per year")),
        ("Electrical PPM visits", ("2 per year", "3 per year", "4 per year")),
        ("MEP call-outs", callouts),
        ("Parts & materials per call-out", ("Not included", "Up to AED 50", "Up to AED 150")),
        ("Handyman visits included", ("Not included", "1 x 1 hour", "2 x 1 hour")),
        ("Additional handyman work", ("AED 160 per hour", "20% off standard rate", "20% off standard rate")),
    ]


def always_included(property_type):
    base = [
        "Priority emergency response",
        "Minor A/C gas top-ups",
        "Drain line cleaning once a year",
        "12-month contract term",
        "One-time or monthly payment",
        "Licensed, DEWA-compliant technicians",
        "All prices include 5% VAT",
    ]
    if property_type == "villa":
        base.insert(2, "Water tank cleaning once a year")
    if property_type == "commercial":
        base.insert(2, "Scheduled outside trading hours where required")
    return base


def card_highlights(property_type):
    co = callouts_for(property_type)
    ct = ("%d call-outs a year" % co["basic"], "%d call-outs a year" % co["standard"], "Unlimited call-outs")
    return [
        [("2 AC PPM visits per unit", False), (ct[0], False), ("2 plumbing + 2 electrical PPM visits", False),
         ("Parts not included", True), ("No free handyman hours", True)],
        [("3 AC PPM visits per unit", False), (ct[1], False), ("3 plumbing + 3 electrical PPM visits", False),
         ("Parts covered up to AED 50 a visit", False), ("1 free handyman hour", False)],
        [("4 AC PPM visits per unit", False), (ct[2], False), ("4 plumbing + 4 electrical PPM visits", False),
         ("Parts covered up to AED 150 a visit", False), ("2 free handyman hours", False)],
    ]


def package_inclusions_table(property_type):
    """The contract's richer 'Package Inclusions at a Glance' table (Section 8)."""
    co = callouts_for(property_type)
    water_tank = ("1x / year",) * 3 if property_type == "villa" else ("N/A",) * 3
    return [
        ("Call Outs (emergency & non-emergency)",
         ("%d limited, free visits" % co["basic"], "%d limited, free visits" % co["standard"], "Unlimited")),
        ("AC Unit Servicing", ("2x per unit / year", "3x per unit / year", "4x per unit / year")),
        ("Drainage Acid Wash", ("1x / year", "1x / year", "1x / year")),
        ("Water Tank Cleaning (For Villas)", water_tank),
        ("Duct Cleaning", ("Not included", "Not included", "1x per unit / year")),
        ("Materials Covered (per visit threshold)",
         ("AED %d" % MAT_THRESHOLD[0], "AED %d" % MAT_THRESHOLD[1], "AED %d" % MAT_THRESHOLD[2])),
        ("Handyman Visits (1 hour each)", HANDYMAN_VISITS_LABEL),
        ("Payment Options", ("One-time or Monthly",) * 3),
    ]


# Wording swap for commercial vs residential - a warehouse in JAFZA isn't "your home".
COPY = {
    "residential": {
        "eyebrow": "Annual Maintenance Contract",
        "title_a": "Your home, ", "title_b": "handled.",
        "strap": "AC, plumbing and electrics looked after all year for one fixed price — planned "
                 "maintenance visits, priority call-outs and no surprise bills.",
        "quote": "“Your home deserves the same care we’d give our own — that’s the "
                 "standard I hold our team to.”",
        "prop_label": "Property",
        "contract_strap": "AC, plumbing and electrical care for your home — the agreement below sets "
                           "out exactly what's covered.",
    },
    "commercial": {
        "eyebrow": "Commercial Maintenance Contract",
        "title_a": "Your site, ", "title_b": "handled.",
        "strap": "AC, plumbing and electrics across your premises looked after all year for one fixed price "
                 "— planned maintenance visits, priority call-outs and no downtime surprises.",
        "quote": "“Your premises deserve the same care we’d give our own — that’s the "
                 "standard I hold our team to.”",
        "prop_label": "Premises",
        "contract_strap": "AC, plumbing and electrical care for your premises — the agreement below "
                           "sets out exactly what's covered.",
    },
}

PROPOSAL_TERMS = [
    "All prices are in UAE Dirhams and include 5% VAT.",
    "Contract term is 12 months from the activation date.",
    "Preventive maintenance visits are scheduled in advance and confirmed by our coordination team.",
    "Emergency response within 90 minutes applies during business hours, within our Dubai service areas. "
    "Outside business hours we attend as soon as a technician is available.",
    "Non-emergency call-outs are attended within 4 hours during business hours, or scheduled for the next "
    "available slot at your convenience.",
    "Call-outs cover labour and diagnosis. Parts are covered up to the limit shown for your package; "
    "anything above is quoted and approved before work starts.",
    "Major component replacements (compressors, coils, pumps, tanks) are not covered and will be quoted "
    "separately.",
    "The monthly plan carries an 8% administration charge and requires the deposit before the first visit.",
    "Cover begins once the signed agreement and first payment are received.",
]
PROPOSAL_NOTE = (
    "Please note: this document is a proposal only and does not constitute a contract. Once you have chosen "
    "a package, a full Annual Maintenance Contract will be issued for signature. Cover starts only when that "
    "agreement is signed and the first payment is received."
)


def unit_options(property_type):
    lo, hi = UNIT_RANGE[property_type]
    return list(range(lo, hi + 1))


def validate_proposal_input(body):
    errors = []
    ptype = body.get("propertyType")
    if ptype not in PROPERTY_TYPES:
        errors.append("propertyType must be one of %s" % (PROPERTY_TYPES,))
    units = body.get("acUnits")
    if ptype in UNIT_RANGE and isinstance(units, int):
        lo, hi = UNIT_RANGE[ptype]
        if not (lo <= units <= hi):
            errors.append("acUnits for %s must be between %d and %d" % (ptype, lo, hi))
    elif not isinstance(units, int):
        errors.append("acUnits is required")
    if body.get("validityDays") not in VALIDITY_OPTIONS:
        errors.append("validityDays must be one of %s" % (VALIDITY_OPTIONS,))
    return errors


def validate_contract_input(body):
    errors = []
    ptype = body.get("propertyType")
    if ptype not in PROPERTY_TYPES:
        errors.append("propertyType must be one of %s" % (PROPERTY_TYPES,))
    units = body.get("acUnits")
    if ptype in UNIT_RANGE and isinstance(units, int):
        lo, hi = UNIT_RANGE[ptype]
        if not (lo <= units <= hi):
            errors.append("acUnits for %s must be between %d and %d" % (ptype, lo, hi))
    elif not isinstance(units, int):
        errors.append("acUnits is required")
    if body.get("package") not in TIERS:
        errors.append("package must be one of %s" % (TIERS,))
    if body.get("payPlan") not in PAY_PLANS:
        errors.append("payPlan must be one of %s" % (PAY_PLANS,))
    if body.get("signatory") not in SIGNATORY_KEYS:
        errors.append("signatory must be one of %s" % (SIGNATORY_KEYS,))
    if not body.get("startDate"):
        errors.append("startDate is required")
    if not (body.get("clientName") or "").strip():
        errors.append("clientName is required")
    return errors
