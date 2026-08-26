"""
Pricing engine v2 - per-line Cost/Sell model.

v1 of this engine (see git history) applied one global markup % per line
*type* (Material/Labour/Subcontractor) to a summed cost. That was replaced
because the business now prices Materials with a per-line Cost + Markup% +
Sell (any one of the three can be edited and the other two stay consistent -
see the materials_markup_link() helper below, mirrored client-side in
app.js), while every other line kind (Staff Labour, Outside Labour, Fixed
Service, Project Management, Other) carries an independent Cost and Sell
with no markup concept at all - the rate a technician is billed out at
already has margin baked in, there's nothing to compute.

    itemsCostSubtotal = sum(item.cost * item.qty)
    itemsSellSubtotal = sum(item.sell * item.qty)
    vehicle           = transportQty * transportFee            (pure pass-through: in both cost and sell)
    callOutAmount     = callOutFee if callOut else 0            (pure margin: sell-side only)
    costPrice         = itemsCostSubtotal + vehicle
    gross             = itemsSellSubtotal + vehicle + callOutAmount

    discountAmount = gross * discountPct/100
    netSelling     = max(0, gross - discountAmount)
    sellingPrice   = overridePrice if overridePrice > 0 else netSelling
    profit         = sellingPrice - costPrice

    markupPct  = profit / costPrice     (0 if costPrice <= 0)   <- profit as a % of COST
    marginPct  = profit / sellingPrice  (0 if sellingPrice <= 0) <- profit as a % of SELL
    (these are deliberately different numbers with the same numerator - the
    UI must always show both, labelled, so "mark-up" and "margin" are never
    read as interchangeable)

    vatAmount  = sellingPrice * vatPct/100
    grandTotal = sellingPrice + vatAmount
"""

ITEM_KINDS = ("material", "staff_labour", "outside_labour", "fixed_service", "project_management", "other")

DEFAULTS = {
    "hourlyRate": 250,
    "transportFee": 125,
    "callOutFee": 150,
    "vatPct": 5,
    "marginMinPct": 30,
    "marginTargetPct": 40,
    "marginUpperPct": 50,
    "maxDiscountPct": 100,
    # Only used client-side as the starting Markup% on a brand-new Material
    # line (before the user has typed a Cost/Sell/Markup% of their own) -
    # compute() itself never reads this, since every line already carries
    # its own resolved cost+sell by the time it reaches the server.
    "defaultMaterialMarkupPct": 50,
}


class ValidationError(Exception):
    """Raised by compute() for a business-rule violation (negative money,
    an out-of-range discount, etc). The API layer turns this into HTTP 400."""


def margin_band(margin_pct_fraction, margins=None):
    m = margins or DEFAULTS
    mp = margin_pct_fraction * 100
    if mp < m["marginMinPct"]:
        return "CRITICAL"
    if mp < m["marginTargetPct"]:
        return "WARN"
    if mp > m["marginUpperPct"]:
        return "ABOVE TARGET"
    return "ON TARGET"


def _num(v, default=0):
    if v is None or v == "":
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def materials_markup_link(edited_field, cost, sell, markup_pct):
    """The three-way Cost / Markup% / Sell relationship for a Material line -
    shared logic so server.py and app.js implement the exact same rule.
    Whichever field the user just edited is treated as ground truth; the
    OTHER TWO are reconciled from it (not "the two not-edited fields left
    stale relative to each other"):

      - edited 'markup_pct' -> sell is recomputed from cost
      - edited 'sell'       -> markup_pct is recomputed from cost (effective %)
      - edited 'cost'       -> sell is recomputed from the *last-known* markup_pct
                                (a cost correction preserves pricing policy,
                                it doesn't silently change the line's margin)

    Returns (cost, sell, markup_pct) - all three, always mutually consistent.
    """
    cost = _num(cost)
    sell = _num(sell)
    markup_pct = _num(markup_pct)

    if edited_field == "sell":
        markup_pct = ((sell - cost) / cost * 100) if cost > 0 else 0.0
    else:
        # edited_field in ("markup_pct", "cost", or unset/initial) - sell follows cost+markup.
        sell = cost * (1 + markup_pct / 100)

    return cost, sell, markup_pct


def compute(q):
    items = q.get("items") or []

    for it in items:
        if _num(it.get("cost")) < 0:
            raise ValidationError("A line item's cost cannot be negative.")
        if _num(it.get("sell")) < 0:
            raise ValidationError("A line item's sell price cannot be negative.")

    override_price = _num(q.get("overridePrice"))
    if override_price < 0:
        raise ValidationError("Override price cannot be negative.")

    max_discount_pct = _num(q.get("maxDiscountPct"), DEFAULTS["maxDiscountPct"])
    discount_pct = _num(q.get("discountPct"))
    if discount_pct < 0 or discount_pct > max_discount_pct:
        raise ValidationError("Discount must be between 0 and %g%%." % max_discount_pct)

    items_cost_subtotal = sum(_num(i.get("cost")) * _num(i.get("qty"), 1) for i in items)
    items_sell_subtotal = sum(_num(i.get("sell")) * _num(i.get("qty"), 1) for i in items)

    breakdown = {}
    for kind in ITEM_KINDS:
        kind_items = [i for i in items if i.get("kind") == kind]
        if not kind_items:
            continue
        breakdown[kind] = {
            "cost": sum(_num(i.get("cost")) * _num(i.get("qty"), 1) for i in kind_items),
            "sell": sum(_num(i.get("sell")) * _num(i.get("qty"), 1) for i in kind_items),
        }

    transport_qty = _num(q.get("transportQty"))
    transport_fee = _num(q.get("transportFee"), DEFAULTS["transportFee"])
    vehicle = transport_qty * transport_fee

    call_out = bool(q.get("callOut"))
    call_out_fee = _num(q.get("callOutFee"), DEFAULTS["callOutFee"])
    call_out_amount = call_out_fee if call_out else 0.0

    vat_pct = _num(q.get("vatPct"), DEFAULTS["vatPct"])

    cost_price = items_cost_subtotal + vehicle
    gross = items_sell_subtotal + vehicle + call_out_amount
    discount_amount = gross * (discount_pct / 100)
    net_selling = max(0.0, gross - discount_amount)
    selling_price = override_price if override_price > 0 else net_selling
    profit = selling_price - cost_price
    markup_pct = (profit / cost_price) if cost_price > 0 else 0.0
    margin_pct = (profit / selling_price) if selling_price > 0 else 0.0
    vat_amount = selling_price * (vat_pct / 100)
    grand_total = selling_price + vat_amount

    margins = {
        "marginMinPct": _num(q.get("marginMinPct"), DEFAULTS["marginMinPct"]),
        "marginTargetPct": _num(q.get("marginTargetPct"), DEFAULTS["marginTargetPct"]),
        "marginUpperPct": _num(q.get("marginUpperPct"), DEFAULTS["marginUpperPct"]),
    }

    return {
        "itemsCostSubtotal": items_cost_subtotal,
        "itemsSellSubtotal": items_sell_subtotal,
        "breakdown": breakdown,
        "vehicle": vehicle,
        "callOutAmount": call_out_amount,
        "costPrice": cost_price,
        "gross": gross,
        "discountAmount": discount_amount,
        "netSelling": net_selling,
        "sellingPrice": selling_price,
        "profit": profit,
        "markupPct": markup_pct,
        "marginPct": margin_pct,
        "marginBand": margin_band(margin_pct, margins),
        "vatAmount": vat_amount,
        "grandTotal": grand_total,
    }
