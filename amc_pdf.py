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


def _letterhead(styles, right_pairs):
    """right_pairs: list of (label, value) shown right-aligned, stacked."""
    logo = Paragraph('Handyman<font color="#F4591C">.ae</font>', styles["Logo"])
    sub = Paragraph("Dubai &middot; United Arab Emirates", styles["LogoSub"])
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


def _footer_drawer(page_label_for):
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
        text = "600 55 4456  ·  handyman.ae  ·  %s" % label
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


def generate_proposal_pdf(data):
    """data: clientName, propertyAddress, propertyType, acUnits, validityDays,
    createdDate (date), validUntil (date), prices [basic,standard,premium],
    isCustom (bool), highlightsTemplate (optional admin-edited 3x5 grid,
    see amc_proposal_highlights in server.py - None uses the default copy)."""
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

    story = []
    story += _letterhead(styles, [("Date", fmt_date(data["createdDate"])),
                                   ("Valid until", fmt_date(data["validUntil"]))])
    story.append(Paragraph(copy["eyebrow"].upper(), styles["Eyebrow"]))
    story.append(Paragraph(copy["title_a"] + '<font color="#F4591C">%s</font>' % copy["title_b"], styles["DocTitle"]))
    story.append(Paragraph(copy["strap"], styles["DocStrap"]))
    story.append(_kv_strip(styles, [
        ("Prepared for", client),
        (copy["prop_label"], prop),
        ("Type", ap.TYPE_LABEL[property_type]),
        ("AC units", "%d%s" % (units, " unit" if units == 1 else " units")),
    ]))
    story.append(Spacer(1, 14))

    highlights_grid = ap.card_highlights(property_type, data.get("highlightsTemplate"))
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

    incl_items = ap.always_included(property_type)
    left_items = incl_items[0::2]
    right_items = incl_items[1::2]

    def _incl_col(items):
        return [Paragraph('<font color="#F4591C">&#10003;</font>&nbsp;&nbsp;%s' % t, styles["Body"])
                for t in items]

    incl_table = Table([[_incl_col(left_items), _incl_col(right_items)]], colWidths=[CONTENT_W / 2] * 2)
    incl_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    incl_box = Table([
        [Paragraph("INCLUDED IN EVERY PACKAGE", ParagraphStyle(
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
    story += _letterhead(styles, [(client, fmt_date(data["createdDate"]))])
    story.append(Paragraph("What each package covers", styles["SectionTitle"]))
    story.append(Paragraph("Side-by-side comparison of the three cover levels.", styles["SectionStrap"]))
    cmp_rows = ap.comparison_rows(property_type)
    cmp_header = ["Service", "Basic", "Standard", "Premium"]
    story.append(_data_table(styles, cmp_header, [(r[0], r[1][0], r[1][1], r[1][2]) for r in cmp_rows],
                              prem_col=3, col_widths=[CONTENT_W * 0.34] + [CONTENT_W * 0.22] * 3))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Payment options", styles["SectionTitle"]))
    story.append(Paragraph("Choose whichever suits you — the cover is identical either way.",
                            styles["SectionStrap"]))
    plans = [ap.monthly_plan(p) for p in prices]
    pay_rows = [
        ("Paid upfront — total for the year",) + tuple("AED %s" % money(p) for p in prices),
        ("Monthly plan — deposit",) + tuple("AED %s" % money2(pl["deposit"]) for pl in plans),
        ("Monthly plan — 11 instalments of",) + tuple("AED %s" % money2(pl["monthly"]) for pl in plans),
        ("Monthly plan — total for the year",) + tuple("AED %s" % money(pl["planTotal"]) for pl in plans),
    ]
    story.append(_data_table(styles, ["Option", "Basic", "Standard", "Premium"], pay_rows, prem_col=3,
                              col_widths=[CONTENT_W * 0.40] + [CONTENT_W * 0.20] * 3))
    story.append(Spacer(1, 8))

    story.append(Paragraph("TERMS", ParagraphStyle("TermsHead", fontName="Helvetica-Bold", fontSize=8.5,
                                                     textColor=SLATE, spaceAfter=6)))
    term_paras = [Paragraph("%d. %s" % (i + 1, t), styles["Body"]) for i, t in enumerate(ap.PROPOSAL_TERMS)]
    half = (len(term_paras) + 1) // 2
    terms_table = Table([[term_paras[:half], term_paras[half:]]], colWidths=[CONTENT_W / 2] * 2)
    terms_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                      ("LEFTPADDING", (1, 0), (1, 0), 14)]))
    story.append(terms_table)
    note_text = ap.PROPOSAL_NOTE
    prefix = "Please note:"
    if note_text.startswith(prefix):
        note_text = note_text[len(prefix):].lstrip()
    story.append(_highlight_box(styles, '<b><font color="#8C3007">Please note:</font></b> ' + note_text))
    story.append(Spacer(1, 8))

    quote_cell = [
        Paragraph(copy["quote"], styles["Pullquote"]),
        Paragraph("TEGAN BRADLEY &middot; OPERATIONS MANAGER", styles["Attrib"]),
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
        return "Page %d of 2" % n

    doc.build(story, onFirstPage=_footer_drawer(page_label), onLaterPages=_footer_drawer(page_label))
    return buf.getvalue()


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


def _def_list(styles, items):
    return [Paragraph("<b>%s</b> — %s" % (term, desc), styles["DefTerm"]) for term, desc in items]


def _plain_list(styles, items, ordered=False):
    out = []
    for i, t in enumerate(items):
        prefix = "%d. " % (i + 1) if ordered else "• "
        out.append(Paragraph(prefix + t, styles["ListItem"]))
    return out


def generate_contract_pdf(data):
    """data: clientName, propertyAddress, propertyType, acUnits, package
    ("Basic"/"Standard"/"Premium"), payPlan ("onetime"/"monthly"), startDate
    (date), signatory ("tegan"/"kristofer"/"govind"), contractDate (date),
    commercialRates (dict or None), savedPrices (dict or None - see
    amc_proposal_prices in server.py)."""
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=MARGIN, bottomMargin=MARGIN + 6 * mm,
                             leftMargin=MARGIN, rightMargin=MARGIN,
                             title="AMC Contract - %s" % data["clientName"])

    property_type = data["propertyType"]
    units = data["acUnits"]
    tier_idx = ap.TIERS.index(data["package"])
    tier_name = data["package"]
    pay_plan = data["payPlan"]
    client = data["clientName"] or "—"
    prop = data["propertyAddress"] or "—"
    copy = ap.COPY["commercial" if property_type == "commercial" else "residential"]
    contract_date = data["contractDate"]
    start_date = data["startDate"]
    date_str = fmt_date(contract_date)
    start_str = fmt_date(start_date)

    row = ap.contract_price_for(property_type, units, commercial_rates=data.get("commercialRates"),
                                 saved_prices=data.get("savedPrices"))
    annual = row[tier_idx]
    plan = ap.monthly_plan(annual)
    co = ap.callouts_for(property_type)

    if pay_plan == "monthly":
        value_text = ("AED %s deposit, then AED %s &times; 11 monthly instalments (AED %s total, incl. VAT)"
                       % (money2(plan["deposit"]), money2(plan["monthly"]), money(plan["planTotal"])))
    else:
        value_text = "AED %s paid upfront, incl. VAT" % money(annual)

    story = []

    # ---- Page 1: cover / details ----
    story += _letterhead(styles, [("Contract date", date_str), ("Start date", start_str)])
    story.append(Paragraph(copy["eyebrow"].upper(), styles["Eyebrow"]))
    story.append(Paragraph('Annual Maintenance <font color="#F4591C">Contract.</font>', styles["DocTitle"]))
    story.append(Paragraph(copy["contract_strap"], styles["DocStrap"]))
    story.append(_kv_strip(styles, [
        ("Customer name", client),
        (copy["prop_label"] + " address" if copy["prop_label"] == "Property" else copy["prop_label"], prop),
        ("Property type", ap.TYPE_LABEL[property_type]),
        ("Number of AC units", "%d%s" % (units, " unit" if units == 1 else " units")),
        ("Selected package", tier_name),
        ("Payment plan", "Monthly Plan" if pay_plan == "monthly" else "One-Time Payment"),
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
    story.append(Spacer(1, 10))
    story.append(_highlight_box(styles, "This document is the full 12-month Annual Maintenance Contract. "
                                         "Cover begins once this agreement is signed and the first payment "
                                         "(full amount, or the 25% deposit under the Monthly Plan) is "
                                         "received."))

    # ---- Page 2: welcome letter ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story.append(Paragraph("Welcome to the handyman.ae Family", styles["SectionTitle"]))
    story.append(Spacer(1, 4))
    letter_paras = [
        "Dear Valued Customer,",
        "Thank you for considering handyman.ae for the care of your home. We know that inviting a "
        "maintenance partner into your property is a decision built on trust — and we don't take "
        "that lightly.",
        "We started handyman.ae on a simple idea: your home deserves consistent, honest, and skilled "
        "care, delivered by people who treat it like their own. Whether it's keeping your AC running "
        "efficiently through Dubai's summer, catching a small plumbing issue before it becomes a big "
        "one, or simply being there when you need us — our goal is to become a maintenance partner "
        "you don't have to think twice about.",
        "This Annual Maintenance Contract sets out exactly what you can expect from us: clear pricing, "
        "real inclusions, and a team that shows up when we say we will. No hidden surprises, no fine "
        "print designed to catch you out — just fair, transparent terms and a genuine commitment to "
        "looking after your property year-round.",
        "We're proud to welcome you as part of the handyman.ae community, and we look forward to "
        "building a long-term relationship with you and your home.",
    ]
    for p in letter_paras:
        story.append(Paragraph(p, styles["Body"]))
    story.append(Paragraph("Warm regards,<br/><b>The handyman.ae Team</b>",
                            ParagraphStyle("Sign", parent=styles["Body"], spaceBefore=6)))
    story.append(Spacer(1, 10))
    commit_items = [
        "Fair, transparent pricing — every cost is itemised, nothing is hidden in the fine print.",
        "Real technicians, properly trained, wearing branded uniform, on every visit.",
        "We treat your property with the same care and respect we'd want for our own homes.",
        "Honest advice — if something doesn't need fixing yet, we'll tell you.",
        "A long-term relationship, not a one-off job — we want to be your maintenance partner for "
        "years to come.",
        "Responsive support — our team is reachable and accountable, every time you need us.",
    ]
    commit_paras = [Paragraph('<font color="#F4591C">&#10003;</font>&nbsp;&nbsp;%s' % t, styles["Body"])
                     for t in commit_items]
    commit_table = Table([[commit_paras[0::2], commit_paras[1::2]]], colWidths=[CONTENT_W / 2] * 2)
    commit_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    commit_box = Table([[Paragraph("OUR COMMITMENT TO YOU", ParagraphStyle(
        "CommitHead", fontName="Helvetica-Bold", fontSize=11, textColor=NAVY))], [commit_table]],
        colWidths=[CONTENT_W])
    commit_box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.75, LINE), ("LINEBEFORE", (0, 0), (0, -1), 2.5, NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (0, 0), 10), ("BOTTOMPADDING", (0, 0), (0, 0), 6),
        ("TOPPADDING", (0, 1), (0, 1), 2), ("BOTTOMPADDING", (0, 1), (0, 1), 10),
    ]))
    story.append(commit_box)

    # ---- Page 3: summary + definitions ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story.append(Paragraph("1. Summary of Services", styles["ClauseH4"]))
    story.append(Paragraph("This Annual Maintenance Contract covers AC, plumbing, and electrical systems "
                            "at your property. The table below provides a quick overview — full "
                            "details follow in the sections after.", styles["Body"]))
    summary_rows = [
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
    story.append(_data_table(styles, ["This contract", "Details"], summary_rows,
                              col_widths=[CONTENT_W * 0.42, CONTENT_W * 0.58]))
    story.append(Paragraph("2. Definitions", styles["ClauseH4"]))
    story += _def_list(styles, DEFINITIONS_PAGE3)

    # ---- Page 4: definitions cont + scope & scheduling ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story += _def_list(styles, DEFINITIONS_PAGE4)
    story.append(Paragraph("3. Scope &amp; Scheduling of Annual Maintenance Work", styles["ClauseH4"]))
    story.append(Paragraph("3.1 Pre-Contract Property Check", styles["ClauseSubhead"]))
    story.append(Paragraph("Prior to activation, a member of our bookings team will confirm the number of "
                            "AC units at the property and check they are cooling correctly. This is an "
                            "initial review only, not a full technical inspection. Any units not running "
                            "or cooling correctly will be diagnosed separately, with diagnosis charges "
                            "credited back to the account once rectification work is approved, completed, "
                            "and paid.", styles["Body"]))
    story.append(Paragraph("3.2 AC Servicing Schedule", styles["ClauseSubhead"]))
    story.append(Paragraph("AC servicing frequency depends on your selected package:", styles["Body"]))
    story += _plain_list(styles, ["Basic — 2x per AC unit, per year", "Standard — 3x per AC unit, "
                                   "per year", "Premium — 4x per AC unit, per year"])
    story.append(Paragraph("Upon signing and receipt of payment, the bookings team will schedule the first "
                            "visit and all remaining visits through our automated booking system. "
                            "Customers will be reminded of scheduled dates by phone, email, or text "
                            "message. We allow a 2-week window either side of scheduled visits to "
                            "accommodate your availability.", styles["Body"]))
    story.append(Paragraph("3.3 Call Outs", styles["ClauseSubhead"]))
    story.append(Paragraph(
        "Basic package customers receive %d limited, free call outs per year for AC, plumbing, and "
        "electrical issues. Standard receive %d limited, free call outs per year for AC, plumbing, and "
        "electrical issues and Premium package customers receive unlimited call outs for the duration of "
        "the contract, subject to fair and reasonable use. Any call out beyond the Basic allowance will "
        "be charged at our standard discounted ad-hoc rate." % (co["basic"], co["standard"]), styles["Body"]))
    story.append(Paragraph("3.4 Response Times", styles["ClauseSubhead"]))
    story += _plain_list(styles, ["Emergency issues: response within 90 minutes from receipt of access "
                                   "pass", "Non-emergency issues: response within 4 hours, during Standard "
                                   "Business Hours"])
    story.append(Paragraph("handyman.ae provides maintenance services on a reasonable endeavours basis but "
                            "shall not be liable for any loss, damage, or claim arising from a failure to "
                            "meet a response time where access or other conditions outside our control "
                            "prevented timely attendance.", styles["Body"]))

    # ---- Page 5: inclusions & exclusions ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story.append(Paragraph("4. Contract Inclusions &amp; Exclusions", styles["ClauseH4"]))
    story.append(Paragraph("4.1 What's Included", styles["ClauseSubhead"]))
    story.append(Paragraph("In the event of hardware failure during the contract period, handyman.ae will "
                            "only quote and charge for materials not listed below — labour to install "
                            "replacement parts is provided free of charge on all covered items.",
                            styles["Body"]))
    story += _plain_list(styles, [
        "Plumbing: PVC, PPR, GI pipe and fittings, check valves, flexible hoses",
        "Electrical: Standard DB breakers",
        "AC: Capacitors, contactors, copper fittings & flare nuts, charging valves, filter driers & "
        "strainers, compressor wire, stem valve and sight glass",
    ])
    story.append(Paragraph(
        "The package also provides free refrigerant (R22 – Freon) for top-ups where required, "
        "assuming R22 remains available and suitable for your unit. Materials and parts up to the "
        "threshold shown below for your tier are covered without additional charge: "
        '<font color="#0B1B3D"><b>AED %d per visit (%s)</b></font>.' % (ap.MAT_THRESHOLD[tier_idx], tier_name),
        styles["Body"]))
    story.append(Paragraph("4.2 What's Not Included", styles["ClauseSubhead"]))
    story.append(Paragraph("Replacement hardware for AC, electrical, or plumbing systems not listed in "
                            "Section 4.1 is not covered. Where parts are required to restore equipment to "
                            "working order, these will be quoted and charged for materials only — "
                            "labour remains free of charge. Examples include:", styles["Body"]))
    story += _plain_list(styles, [
        "Plumbing Fixtures — pumps, pressure vessels & switches, taps, shower heads & hoses, WC "
        "components, water heaters, valves, sinks, baths, washing machine hoses",
        "Electrical Fixtures — sockets, switches, ELCBs and distribution board components, light "
        "bulbs, ballasts, transformers, cabling, fittings",
        "AC Hardware — compressors, thermostats, fan motors & blades, actuator valves & motors, "
        "transformers, insulation tape & adhesive",
    ])
    story.append(Paragraph("Gas leak repair labour is provided at a discount versus standard ad-hoc rates; "
                            "if a full regas or different refrigerant type is required, this is chargeable "
                            "for materials and labour at the same discounted rate.", styles["Body"]))

    # ---- Page 6: payment terms ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story.append(Paragraph("5. Payment Terms", styles["ClauseH4"]))
    story.append(Paragraph("5.1 Payment Options", styles["ClauseSubhead"]))
    story.append(Paragraph("Customers may choose between two payment options for their Annual Maintenance "
                            "Contract:", styles["Body"]))
    story.append(Paragraph("<b>One-Time Payment</b> — the full annual contract value is paid upfront, "
                            "in advance of contract activation.", styles["ListItem"]))
    story.append(Paragraph("<b>Monthly Plan</b> — a 25% deposit is paid upfront (counted as the first "
                            "instalment), with the remaining balance split across 11 further monthly "
                            "instalments. A service markup applies to the Monthly Plan to reflect the "
                            "flexibility of spreading payment across the year: 8% for Basic, Standard, and "
                            "Premium.", styles["ListItem"]))
    if pay_plan == "monthly":
        pay_rows = [
            ("Monthly Plan — deposit (25%)", "AED %s" % money2(plan["deposit"])),
            ("Monthly Plan — 11 instalments of", "AED %s" % money2(plan["monthly"])),
            ("Monthly Plan — total for the year (incl. 8% markup)", "AED %s" % money(plan["planTotal"])),
        ]
    else:
        pay_rows = [("One-Time Payment — total for the year", "AED %s" % money(annual))]
    story.append(_data_table(styles, ["This contract", "Selected terms"], pay_rows,
                              col_widths=[CONTENT_W * 0.6, CONTENT_W * 0.4]))
    story.append(Paragraph("5.2 General Payment Conditions", styles["ClauseSubhead"]))
    story += _plain_list(styles, [
        "Payment can be made by cash, cheque, bank transfer, or credit card.",
        "Any additional hardware not covered under Section 4.1 must be paid for on completion of "
        "installation.",
        "Where quoted additional works exceed AED 1,500, a 50% deposit or purchase order is required "
        "before parts are sourced, with the balance payable on completion.",
        "All contracts are subject to 5% VAT in compliance with UAE federal law. handyman.ae acts as a "
        "collection agent on behalf of the relevant taxation authority.",
        "Missed monthly instalments may result in suspension of contract benefits (including call out and "
        "servicing entitlements) until payment is brought up to date.",
    ])

    # ---- Page 7: warranty + general terms ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story.append(Paragraph("6. Hardware Installation &amp; Warranty Terms", styles["ClauseH4"]))
    story += _plain_list(styles, [
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
        "testing, maintenance, or repair by an external contractor will void the warranty on that unit and "
        "exclude it from the remaining contract period.",
    ], ordered=True)
    story.append(Paragraph("7. General Contract Terms", styles["ClauseH4"]))
    story.append(Paragraph("7.1 Contract Commencement &amp; Term", styles["ClauseSubhead"]))
    story.append(Paragraph("This contract commences upon receipt of payment (full payment, or the 25% "
                            "deposit under the Monthly Plan) and runs for an initial period of 12 months, "
                            "unless otherwise agreed in writing.", styles["Body"]))
    story.append(Paragraph("7.2 Community &amp; Property Access", styles["ClauseSubhead"]))
    story.append(Paragraph("Maintenance visits are subject to community and property access being "
                            "available. A responsible person should be in attendance for the duration of "
                            "each scheduled or emergency visit.", styles["Body"]))
    story.append(Paragraph("7.3 Provision of Utilities", styles["ClauseSubhead"]))
    story.append(Paragraph("All utilities required for maintenance work (electricity, water) must be "
                            "provided at the customer's cost throughout the contract term. Where utilities "
                            "are unavailable for reasons unrelated to a fault being rectified, the work "
                            "site will be considered non-operational until reinstated.", styles["Body"]))
    story.append(Paragraph("7.4 Use of Scaffolding", styles["ClauseSubhead"]))
    story.append(Paragraph("Where scaffolding is required to complete works, this will be charged "
                            "additionally at our standard delivery and rental rate, quoted in advance.",
                            styles["Body"]))

    # ---- Page 8: general terms cont + package table ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story.append(Paragraph("7.5 Cancellation", styles["ClauseSubhead"]))
    story += _plain_list(styles, [
        "Should a customer need to cancel an active contract, refunds will be assessed on a pro-rata "
        "basis for unused contract value, less any services already delivered.",
        "handyman.ae reserves the right to cancel or pause a contract in the event of an ongoing payment "
        "or legal dispute, without liability, until resolution.",
        "handyman.ae reserves the right to cancel any contract at its discretion by providing 30 days' "
        "written notice and a 100% pro-rata refund from the cancellation date.",
    ])
    story.append(Paragraph("7.6 Expired Contracts", styles["ClauseSubhead"]))
    story.append(Paragraph("handyman.ae will provide emergency call outs for a maximum of 1 week after "
                            "contract expiry, provided written confirmation has been received that the "
                            "client intends to renew. Should the contract not be renewed within 14 days of "
                            "expiry, the client will be liable for charges at standard ad-hoc rates for any "
                            "further work.", styles["Body"]))
    story.append(Paragraph("7.7 Insurance", styles["ClauseSubhead"]))
    story.append(Paragraph("Our work is covered by Public Liability Insurance. handyman.ae will accept no "
                            "liability for any issues, occurrences, or failures unless proven at fault by "
                            "a competent authority within the jurisdiction of Dubai, United Arab Emirates. "
                            "Both parties agree to exclude all indirect or consequential loss, including "
                            "loss of revenue, opportunity, profit, or use, from any claims under this "
                            "agreement. We strongly recommend all customers maintain their own home and "
                            "contents insurance to cover potential damage from AC or plumbing leaks, "
                            "electrical faults, or similar events, however caused.", styles["Body"]))
    story.append(Paragraph("7.8 Governing Law &amp; Jurisdiction", styles["ClauseSubhead"]))
    story.append(Paragraph("Any dispute arising out of or in connection with this contract shall be "
                            "subject to the exclusive jurisdiction of the Courts of Dubai, United Arab "
                            "Emirates, and shall be governed by and construed in accordance with the laws "
                            "of the United Arab Emirates.", styles["Body"]))
    story.append(Paragraph("8. Package Inclusions at a Glance", styles["ClauseH4"]))
    story.append(Paragraph("A side-by-side summary of what's included in each handyman.ae AMC tier.",
                            styles["Body"]))
    pkg_rows = ap.package_inclusions_table(property_type)
    selected_col = tier_idx + 1  # rowhead is column 0; Basic/Standard/Premium are 1/2/3
    story.append(_data_table(styles, ["Feature", "Basic", "Standard", "Premium"],
                              [(r[0], r[1][0], r[1][1], r[1][2]) for r in pkg_rows], prem_col=3,
                              extra_col=selected_col if selected_col != 3 else None,
                              col_widths=[CONTENT_W * 0.34] + [CONTENT_W * 0.22] * 3))

    # ---- Page 9: exclusions + signed agreement ----
    story.append(PageBreak())
    story += _letterhead(styles, [(client, date_str)])
    story.append(Paragraph("9. Services Not Undertaken", styles["ClauseH4"]))
    story.append(Paragraph("We focus on doing a smaller set of jobs excellently, rather than everything "
                            "adequately. The following are not undertaken under this contract, though we "
                            "are happy to recommend trusted third-party providers where possible:",
                            styles["Body"]))
    excl_paras = [Paragraph("• " + t, styles["ListItem"]) for t in EXCLUSIONS_9]
    half = (len(excl_paras) + 1) // 2
    excl_table = Table([[excl_paras[:half], excl_paras[half:]]], colWidths=[CONTENT_W / 2] * 2)
    excl_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(excl_table)

    story.append(Paragraph("10. Signed Agreement", styles["ClauseH4"]))
    story.append(Paragraph('This Annual Maintenance Contract is entered into between handyman.ae (the '
                            '"Service Provider") and the customer named below (the "Customer"), and comes '
                            'into effect upon signature and receipt of payment.', styles["Body"]))
    story.append(Spacer(1, 8))

    sig_info = ap.SIGNATORIES[data["signatory"]]
    provider_cell = [
        Paragraph("SERVICE PROVIDER", styles["SigLabel"]),
        Paragraph("handyman.ae", ParagraphStyle("CoName", fontName="Helvetica-Bold", fontSize=11,
                                                  textColor=NAVY, spaceAfter=1)),
        Paragraph("(GIJO Maintenance Services)", styles["Small"]),
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
    provider_cell.append(Paragraph(date_str, styles["SigName"]))

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
    story.append(sig_row)

    def page_label(n):
        return "Page %d of 9" % n

    doc.build(story, onFirstPage=_footer_drawer(page_label), onLaterPages=_footer_drawer(page_label))
    return buf.getvalue()
