"""
AMC Proposal & Contract PDF generation.

Replaces the original standalone tool's "browser print dialog set to Save as
PDF" workflow with a real generated PDF (reportlab), built server-side so it
can be stored as a permanent, immutable record alongside the proposal/
contract row in SQLite (see server.py's amc_proposal_*/amc_contract_*
functions). Content, structure, numbers and legal wording are carried over
verbatim from the original file - see amc_proposal.py for the pricing/
inclusions data this module renders, and read the docstring there for the
provenance note.

Visual design approximates the original's navy/orange brand look (the exact
Barlow Condensed / Inter webfonts aren't bundled here, so headings use
Helvetica-Bold and body text Helvetica - both guaranteed available with no
extra font files to manage or deploy).
"""
import io
import os

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

import amc_proposal as ap

NAVY = HexColor("#0B1B3D")
ORANGE = HexColor("#F4591C")
INK = HexColor("#1A2438")
SLATE = HexColor("#5F6B80")
LINE = HexColor("#DDE2EA")
LINE_SOFT = HexColor("#EDF0F5")
WASH = HexColor("#F6F8FB")
WARN_BG = HexColor("#FEF3EE")
WARN_TEXT = HexColor("#8C3007")
GREY_OFF = HexColor("#9AA4B5")
WHITE = colors.white

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
CONTENT_W = PAGE_W - 2 * MARGIN

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
SIGNATURE_PATH = os.path.join(ASSETS_DIR, "tegan_signature.png")

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def fmt_date(d):
    return "%d %s %d" % (d.day, MONTHS[d.month - 1], d.year)


def money(n):
    return "{:,.0f}".format(round(n))


def money2(n):
    return "{:,.2f}".format(n)


# ---------------------------------------------------------------------------
# Shared styles
# ---------------------------------------------------------------------------

def _styles():
    s = {}
    s["Logo"] = ParagraphStyle("Logo", fontName="Helvetica-Bold", fontSize=20, leading=22, textColor=NAVY)
    s["LogoSub"] = ParagraphStyle("LogoSub", fontName="Helvetica", fontSize=7.5, leading=10, textColor=SLATE,
                                   spaceBefore=2)
    s["MetaLine"] = ParagraphStyle("MetaLine", fontName="Helvetica", fontSize=9, leading=13, textColor=SLATE,
                                    alignment=TA_RIGHT)
    s["Eyebrow"] = ParagraphStyle("Eyebrow", fontName="Helvetica-Bold", fontSize=9, leading=12, textColor=ORANGE,
                                   spaceBefore=10, spaceAfter=4)
    s["DocTitle"] = ParagraphStyle("DocTitle", fontName="Helvetica-Bold", fontSize=25, leading=27, textColor=NAVY,
                                    spaceAfter=4)
    s["DocStrap"] = ParagraphStyle("DocStrap", fontName="Helvetica", fontSize=10, leading=14, textColor=SLATE,
                                    spaceAfter=12)
    s["SectionTitle"] = ParagraphStyle("SectionTitle", fontName="Helvetica-Bold", fontSize=16, leading=18,
                                        textColor=NAVY, spaceBefore=4, spaceAfter=2)
    s["SectionStrap"] = ParagraphStyle("SectionStrap", fontName="Helvetica", fontSize=9.5, leading=13,
                                        textColor=SLATE, spaceAfter=8)
    s["ClauseH4"] = ParagraphStyle("ClauseH4", fontName="Helvetica-Bold", fontSize=12.5, leading=15, textColor=NAVY,
                                    spaceBefore=12, spaceAfter=4)
    s["ClauseSubhead"] = ParagraphStyle("ClauseSubhead", fontName="Helvetica-Bold", fontSize=9.5, leading=13,
                                         textColor=NAVY, spaceBefore=6, spaceAfter=2)
    s["Body"] = ParagraphStyle("Body", fontName="Helvetica", fontSize=8.7, leading=12.5, textColor=INK,
                                spaceAfter=5)
    s["BodyBold"] = ParagraphStyle("BodyBold", fontName="Helvetica-Bold", fontSize=8.7, leading=12.5,
                                    textColor=INK, spaceAfter=5)
    s["ListItem"] = ParagraphStyle("ListItem", fontName="Helvetica", fontSize=8.7, leading=12.5, textColor=INK,
                                    spaceAfter=3, leftIndent=10, bulletIndent=0)
    s["Small"] = ParagraphStyle("Small", fontName="Helvetica", fontSize=8, leading=11, textColor=SLATE)
    s["KLabel"] = ParagraphStyle("KLabel", fontName="Helvetica-Bold", fontSize=6.6, leading=9, textColor=SLATE)
    s["KValue"] = ParagraphStyle("KValue", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=NAVY)
    s["CardTier"] = ParagraphStyle("CardTier", fontName="Helvetica-Bold", fontSize=16, leading=18, textColor=WHITE)
    s["CardTag"] = ParagraphStyle("CardTag", fontName="Helvetica", fontSize=7.5, leading=10, textColor=WHITE)
    s["CardAmt"] = ParagraphStyle("CardAmt", fontName="Helvetica-Bold", fontSize=22, leading=24, textColor=NAVY)
    s["CardAmtFlag"] = ParagraphStyle("CardAmtFlag", parent=s["CardAmt"], textColor=ORANGE)
    s["CardNote"] = ParagraphStyle("CardNote", fontName="Helvetica", fontSize=8, leading=11, textColor=SLATE)
    s["CardPerDay"] = ParagraphStyle("CardPerDay", fontName="Helvetica", fontSize=8.5, leading=12, textColor=INK)
    s["CardMonthlyLbl"] = ParagraphStyle("CardMonthlyLbl", fontName="Helvetica-Bold", fontSize=7, leading=9,
                                          textColor=SLATE)
    s["CardMonthlyVal"] = ParagraphStyle("CardMonthlyVal", fontName="Helvetica", fontSize=8.5, leading=12.5,
                                          textColor=INK)
    s["CardBullet"] = ParagraphStyle("CardBullet", fontName="Helvetica", fontSize=8, leading=11.5, textColor=INK,
                                      spaceAfter=3)
    s["CardBulletOff"] = ParagraphStyle("CardBulletOff", parent=s["CardBullet"], textColor=GREY_OFF)
    s["TblHead"] = ParagraphStyle("TblHead", fontName="Helvetica-Bold", fontSize=8.3, leading=10, textColor=WHITE,
                                   alignment=TA_CENTER)
    s["TblHeadLeft"] = ParagraphStyle("TblHeadLeft", parent=s["TblHead"], alignment=TA_LEFT)
    s["TblRowhead"] = ParagraphStyle("TblRowhead", fontName="Helvetica-Bold", fontSize=8, leading=11,
                                      textColor=NAVY)
    s["TblCell"] = ParagraphStyle("TblCell", fontName="Helvetica", fontSize=8, leading=11, textColor=INK,
                                   alignment=TA_CENTER)
    s["TblCellPrem"] = ParagraphStyle("TblCell", fontName="Helvetica-Bold", fontSize=8, leading=11,
                                       textColor=WARN_TEXT, alignment=TA_CENTER)
    s["Pullquote"] = ParagraphStyle("Pullquote", fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=NAVY)
    s["Attrib"] = ParagraphStyle("Attrib", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=SLATE,
                                  spaceBefore=4)
    s["SigLabel"] = ParagraphStyle("SigLabel", fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=SLATE)
    s["SigName"] = ParagraphStyle("SigName", fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=INK)
    s["Note"] = ParagraphStyle("Note", fontName="Helvetica", fontSize=8.3, leading=12, textColor=INK)
    s["NoteBold"] = ParagraphStyle("NoteBold", fontName="Helvetica-Bold", fontSize=8.3, leading=12,
                                    textColor=WARN_TEXT)
    s["DefTerm"] = ParagraphStyle("DefTerm", fontName="Helvetica", fontSize=8.5, leading=12.5, textColor=INK,
                                   spaceAfter=6)
    return s


def _rule(width=CONTENT_W, height=1.4, color=NAVY):
    t = Table([[""]], colWidths=[width], rowHeights=[height])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), color)]))
    return t


def _letterhead(styles, right_pairs, tagline=None):
    """right_pairs: list of (label, value) shown right-aligned, stacked.
    tagline: optional plain-text replacement for the line under the logo."""
    logo = Paragraph('Handyman<font color="#F4591C">.ae</font>', styles["Logo"])
    sub = Paragraph(_xml_escape(tagline) if tagline else "Dubai &middot; United Arab Emirates", styles["LogoSub"])
    right = [Paragraph("%s <b>%s</b>" % (lbl, val), styles["MetaLine"]) for lbl, val in right_pairs]
    t = Table([[[logo, sub], right]], colWidths=[CONTENT_W * 0.55, CONTENT_W * 0.45])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return [t, _rule(), Spacer(1, 10)]


def _footer_drawer(page_label_for, contact_text=None):
    def _draw(canvas, doc):
        canvas.saveState()
        y = MARGIN - 2 * mm
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(1.4)
        canvas.line(MARGIN, y + 12, PAGE_W - MARGIN, y + 12)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.setFillColor(NAVY)
        canvas.drawString(MARGIN, y, "Handyman.ae")
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(SLATE)
        label = page_label_for(canvas.getPageNumber())
        text = "%s  ·  %s" % (contact_text or "600 55 4456  ·  handyman.ae", label)
        canvas.drawRightString(PAGE_W - MARGIN, y, text)
        canvas.restoreState()
    return _draw


def _kv_strip(styles, pairs, cols=4):
    """pairs: list of (label, value). Renders as an evenly-split grid, wash bg."""
    cells = []
    for lbl, val in pairs:
        cells.append([Paragraph(lbl.upper(), styles["KLabel"]), Paragraph(val or "—", styles["KValue"])])
    rows = [cells[i:i + cols] for i in range(0, len(cells), cols)]
    col_w = CONTENT_W / cols
    t = Table(rows, colWidths=[col_w] * cols)
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), WASH),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    t.setStyle(TableStyle(style))
    return t


def _data_table(styles, header, rows, prem_col=None, rowhead_col=0, col_widths=None, extra_col=None):
    """header: list of header cell strings (first may be a left-aligned rowhead column).
    rows: list of row tuples matching header length. prem_col: 0-based column index to
    highlight (orange header + orange-tinted body, matching the original's .premcol/.prem
    styling - normally the Premium column). extra_col: an additional column to give the
    same body-cell highlight (no header colour change) - used on the contract's package
    table, which always highlights Premium *and* whichever tier the customer picked."""
    highlight_cols = set()
    if prem_col is not None:
        highlight_cols.add(prem_col)
    if extra_col is not None:
        highlight_cols.add(extra_col)
    head_cells = []
    for i, h in enumerate(header):
        style = styles["TblHeadLeft"] if i == rowhead_col else styles["TblHead"]
        head_cells.append(Paragraph(h, style))
    body_rows = []
    for row in rows:
        cells = []
        for i, val in enumerate(row):
            if i == rowhead_col:
                cells.append(Paragraph(str(val), styles["TblRowhead"]))
            elif i in highlight_cols:
                cells.append(Paragraph(str(val), styles["TblCellPrem"]))
            else:
                cells.append(Paragraph(str(val), styles["TblCell"]))
        body_rows.append(cells)
    data = [head_cells] + body_rows
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, LINE_SOFT),
        ("LINEBELOW", (0, -1), (-1, -1), 0.75, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (rowhead_col, 0), (rowhead_col, -1), "LEFT"),
        ("BACKGROUND", (rowhead_col, 1), (rowhead_col, -1), WASH),
    ]
    if prem_col is not None:
        style.append(("BACKGROUND", (prem_col, 0), (prem_col, 0), ORANGE))
    for c in highlight_cols:
        style.append(("BACKGROUND", (c, 1), (c, -1), WARN_BG))
    for r in range(1, len(data), 2):
        style.append(("BACKGROUND", (0, r), (-1, r), colors.Color(0.984, 0.988, 0.996)))
        for c in highlight_cols:
            style.append(("BACKGROUND", (c, r), (c, r), WARN_BG))
    t.setStyle(TableStyle(style))
    return t


# ---------------------------------------------------------------------------
# Proposal PDF
# ---------------------------------------------------------------------------

def _package_card(styles, tier_idx, tier_name, tag, annual, is_custom, property_type, highlights=None):
    flagged = tier_idx == 1
    per_day = annual / 365.0
    plan = ap.monthly_plan(annual)
    if highlights is None:
        highlights = ap.card_highlights(property_type)[tier_idx]

    head_bg = ORANGE if flagged else NAVY
    tag_text = "Best value" if flagged else tag
    header_cell = [Paragraph(tier_name, styles["CardTier"]), Paragraph(tag_text.upper(), styles["CardTag"])]
    if flagged:
        header_cell.append(Spacer(1, 3))
        header_cell.append(Paragraph("RECOMMENDED", ParagraphStyle(
            "Ribbon", fontName="Helvetica-Bold", fontSize=6.5, textColor=WHITE)))
    header = Table([[header_cell]], colWidths=[None])
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), head_bg),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))

    amt_style = styles["CardAmtFlag"] if flagged else styles["CardAmt"]
    price_block = [
        Paragraph('<font size="9" color="#5F6B80">AED</font> ' + money(annual), amt_style),
        Paragraph("per year, paid upfront &middot; incl. VAT", styles["CardNote"]),
        Spacer(1, 3),
        _side_note(styles, "Works out at <b>AED %s a day</b>" % ("%.2f" % per_day)),
    ]
    if is_custom and tier_idx is not None:
        pass  # per-proposal override note is shown once beneath the cards, not per-card

    monthly_block = [
        Paragraph("OR PAY MONTHLY", styles["CardMonthlyLbl"]),
        Spacer(1, 2),
        Paragraph("<b>AED %s</b> deposit, then<br/><b>AED %s</b> &times; 11 months" %
                  (money2(plan["deposit"]), money2(plan["monthly"])), styles["CardMonthlyVal"]),
    ]
    monthly_table = Table([[monthly_block]], colWidths=[CONTENT_W])  # width fixed below when nested
    monthly_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WASH),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))

    bullets = []
    for text, off in highlights:
        dot_color = "#C9D0DB" if off else "#F4591C"
        style = styles["CardBulletOff"] if off else styles["CardBullet"]
        bullets.append(Paragraph('<font color="%s">&#9679;</font>&nbsp;&nbsp;%s' % (dot_color, text), style))

    rows = [
        [header],
        [price_block],
        [monthly_table],
        [bullets],
    ]
    card = Table(rows, colWidths=[None])
    card.setStyle(TableStyle([
        ("LEFTPADDING", (0, 1), (0, 1), 10), ("RIGHTPADDING", (0, 1), (0, 1), 10),
        ("TOPPADDING", (0, 1), (0, 1), 10), ("BOTTOMPADDING", (0, 1), (0, 1), 8),
        ("LEFTPADDING", (0, 2), (0, 2), 0), ("RIGHTPADDING", (0, 2), (0, 2), 0),
        ("TOPPADDING", (0, 2), (0, 2), 0), ("BOTTOMPADDING", (0, 2), (0, 2), 0),
        ("LEFTPADDING", (0, 3), (0, 3), 10), ("RIGHTPADDING", (0, 3), (0, 3), 10),
        ("TOPPADDING", (0, 3), (0, 3), 10), ("BOTTOMPADDING", (0, 3), (0, 3), 10),
        ("BOX", (0, 0), (-1, -1), 1.2 if flagged else 0.75, ORANGE if flagged else LINE),
        ("LINEBELOW", (0, 0), (0, 0), 0.5, LINE_SOFT),
        ("LINEBELOW", (0, 1), (0, 1), 0.5, LINE_SOFT),
        ("LINEBELOW", (0, 2), (0, 2), 0.5, LINE_SOFT),
    ]))
    return card


def _side_note(styles, text):
    t = Table([[Paragraph(text, styles["CardPerDay"])]], colWidths=[None])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WASH),
        ("LINEBEFORE", (0, 0), (0, 0), 2, ORANGE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _highlight_box(styles, html, bold_prefix=None):
    text = html
    t = Table([[Paragraph(text, styles["Note"])]], colWidths=[CONTENT_W])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WARN_BG),
        ("LINEBEFORE", (0, 0), (0, 0), 2.5, ORANGE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


PROPOSAL_LAYOUT_KEYS = ("eyebrow", "titleA", "titleB", "strap", "quote", "quoteAttrib", "tagline", "footer",
                        "inclTitle", "cmpTitle", "cmpStrap", "payTitle", "payStrap",
                        "payRowUpfront", "payRowDeposit", "payRowInstal", "payRowTotal")
CONTRACT_LAYOUT_KEYS = ("eyebrow", "titleA", "titleB", "strap", "tagline", "footer", "providerEntity")
LAYOUT_LABELS = {
    "eyebrow": "Small heading above the title", "titleA": "Title - first part",
    "titleB": "Title - highlighted part (orange)", "strap": "Line under the title",
    "quote": "Quote at the bottom", "quoteAttrib": "Quote attribution",
    "tagline": "Line under the logo (every page)", "footer": "Footer text (every page, before the page number)",
    "providerEntity": "Line under handyman.ae in the signature block",
    "inclTitle": "Heading of the \"included in every package\" box", "cmpTitle": "Comparison table heading",
    "cmpStrap": "Line under the comparison heading", "payTitle": "Payment options heading",
    "payStrap": "Line under the payment options heading", "payRowUpfront": "Payment table row 1 label",
    "payRowDeposit": "Payment table row 2 label", "payRowInstal": "Payment table row 3 label",
    "payRowTotal": "Payment table row 4 label",
}


def default_proposal_layout(property_type):
    copy = ap.COPY["commercial" if property_type == "commercial" else "residential"]
    return {"eyebrow": copy["eyebrow"], "titleA": copy["title_a"].strip(), "titleB": copy["title_b"],
            "strap": copy["strap"], "quote": copy["quote"], "quoteAttrib": "TEGAN BRADLEY · OPERATIONS MANAGER",
            "tagline": "Dubai · United Arab Emirates", "footer": "600 55 4456  ·  handyman.ae",
            "inclTitle": "INCLUDED IN EVERY PACKAGE", "cmpTitle": "What each package covers",
            "cmpStrap": "Side-by-side comparison of the three cover levels.", "payTitle": "Payment options",
            "payStrap": "Choose whichever suits you — the cover is identical either way.",
            "payRowUpfront": "Paid upfront — total for the year", "payRowDeposit": "Monthly plan — deposit",
            "payRowInstal": "Monthly plan — 11 instalments of", "payRowTotal": "Monthly plan — total for the year"}


def default_contract_layout(property_type):
    copy = ap.COPY["commercial" if property_type == "commercial" else "residential"]
    return {"eyebrow": copy["eyebrow"], "titleA": "Annual Maintenance", "titleB": "Contract.",
            "strap": copy["contract_strap"], "tagline": "Dubai · United Arab Emirates",
            "footer": "600 55 4456  ·  handyman.ae", "providerEntity": "(GIJO Maintenance Services)"}


def sanitize_layout(layout, keys):
    """Keeps only known keys with non-empty text; anything missing falls back to the default wording."""
    if layout is None:
        return {}
    if not isinstance(layout, dict):
        raise ValueError("layout must be an object")
    clean = {}
    for k in keys:
        v = layout.get(k)
        if v is None:
            continue
        v = str(v)
        if len(v) > 2000:
            raise ValueError("a cover/page text field is too long (max 2000 characters)")
        if v.strip():
            clean[k] = v
    return clean


def _plain(text, ctx=None):
    """Escaped text for layout fields (tokens + **bold** supported)."""
    return _rich(text, ctx or {})


def _upper_plain(text, ctx=None):
    t = _TOKEN_RE.sub(lambda m: str((ctx or {}).get(m.group(1), m.group(0))), str(text or ""))
    return _xml_escape(t).upper()


def _title_html(lay, ctx):
    return _plain(lay["titleA"], ctx) + " " + '<font color="#F4591C">%s</font>' % _plain(lay["titleB"], ctx)


def _build_proposal_pdf(data, total_pages):
    """data: clientName, propertyAddress, propertyType, acUnits, validityDays,
    createdDate (date), validUntil (date), prices [basic,standard,premium],
    isCustom (bool), highlightsTemplate (optional admin-edited 3x5 grid,
    see amc_proposal_highlights in server.py - None uses the default copy),
    wording (optional {"terms": [str], "note": str} - None uses the standard
    wording; see default_proposal_wording())."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=MARGIN, bottomMargin=MARGIN + 6 * mm,
                             leftMargin=MARGIN, rightMargin=MARGIN, title="AMC Proposal - %s" % data["clientName"])

    property_type = data["propertyType"]
    copy = ap.COPY["commercial" if property_type == "commercial" else "residential"]
    units = data["acUnits"]
    prices = data["prices"]
    client = data["clientName"] or "—"
    prop = data["propertyAddress"] or "—"

    wording = data.get("wording") or default_proposal_wording()
    lay = {**default_proposal_layout(property_type), **(wording.get("layout") or {})}
    pctx = {"client": client, "property": prop, "date": fmt_date(data["createdDate"]),
            "validUntil": fmt_date(data["validUntil"]), "units": str(units)}
    story = []
    story += _letterhead(styles, [("Date", fmt_date(data["createdDate"])),
                                   ("Valid until", fmt_date(data["validUntil"]))], tagline=lay["tagline"])
    story.append(Paragraph(_upper_plain(lay["eyebrow"], pctx), styles["Eyebrow"]))
    story.append(Paragraph(_title_html(lay, pctx), styles["DocTitle"]))
    story.append(Paragraph(_plain(lay["strap"], pctx), styles["DocStrap"]))
    story.append(_kv_strip(styles, [
        ("Prepared for", client),
        (copy["prop_label"], prop),
        ("Type", ap.TYPE_LABEL[property_type]),
        ("AC units", "%d%s" % (units, " unit" if units == 1 else " units")),
    ]))
    story.append(Spacer(1, 14))

    highlights_grid = ap.card_highlights(property_type, wording.get("highlights") or data.get("highlightsTemplate"))
    cards = [_package_card(styles, i, ap.TIERS[i], ap.TAGS[i], prices[i], data.get("isCustom"), property_type,
                           highlights=highlights_grid[i])
             for i in range(3)]
    gutter = 4 * mm
    card_w = (CONTENT_W - 2 * gutter) / 3
    for c in cards:
        c._argW = [card_w]
    card_row = Table([cards], colWidths=[card_w] * 3, spaceBefore=0)
    card_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), gutter),
        ("RIGHTPADDING", (2, 0), (2, 0), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(card_row)
    story.append(Spacer(1, 14))

    incl_items = ap.always_included(property_type, wording.get("included"))
    left_items = incl_items[0::2]
    right_items = incl_items[1::2]

    def _incl_col(items):
        return [Paragraph('<font color="#F4591C">&#10003;</font>&nbsp;&nbsp;%s' % _rich(t, pctx), styles["Body"])
                for t in items]

    incl_table = Table([[_incl_col(left_items), _incl_col(right_items)]], colWidths=[CONTENT_W / 2] * 2)
    incl_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    incl_box = Table([
        [Paragraph(_plain(lay["inclTitle"], pctx), ParagraphStyle(
            "InclHead", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY))],
        [incl_table],
    ], colWidths=[CONTENT_W])
    incl_box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, LINE), ("LINEBEFORE", (0, 0), (0, -1), 2.5, NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, 0), (0, 0), 6),
        ("TOPPADDING", (0, 1), (0, 1), 2), ("BOTTOMPADDING", (0, 1), (0, 1), 10),
    ]))
    story.append(incl_box)

    story.append(PageBreak())
    story += _letterhead(styles, [(client, fmt_date(data["createdDate"]))], tagline=lay["tagline"])
    story.append(Paragraph(_plain(lay["cmpTitle"], pctx), styles["SectionTitle"]))
    story.append(Paragraph(_plain(lay["cmpStrap"], pctx), styles["SectionStrap"]))
    cmp_rows = ap.comparison_rows(property_type, wording.get("comparison"))
    cmp_header = ["Service", "Basic", "Standard", "Premium"]
    story.append(_data_table(styles, cmp_header, [tuple(_plain(c, pctx) for c in (r[0], r[1][0], r[1][1], r[1][2])) for r in cmp_rows],
                              prem_col=3, col_widths=[CONTENT_W * 0.34] + [CONTENT_W * 0.22] * 3))
    story.append(Spacer(1, 8))

    story.append(Paragraph(_plain(lay["payTitle"], pctx), styles["SectionTitle"]))
    story.append(Paragraph(_plain(lay["payStrap"], pctx), styles["SectionStrap"]))
    plans = [ap.monthly_plan(p) for p in prices]
    pay_rows = [
        (_plain(lay["payRowUpfront"], pctx),) + tuple("AED %s" % money(p) for p in prices),
        (_plain(lay["payRowDeposit"], pctx),) + tuple("AED %s" % money2(pl["deposit"]) for pl in plans),
        (_plain(lay["payRowInstal"], pctx),) + tuple("AED %s" % money2(pl["monthly"]) for pl in plans),
        (_plain(lay["payRowTotal"], pctx),) + tuple("AED %s" % money(pl["planTotal"]) for pl in plans),
    ]
    story.append(_data_table(styles, ["Option", "Basic", "Standard", "Premium"], pay_rows, prem_col=3,
                              col_widths=[CONTENT_W * 0.40] + [CONTENT_W * 0.20] * 3))
    story.append(Spacer(1, 8))

    story.append(Paragraph("TERMS", ParagraphStyle("TermsHead", fontName="Helvetica-Bold", fontSize=8.5,
                                                     textColor=SLATE, spaceAfter=6)))
    term_paras = [Paragraph("%d. %s" % (i + 1, _rich(t, pctx)), styles["Body"])
                  for i, t in enumerate(wording["terms"])]
    half = (len(term_paras) + 1) // 2
    terms_table = Table([[term_paras[:half], term_paras[half:]]], colWidths=[CONTENT_W / 2] * 2)
    terms_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                      ("LEFTPADDING", (1, 0), (1, 0), 14)]))
    story.append(terms_table)
    note_text = wording["note"]
    prefix = "Please note:"
    if note_text.startswith(prefix):
        note_text = note_text[len(prefix):].lstrip()
    story.append(_highlight_box(styles, '<b><font color="#8C3007">Please note:</font></b> ' + _rich(note_text, pctx)))
    story.append(Spacer(1, 8))

    quote_cell = [
        Paragraph(_plain(lay["quote"], pctx), styles["Pullquote"]),
        Paragraph(_plain(lay["quoteAttrib"], pctx), styles["Attrib"]),
    ]
    quote_box = Table([[quote_cell]], colWidths=[CONTENT_W * 0.6])
    quote_box.setStyle(TableStyle([("LINEBEFORE", (0, 0), (0, 0), 2.5, ORANGE),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 12)]))
    sig_cell = [Paragraph("SIGNED OFF BY", styles["SigLabel"])]
    if os.path.isfile(SIGNATURE_PATH):
        sig_cell.append(Image(SIGNATURE_PATH, width=30 * mm, height=10 * mm))
    sig_cell.append(Paragraph("Tegan Bradley", styles["SigName"]))
    sig_cell.append(Paragraph("Operations Manager, Handyman.ae", styles["Small"]))
    sign_row = Table([[quote_box, sig_cell]], colWidths=[CONTENT_W * 0.62, CONTENT_W * 0.38])
    sign_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    story.append(sign_row)

    def page_label(n):
        return "Page %d of %d" % (n, total_pages) if total_pages else "Page %d" % n

    footer = _footer_drawer(page_label, lay["footer"])
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue(), doc.page


def generate_proposal_pdf(data):
    _, pages = _build_proposal_pdf(data, 0)
    pdf, _ = _build_proposal_pdf(data, pages)
    return pdf


def default_proposal_wording():
    return {"terms": list(ap.PROPOSAL_TERMS), "note": ap.PROPOSAL_NOTE}


PROPOSAL_TOKENS = {
    "client": "Customer name", "property": "Property / address", "date": "Proposal date",
    "validUntil": "Valid-until date", "units": "Number of AC units",
}


def sanitize_highlights(h):
    """The 3 (packages) x 5 (bullets) inclusions grid. Raises ValueError if malformed."""
    if not isinstance(h, list) or len(h) != 3 or any(not isinstance(t, list) or len(t) != 5 for t in h):
        raise ValueError("package inclusions must be 3 packages of exactly 5 bullets")
    clean = []
    for tier in h:
        row = []
        for b in tier:
            if not isinstance(b, dict) or not isinstance(b.get("text"), str):
                raise ValueError("each inclusion bullet needs text")
            if len(b["text"]) > 300:
                raise ValueError("an inclusion bullet is too long (max 300 characters)")
            row.append({"text": b["text"], "off": bool(b.get("off"))})
        clean.append(row)
    return clean


def sanitize_comparison(rows):
    """The comparison table: rows of [label, Basic, Standard, Premium]. Raises ValueError if malformed."""
    if not isinstance(rows, list) or len(rows) > 30:
        raise ValueError("the comparison table must be a list of at most 30 rows")
    clean = []
    for r in rows:
        if not isinstance(r, (list, tuple)):
            raise ValueError("each comparison row needs a label and three values")
        cells = ["" if c is None else str(c) for c in r][:4]
        cells += [""] * (4 - len(cells))
        if any(len(c) > 300 for c in cells):
            raise ValueError("a comparison cell is too long (max 300 characters)")
        if any(c.strip() for c in cells):
            clean.append(cells)
    if not clean:
        raise ValueError("the comparison table needs at least one row")
    return clean


def sanitize_included(items):
    if not isinstance(items, list) or len(items) > 40:
        raise ValueError("the included list must be at most 40 items")
    clean = []
    for t in items:
        t = "" if t is None else str(t)
        if len(t) > 300:
            raise ValueError("an included item is too long (max 300 characters)")
        if t.strip():
            clean.append(t)
    return clean


def sanitize_wording(w):
    """Validates edited proposal wording. Raises ValueError with a readable message."""
    if not isinstance(w, dict):
        raise ValueError("wording must be an object with terms and note")
    terms, note = w.get("terms"), w.get("note")
    if not isinstance(terms, list) or len(terms) > 100:
        raise ValueError("terms must be a list of at most 100 items")
    clean_terms = []
    for t in terms:
        t = "" if t is None else str(t)
        if len(t) > MAX_TEXT:
            raise ValueError("a term is too long (max %d characters)" % MAX_TEXT)
        if t.strip():
            clean_terms.append(t)
    note = "" if note is None else str(note)
    if len(note) > MAX_TEXT:
        raise ValueError("the note is too long (max %d characters)" % MAX_TEXT)
    out = {"terms": clean_terms, "note": note}
    layout = sanitize_layout(w.get("layout"), PROPOSAL_LAYOUT_KEYS)
    if layout:
        out["layout"] = layout
    if w.get("highlights") is not None:
        out["highlights"] = sanitize_highlights(w["highlights"])
    if w.get("comparison") is not None:
        out["comparison"] = sanitize_comparison(w["comparison"])
    if w.get("included"):
        out["included"] = sanitize_included(w["included"])
    return out


# ---------------------------------------------------------------------------
# Contract PDF - 9 pages, verbatim clause text from the original tool
# ---------------------------------------------------------------------------

DEFINITIONS_PAGE3 = [
    ("handyman.ae", "Brand name for the annual maintenance contract arm of GIJO Maintenance Services, "
                     "trading as handyman.ae, Dubai's Handyman."),
    ("AMC", "Annual Maintenance Contract: an agreement between handyman.ae and a specified customer to "
            "provide specified maintenance services over a 12-month period."),
    ("AC Unit", "A unitary air conditioning system, including split, multi-system, package, or chilled "
                "water types."),
    ("MEP Callout", "An on-demand service visit to inspect and diagnose issues in Mechanical (AC, exhaust "
                     "fans, water pumps), Electrical (breakers, wiring, sockets), or Plumbing (leaks, "
                     "drainage, valves, pressure) systems. A callout includes an on-site technician visit, "
                     "visual inspection, basic fault diagnosis, and initial minor troubleshooting where "
                     "applicable."),
    ("AC Emergency", "Complete failure of all AC units on one or more floors of a property — either "
                      "no cooling or full power failure."),
    ("AC Non-Emergency", "Failure of a single unit or less than a full floor, reduced or intermittent "
                          "cooling, leakage, abnormal sound or smell."),
    ("Plumbing Emergency", "A major water leak, interruption of water supply, or significant reduction in "
                            "water pressure through multiple outlets."),
    ("Plumbing Non-Emergency", "A minor contained water leak or limited reduction in water pressure."),
]

DEFINITIONS_PAGE4 = [
    ("Electrical Emergency", "A complete interruption of power supply to one floor or more within a "
                              "villa, or a full apartment. Excludes faults in exterior fittings, gardens, "
                              "or irrigation systems."),
    ("Electrical Non-Emergency", "Failure of sockets and switches, or tripping of a breaker affecting a "
                                  "limited area of the property."),
    ("Standard Business Hours", "8:30am to 5:30pm, Monday to Saturday. All other periods are considered "
                                 "Outside of Standard Business Hours."),
]

EXCLUSIONS_9 = [
    "Carpentry repairs beyond minor fixed woodwork, including garden furniture",
    "Audio visual installation, home automation systems, and data/Wi-Fi cabling",
    "Construction, property modification, or bathroom/kitchen renovation",
    "Damage caused by flooding or other natural disasters",
    "Locksmith services",
    "Exterior property texturing, painting, or repair",
    "Masonry, tiling & grouting beyond minor touch-ups",
    "Wall repairs, settlement cracks, mould, or damp issues",
    "Pest control",
    "Gardening, irrigation systems, and yard work",
    "Household appliance servicing or repair",
    "Swimming pool maintenance",
    "Door & window repair or replacement",
    "Automated garage doors, gates, CCTV & building management systems",
]


# ---------------------------------------------------------------------------
# Editable contract body.
#
# Everything after the cover details is a list of "blocks" so a contract's
# wording (terms, clauses, tables, page breaks) can be edited per contract and
# the PDF regenerated. Block shapes:
#   {"type": "title"|"heading"|"subheading"|"p"|"callout", "text": str, "indent": bool (p only)}
#   {"type": "bullets"|"numbered", "items": [str], "columns": 1|2}
#   {"type": "definitions", "items": [[term, description], ...]}
#   {"type": "checklist", "title": str, "items": [str]}
#   {"type": "table", "header": [str], "rows": [[str]], "auto": "summary"|"payment"|"package" (optional)}
#   {"type": "pagebreak"}  - starts a new page with the letterhead
#   {"type": "signature"}  - the two-column signed-agreement block
# Text is plain: **bold** is supported, {{tokens}} are filled from the
# contract's details (see CONTRACT_TOKENS) so edited wording stays correct
# when the package, client or dates are later changed.
# ---------------------------------------------------------------------------

import re
from xml.sax.saxutils import escape as _xml_escape

CONTRACT_TOKENS = {
    "client": "Customer name",
    "property": "Property / address",
    "date": "Contract date",
    "startDate": "Start date",
    "tier": "Selected package (Basic / Standard / Premium)",
    "units": "Number of AC units",
    "matThreshold": "Materials covered per visit for the selected package (AED)",
    "callOutsBasic": "Basic package call outs per year",
    "callOutsStandard": "Standard package call outs per year",
}

_TOKEN_RE = re.compile(r"\{\{(\w+)\}\}")
_BLOCK_TYPES = ("title", "heading", "subheading", "p", "callout", "bullets", "numbered", "definitions",
                "checklist", "table", "pagebreak", "signature")
_AUTO_TABLES = ("summary", "payment", "package")
MAX_BLOCKS = 600
MAX_TEXT = 20000


def default_contract_terms():
    return [
        {"type": "callout", "text": "This document is the full 12-month Annual Maintenance Contract. "
                                    "Cover begins once this agreement is signed and the first payment "
                                    "(full amount, or the 25% deposit under the Monthly Plan) is received."},
        {"type": "pagebreak"},
        {"type": "title", "text": "Welcome to the handyman.ae Family"},
        {"type": "p", "text": "Dear Valued Customer,"},
        {"type": "p", "text": "Thank you for considering handyman.ae for the care of your home. We know that "
                              "inviting a maintenance partner into your property is a decision built on trust "
                              "— and we don't take that lightly."},
        {"type": "p", "text": "We started handyman.ae on a simple idea: your home deserves consistent, honest, "
                              "and skilled care, delivered by people who treat it like their own. Whether it's "
                              "keeping your AC running efficiently through Dubai's summer, catching a small "
                              "plumbing issue before it becomes a big one, or simply being there when you need "
                              "us — our goal is to become a maintenance partner you don't have to think twice "
                              "about."},
        {"type": "p", "text": "This Annual Maintenance Contract sets out exactly what you can expect from us: "
                              "clear pricing, real inclusions, and a team that shows up when we say we will. No "
                              "hidden surprises, no fine print designed to catch you out — just fair, "
                              "transparent terms and a genuine commitment to looking after your property "
                              "year-round."},
        {"type": "p", "text": "We're proud to welcome you as part of the handyman.ae community, and we look "
                              "forward to building a long-term relationship with you and your home."},
        {"type": "p", "text": "Warm regards,\n**The handyman.ae Team**"},
        {"type": "checklist", "title": "OUR COMMITMENT TO YOU", "items": [
            "Fair, transparent pricing — every cost is itemised, nothing is hidden in the fine print.",
            "Real technicians, properly trained, wearing branded uniform, on every visit.",
            "We treat your property with the same care and respect we'd want for our own homes.",
            "Honest advice — if something doesn't need fixing yet, we'll tell you.",
            "A long-term relationship, not a one-off job — we want to be your maintenance partner for "
            "years to come.",
            "Responsive support — our team is reachable and accountable, every time you need us.",
        ]},
        {"type": "pagebreak"},
        {"type": "heading", "text": "1. Summary of Services"},
        {"type": "p", "text": "This Annual Maintenance Contract covers AC, plumbing, and electrical systems at "
                              "your property. The table below provides a quick overview — full details follow "
                              "in the sections after."},
        {"type": "table", "auto": "summary", "header": ["This contract", "Details"], "rows": []},
        {"type": "heading", "text": "2. Definitions"},
        {"type": "definitions", "items": [list(i) for i in DEFINITIONS_PAGE3]},
        {"type": "pagebreak"},
        {"type": "definitions", "items": [list(i) for i in DEFINITIONS_PAGE4]},
        {"type": "heading", "text": "3. Scope & Scheduling of Annual Maintenance Work"},
        {"type": "subheading", "text": "3.1 Pre-Contract Property Check"},
        {"type": "p", "text": "Prior to activation, a member of our bookings team will confirm the number of AC "
                              "units at the property and check they are cooling correctly. This is an initial "
                              "review only, not a full technical inspection. Any units not running or cooling "
                              "correctly will be diagnosed separately, with diagnosis charges credited back to "
                              "the account once rectification work is approved, completed, and paid."},
        {"type": "subheading", "text": "3.2 AC Servicing Schedule"},
        {"type": "p", "text": "AC servicing frequency depends on your selected package:"},
        {"type": "bullets", "items": ["Basic — 2x per AC unit, per year", "Standard — 3x per AC unit, per year",
                                      "Premium — 4x per AC unit, per year"]},
        {"type": "p", "text": "Upon signing and receipt of payment, the bookings team will schedule the first "
                              "visit and all remaining visits through our automated booking system. Customers "
                              "will be reminded of scheduled dates by phone, email, or text message. We allow a "
                              "2-week window either side of scheduled visits to accommodate your availability."},
        {"type": "subheading", "text": "3.3 Call Outs"},
        {"type": "p", "text": "Basic package customers receive {{callOutsBasic}} limited, free call outs per year "
                              "for AC, plumbing, and electrical issues. Standard receive {{callOutsStandard}} "
                              "limited, free call outs per year for AC, plumbing, and electrical issues and "
                              "Premium package customers receive unlimited call outs for the duration of the "
                              "contract, subject to fair and reasonable use. Any call out beyond the Basic "
                              "allowance will be charged at our standard discounted ad-hoc rate."},
        {"type": "subheading", "text": "3.4 Response Times"},
        {"type": "bullets", "items": [
            "Emergency issues: response within 90 minutes from receipt of access pass",
            "Non-emergency issues: response within 4 hours, during Standard Business Hours"]},
        {"type": "p", "text": "handyman.ae provides maintenance services on a reasonable endeavours basis but "
                              "shall not be liable for any loss, damage, or claim arising from a failure to meet "
                              "a response time where access or other conditions outside our control prevented "
                              "timely attendance."},
        {"type": "pagebreak"},
        {"type": "heading", "text": "4. Contract Inclusions & Exclusions"},
        {"type": "subheading", "text": "4.1 What's Included"},
        {"type": "p", "text": "In the event of hardware failure during the contract period, handyman.ae will "
                              "only quote and charge for materials not listed below — labour to install "
                              "replacement parts is provided free of charge on all covered items."},
        {"type": "bullets", "items": [
            "Plumbing: PVC, PPR, GI pipe and fittings, check valves, flexible hoses",
            "Electrical: Standard DB breakers",
            "AC: Capacitors, contactors, copper fittings & flare nuts, charging valves, filter driers & "
            "strainers, compressor wire, stem valve and sight glass"]},
        {"type": "p", "text": "The package also provides free refrigerant (R22 – Freon) for top-ups where "
                              "required, assuming R22 remains available and suitable for your unit. Materials "
                              "and parts up to the threshold shown below for your tier are covered without "
                              "additional charge: **AED {{matThreshold}} per visit ({{tier}})**."},
        {"type": "subheading", "text": "4.2 What's Not Included"},
        {"type": "p", "text": "Replacement hardware for AC, electrical, or plumbing systems not listed in Section "
                              "4.1 is not covered. Where parts are required to restore equipment to working "
                              "order, these will be quoted and charged for materials only — labour remains free "
                              "of charge. Examples include:"},
        {"type": "bullets", "items": [
            "Plumbing Fixtures — pumps, pressure vessels & switches, taps, shower heads & hoses, WC "
            "components, water heaters, valves, sinks, baths, washing machine hoses",
            "Electrical Fixtures — sockets, switches, ELCBs and distribution board components, light "
            "bulbs, ballasts, transformers, cabling, fittings",
            "AC Hardware — compressors, thermostats, fan motors & blades, actuator valves & motors, "
            "transformers, insulation tape & adhesive"]},
        {"type": "p", "text": "Gas leak repair labour is provided at a discount versus standard ad-hoc rates; if "
                              "a full regas or different refrigerant type is required, this is chargeable for "
                              "materials and labour at the same discounted rate."},
        {"type": "pagebreak"},
        {"type": "heading", "text": "5. Payment Terms"},
        {"type": "subheading", "text": "5.1 Payment Options"},
        {"type": "p", "text": "Customers may choose between two payment options for their Annual Maintenance "
                              "Contract:"},
        {"type": "p", "indent": True, "text": "**One-Time Payment** — the full annual contract value is paid "
                                              "upfront, in advance of contract activation."},
        {"type": "p", "indent": True, "text": "**Monthly Plan** — a 25% deposit is paid upfront (counted as the "
                                              "first instalment), with the remaining balance split across 11 "
                                              "further monthly instalments. A service markup applies to the "
                                              "Monthly Plan to reflect the flexibility of spreading payment "
                                              "across the year: 8% for Basic, Standard, and Premium."},
        {"type": "table", "auto": "payment", "header": ["This contract", "Selected terms"], "rows": []},
        {"type": "subheading", "text": "5.2 General Payment Conditions"},
        {"type": "bullets", "items": [
            "Payment can be made by cash, cheque, bank transfer, or credit card.",
            "Any additional hardware not covered under Section 4.1 must be paid for on completion of "
            "installation.",
            "Where quoted additional works exceed AED 1,500, a 50% deposit or purchase order is required "
            "before parts are sourced, with the balance payable on completion.",
            "All contracts are subject to 5% VAT in compliance with UAE federal law. handyman.ae acts as a "
            "collection agent on behalf of the relevant taxation authority.",
            "Missed monthly instalments may result in suspension of contract benefits (including call out "
            "and servicing entitlements) until payment is brought up to date."]},
        {"type": "pagebreak"},
        {"type": "heading", "text": "6. Hardware Installation & Warranty Terms"},
        {"type": "numbered", "items": [
            "All hardware installed under this contract is covered by a full 12-month warranty from the date "
            "of installation.",
            "Any hardware or parts required to rectify a defect covered by this contract must be sourced from "
            "handyman.ae to maintain warranty terms. Replacement parts sourced from a third party will not be "
            "permitted nor installed on any AC, plumbing, or electrical hardware covered by this contract.",
            "Replacement hardware provision and timescales are subject to availability within the UAE. "
            "handyman.ae will make best endeavours to source necessary parts but shall not be held liable for "
            "delays caused by supplier unavailability.",
            "Where handyman.ae recommends replacing a component and the customer chooses not to proceed "
            "within 30 days of notification, or has the work carried out by another provider, that hardware "
            "unit will no longer be considered under contract or warranty.",
            "All diagnosis of hardware failure must be undertaken by handyman.ae technicians. Diagnosis, "
            "testing, maintenance, or repair by an external contractor will void the warranty on that unit "
            "and exclude it from the remaining contract period."]},
        {"type": "heading", "text": "7. General Contract Terms"},
        {"type": "subheading", "text": "7.1 Contract Commencement & Term"},
        {"type": "p", "text": "This contract commences upon receipt of payment (full payment, or the 25% deposit "
                              "under the Monthly Plan) and runs for an initial period of 12 months, unless "
                              "otherwise agreed in writing."},
        {"type": "subheading", "text": "7.2 Community & Property Access"},
        {"type": "p", "text": "Maintenance visits are subject to community and property access being available. "
                              "A responsible person should be in attendance for the duration of each scheduled "
                              "or emergency visit."},
        {"type": "subheading", "text": "7.3 Provision of Utilities"},
        {"type": "p", "text": "All utilities required for maintenance work (electricity, water) must be provided "
                              "at the customer's cost throughout the contract term. Where utilities are "
                              "unavailable for reasons unrelated to a fault being rectified, the work site will "
                              "be considered non-operational until reinstated."},
        {"type": "subheading", "text": "7.4 Use of Scaffolding"},
        {"type": "p", "text": "Where scaffolding is required to complete works, this will be charged additionally "
                              "at our standard delivery and rental rate, quoted in advance."},
        {"type": "pagebreak"},
        {"type": "subheading", "text": "7.5 Cancellation"},
        {"type": "bullets", "items": [
            "Should a customer need to cancel an active contract, refunds will be assessed on a pro-rata "
            "basis for unused contract value, less any services already delivered.",
            "handyman.ae reserves the right to cancel or pause a contract in the event of an ongoing payment "
            "or legal dispute, without liability, until resolution.",
            "handyman.ae reserves the right to cancel any contract at its discretion by providing 30 days' "
            "written notice and a 100% pro-rata refund from the cancellation date."]},
        {"type": "subheading", "text": "7.6 Expired Contracts"},
        {"type": "p", "text": "handyman.ae will provide emergency call outs for a maximum of 1 week after "
                              "contract expiry, provided written confirmation has been received that the client "
                              "intends to renew. Should the contract not be renewed within 14 days of expiry, "
                              "the client will be liable for charges at standard ad-hoc rates for any further "
                              "work."},
        {"type": "subheading", "text": "7.7 Insurance"},
        {"type": "p", "text": "Our work is covered by Public Liability Insurance. handyman.ae will accept no "
                              "liability for any issues, occurrences, or failures unless proven at fault by a "
                              "competent authority within the jurisdiction of Dubai, United Arab Emirates. Both "
                              "parties agree to exclude all indirect or consequential loss, including loss of "
                              "revenue, opportunity, profit, or use, from any claims under this agreement. We "
                              "strongly recommend all customers maintain their own home and contents insurance "
                              "to cover potential damage from AC or plumbing leaks, electrical faults, or "
                              "similar events, however caused."},
        {"type": "subheading", "text": "7.8 Governing Law & Jurisdiction"},
        {"type": "p", "text": "Any dispute arising out of or in connection with this contract shall be subject "
                              "to the exclusive jurisdiction of the Courts of Dubai, United Arab Emirates, and "
                              "shall be governed by and construed in accordance with the laws of the United Arab "
                              "Emirates."},
        {"type": "heading", "text": "8. Package Inclusions at a Glance"},
        {"type": "p", "text": "A side-by-side summary of what's included in each handyman.ae AMC tier."},
        {"type": "table", "auto": "package", "header": ["Feature", "Basic", "Standard", "Premium"], "rows": []},
        {"type": "pagebreak"},
        {"type": "heading", "text": "9. Services Not Undertaken"},
        {"type": "p", "text": "We focus on doing a smaller set of jobs excellently, rather than everything "
                              "adequately. The following are not undertaken under this contract, though we are "
                              "happy to recommend trusted third-party providers where possible:"},
        {"type": "bullets", "columns": 2, "items": list(EXCLUSIONS_9)},
        {"type": "heading", "text": "10. Signed Agreement"},
        {"type": "p", "text": 'This Annual Maintenance Contract is entered into between handyman.ae (the '
                              '"Service Provider") and the customer named below (the "Customer"), and comes '
                              'into effect upon signature and receipt of payment.'},
        {"type": "signature"},
    ]


def sanitize_terms(terms):
    """Validates/normalises an edited block list. Raises ValueError with a
    user-readable message if it can't be rendered."""
    if not isinstance(terms, list):
        raise ValueError("terms must be a list of blocks")
    if len(terms) > MAX_BLOCKS:
        raise ValueError("too many blocks (max %d)" % MAX_BLOCKS)

    def s(v):
        v = "" if v is None else str(v)
        if len(v) > MAX_TEXT:
            raise ValueError("a text field is too long (max %d characters)" % MAX_TEXT)
        return v

    def str_list(v):
        if not isinstance(v, list):
            raise ValueError("block items must be a list")
        return [s(x) for x in v]

    clean = []
    for b in terms:
        if not isinstance(b, dict) or b.get("type") not in _BLOCK_TYPES:
            raise ValueError("unknown block type: %r" % (b.get("type") if isinstance(b, dict) else b))
        t = b["type"]
        if t in ("title", "heading", "subheading", "callout"):
            clean.append({"type": t, "text": s(b.get("text"))})
        elif t == "p":
            clean.append({"type": "p", "text": s(b.get("text")), "indent": bool(b.get("indent"))})
        elif t in ("bullets", "numbered"):
            blk = {"type": t, "items": str_list(b.get("items", []))}
            if t == "bullets" and b.get("columns") == 2:
                blk["columns"] = 2
            clean.append(blk)
        elif t == "definitions":
            items = b.get("items", [])
            if not isinstance(items, list):
                raise ValueError("definitions items must be a list")
            pairs = []
            for it in items:
                if not isinstance(it, (list, tuple)) or len(it) != 2:
                    raise ValueError("each definition needs a term and a description")
                pairs.append([s(it[0]), s(it[1])])
            clean.append({"type": "definitions", "items": pairs})
        elif t == "checklist":
            clean.append({"type": "checklist", "title": s(b.get("title")), "items": str_list(b.get("items", []))})
        elif t == "table":
            header = str_list(b.get("header", []))
            auto = b.get("auto")
            if auto is not None and auto not in _AUTO_TABLES:
                raise ValueError("unknown auto table: %r" % (auto,))
            rows = []
            if not auto:
                if not header:
                    raise ValueError("a table needs at least one header column")
                for r in b.get("rows", []):
                    r = str_list(r)
                    rows.append((r + [""] * len(header))[:len(header)])
            blk = {"type": "table", "header": header, "rows": rows}
            if auto:
                blk["auto"] = auto
            clean.append(blk)
        else:  # pagebreak, signature
            clean.append({"type": t})
    return clean


def _rich(text, ctx):
    t = _TOKEN_RE.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), str(text or ""))
    t = _xml_escape(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t, flags=re.S)
    return t.replace("\n", "<br/>")


def contract_context(data):
    """Everything computed from a contract's details: token values plus the
    figures behind the auto-generated tables. Shared by the PDF renderer and
    the server (which sends the auto-table rows to the editor)."""
    property_type = data["propertyType"]
    tier_idx = ap.TIERS.index(data["package"])
    units = data["acUnits"]
    row = ap.contract_price_for(property_type, units, commercial_rates=data.get("commercialRates"),
                                 saved_prices=data.get("savedPrices"))
    annual = data["annualValue"] if data.get("annualValue") is not None else row[tier_idx]
    co = ap.callouts_for(property_type)
    contract_date = data["contractDate"]
    start_date = data["startDate"]
    return {
        "client": data["clientName"] or "—", "property": data["propertyAddress"] or "—",
        "date": fmt_date(contract_date), "startDate": fmt_date(start_date),
        "tier": data["package"], "units": str(units),
        "matThreshold": str(ap.MAT_THRESHOLD[tier_idx]),
        "callOutsBasic": str(co["basic"]), "callOutsStandard": str(co["standard"]),
        # not tokens - used by the renderer / auto tables
        "_property_type": property_type, "_tier_idx": tier_idx, "_pay_plan": data["payPlan"],
        "_annual": annual, "_plan": ap.monthly_plan(annual), "_co": co,
        "_signatory": data["signatory"],
        "_layout": {**default_contract_layout(property_type), **(data.get("layout") or {})},
    }


def auto_table_rows(kind, ctx):
    property_type = ctx["_property_type"]
    co = ctx["_co"]
    plan = ctx["_plan"]
    if kind == "summary":
        return [
            ("24/7, 365 days a year helpline", "Included"),
            ("Scheduled AC servicing", "2–4x per year, by tier"),
            ("Emergency & non-emergency call outs", "%d (Basic), %d (Standard) or Unlimited (Premium)"
             % (co["basic"], co["standard"])),
            ("Labour for replacement hardware", "Included on covered items"),
            ("Materials covered (per visit)", "AED 50 – 150, by tier"),
            ("Emergency response time", "Within 90 minutes"),
            ("Non-emergency response time", "Within 4 hours, working hours"),
            ("Water tank cleaning", "Included — Basic, Standard & Premium" if property_type == "villa"
             else "Villas only"),
            ("Payment options", "One-Time or Monthly Plan"),
        ]
    if kind == "payment":
        if ctx["_pay_plan"] == "monthly":
            return [
                ("Monthly Plan — deposit (25%)", "AED %s" % money2(plan["deposit"])),
                ("Monthly Plan — 11 instalments of", "AED %s" % money2(plan["monthly"])),
                ("Monthly Plan — total for the year (incl. 8% markup)", "AED %s" % money(plan["planTotal"])),
            ]
        return [("One-Time Payment — total for the year", "AED %s" % money(ctx["_annual"]))]
    return [(r[0], r[1][0], r[1][1], r[1][2]) for r in ap.package_inclusions_table(property_type)]


def _table_flowable(styles, block, ctx):
    auto = block.get("auto")
    header = block.get("header") or []
    if auto == "summary":
        return _data_table(styles, header, auto_table_rows(auto, ctx),
                            col_widths=[CONTENT_W * 0.42, CONTENT_W * 0.58])
    if auto == "payment":
        return _data_table(styles, header, auto_table_rows(auto, ctx),
                            col_widths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
    if auto == "package":
        selected_col = ctx["_tier_idx"] + 1
        return _data_table(styles, header, auto_table_rows(auto, ctx), prem_col=3,
                            extra_col=selected_col if selected_col != 3 else None,
                            col_widths=[CONTENT_W * 0.34] + [CONTENT_W * 0.22] * 3)
    rows = [[_rich(c, ctx) for c in r] for r in block.get("rows", [])]
    n = len(header)
    if n == 1:
        widths = [CONTENT_W]
    else:
        first = CONTENT_W * (0.4 if n > 2 else 0.5)
        widths = [first] + [(CONTENT_W - first) / (n - 1)] * (n - 1)
    return _data_table(styles, [_rich(h, ctx) for h in header], rows, col_widths=widths)


def _signature_flowable(styles, ctx):
    sig_info = ap.SIGNATORIES[ctx["_signatory"]]
    client = _xml_escape(ctx["client"])
    provider_cell = [
        Paragraph("SERVICE PROVIDER", styles["SigLabel"]),
        Paragraph("handyman.ae", ParagraphStyle("CoName", fontName="Helvetica-Bold", fontSize=11,
                                                  textColor=NAVY, spaceAfter=1)),
        Paragraph(_plain(ctx["_layout"]["providerEntity"], ctx), styles["Small"]),
        Spacer(1, 8),
        Paragraph("Name", styles["SigLabel"]),
        Paragraph(sig_info["name"], styles["SigName"]),
        Spacer(1, 4),
        Paragraph("Signature", styles["SigLabel"]),
    ]
    if sig_info["signature"] and os.path.isfile(SIGNATURE_PATH):
        provider_cell.append(Image(SIGNATURE_PATH, width=30 * mm, height=10 * mm))
    else:
        provider_cell.append(Spacer(1, 12))
    provider_cell.append(Spacer(1, 4))
    provider_cell.append(Paragraph("Date", styles["SigLabel"]))
    provider_cell.append(Paragraph(ctx["date"], styles["SigName"]))

    customer_cell = [
        Paragraph("CUSTOMER", styles["SigLabel"]),
        Paragraph(client, ParagraphStyle("CustName", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY,
                                          spaceAfter=1)),
        Paragraph("(Customer Name / Company)", styles["Small"]),
        Spacer(1, 8),
        Paragraph("Name", styles["SigLabel"]),
        Paragraph(client, styles["SigName"]),
        Spacer(1, 4),
        Paragraph("Signature", styles["SigLabel"]),
        Spacer(1, 14),
        Paragraph("Date", styles["SigLabel"]),
        Spacer(1, 10),
    ]
    sig_row = Table([[provider_cell, customer_cell]], colWidths=[CONTENT_W / 2, CONTENT_W / 2])
    sig_row.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, 0), (0, 0), 0.6, LINE), ("LINEABOVE", (1, 0), (1, 0), 0.6, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (0, 0), 14),
    ]))
    return sig_row


def _render_blocks(styles, blocks, ctx):
    story = []
    for b in blocks:
        t = b.get("type")
        if t == "pagebreak":
            story.append(PageBreak())
            story += _letterhead(styles, [(_xml_escape(ctx["client"]), ctx["date"])], tagline=ctx["_layout"]["tagline"])
        elif t == "title":
            story.append(Paragraph(_rich(b.get("text"), ctx), styles["SectionTitle"]))
            story.append(Spacer(1, 4))
        elif t == "heading":
            story.append(Paragraph(_rich(b.get("text"), ctx), styles["ClauseH4"]))
        elif t == "subheading":
            story.append(Paragraph(_rich(b.get("text"), ctx), styles["ClauseSubhead"]))
        elif t == "p":
            story.append(Paragraph(_rich(b.get("text"), ctx), styles["ListItem" if b.get("indent") else "Body"]))
        elif t == "callout":
            story.append(Spacer(1, 10))
            story.append(_highlight_box(styles, _rich(b.get("text"), ctx)))
        elif t in ("bullets", "numbered"):
            paras = [Paragraph(("%d. " % (i + 1) if t == "numbered" else "• ") + _rich(x, ctx), styles["ListItem"])
                     for i, x in enumerate(b.get("items", []))]
            if t == "bullets" and b.get("columns") == 2 and paras:
                half = (len(paras) + 1) // 2
                tbl = Table([[paras[:half], paras[half:]]], colWidths=[CONTENT_W / 2] * 2)
                tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
                story.append(tbl)
            else:
                story += paras
        elif t == "definitions":
            story += [Paragraph("<b>%s</b> — %s" % (_rich(term, ctx), _rich(desc, ctx)), styles["DefTerm"])
                      for term, desc in b.get("items", [])]
        elif t == "checklist":
            story.append(Spacer(1, 10))
            paras = [Paragraph('<font color="#F4591C">&#10003;</font>&nbsp;&nbsp;%s' % _rich(x, ctx), styles["Body"])
                     for x in b.get("items", [])]
            box_rows = [[Paragraph(_rich(b.get("title"), ctx), ParagraphStyle(
                "CommitHead", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY))]]
            if paras:
                inner = Table([[paras[0::2], paras[1::2]]], colWidths=[CONTENT_W / 2] * 2)
                inner.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
                box_rows.append([inner])
            box = Table(box_rows, colWidths=[CONTENT_W])
            box.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.75, LINE), ("LINEBEFORE", (0, 0), (0, -1), 2.5, NAVY),
                ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, 0), (0, 0), 6),
                ("TOPPADDING", (0, -1), (0, -1), 2), ("BOTTOMPADDING", (0, -1), (0, -1), 10),
            ]))
            story.append(box)
        elif t == "table":
            story.append(_table_flowable(styles, b, ctx))
        elif t == "signature":
            story.append(Spacer(1, 8))
            story.append(_signature_flowable(styles, ctx))
    return story


def generate_contract_pdf(data):
    """data: clientName, propertyAddress, propertyType, acUnits, package
    ("Basic"/"Standard"/"Premium"), payPlan ("onetime"/"monthly"), startDate
    (date), signatory ("tegan"/"kristofer"/"govind"), contractDate (date),
    commercialRates (dict or None), savedPrices (dict or None - see
    amc_proposal_prices in server.py). Optional: annualValue (overrides the
    rate-book price), valueText (overrides the cover's contract-value line),
    terms (edited block list - defaults to default_contract_terms())."""
    ctx = contract_context(data)
    blocks = data.get("terms")
    blocks = default_contract_terms() if blocks is None else blocks
    property_type = data["propertyType"]
    copy = ap.COPY["commercial" if property_type == "commercial" else "residential"]
    plan = ctx["_plan"]
    if data.get("valueText"):
        value_text = _rich(data["valueText"], ctx)
    elif ctx["_pay_plan"] == "monthly":
        value_text = ("AED %s deposit, then AED %s &times; 11 monthly instalments (AED %s total, incl. VAT)"
                       % (money2(plan["deposit"]), money2(plan["monthly"]), money(plan["planTotal"])))
    else:
        value_text = "AED %s paid upfront, incl. VAT" % money(ctx["_annual"])

    def build(total_pages):
        styles = _styles()
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=MARGIN, bottomMargin=MARGIN + 6 * mm,
                                 leftMargin=MARGIN, rightMargin=MARGIN,
                                 title="AMC Contract - %s" % data["clientName"])
        client = _xml_escape(ctx["client"])
        units = data["acUnits"]
        story = []
        lay = ctx["_layout"]
        story += _letterhead(styles, [("Contract date", ctx["date"]), ("Start date", ctx["startDate"])], tagline=lay["tagline"])
        story.append(Paragraph(_upper_plain(lay["eyebrow"], ctx), styles["Eyebrow"]))
        story.append(Paragraph(_title_html(lay, ctx), styles["DocTitle"]))
        story.append(Paragraph(_plain(lay["strap"], ctx), styles["DocStrap"]))
        story.append(_kv_strip(styles, [
            ("Customer name", client),
            (copy["prop_label"] + " address" if copy["prop_label"] == "Property" else copy["prop_label"],
             _xml_escape(ctx["property"])),
            ("Property type", ap.TYPE_LABEL[property_type]),
            ("Number of AC units", "%d%s" % (units, " unit" if units == 1 else " units")),
            ("Selected package", data["package"]),
            ("Payment plan", "Monthly Plan" if ctx["_pay_plan"] == "monthly" else "One-Time Payment"),
        ], cols=2))
        story.append(Spacer(1, 4))
        value_box = Table([[Paragraph("CONTRACT VALUE", styles["KLabel"])],
                            [Paragraph(value_text, styles["KValue"])]], colWidths=[CONTENT_W])
        value_box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), WASH), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (0, 0), 8), ("BOTTOMPADDING", (0, -1), (0, -1), 8),
        ]))
        story.append(value_box)
        story += _render_blocks(styles, blocks, ctx)

        def page_label(n):
            return "Page %d of %d" % (n, total_pages) if total_pages else "Page %d" % n

        footer = _footer_drawer(page_label, lay["footer"])
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        return buf.getvalue(), doc.page

    _, pages = build(0)
    pdf, _ = build(pages)
    return pdf
