"""
Handyman.ae Quote Builder - backend.

A WSGI application (wsgiref etc. from the standard library, plus psycopg2
for Postgres - see requirements.txt), so it runs both:
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
from datetime import date, timedelta
import re
import socketserver
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import psycopg2
import psycopg2.extras

import amc
import amc_pdf
import amc_proposal
import auth
import pricing

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
# Railway (and any other Postgres host) injects this - no default, since
# there's no sensible local fallback anymore: see README.txt for how to run
# a local Postgres for development.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

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

# ---------------------------------------------------------------------------
# Seed data: Suppliers (parts/materials vendors, team-scoped), ported from
# the standalone "Handyman.ae Quote Builder & Price List" master file.
# Each row is [team, category, name, location, phone, email, services, preferred].
# Contractors (below, SEED_CONTRACTORS) are also team-scoped but have no
# `team` column in their own seed rows - every seeded contractor defaults to
# 'dubai' on insert, same convention as every other pre-team-column catalog.
# ---------------------------------------------------------------------------
SEED_SUPPLIERS = json.loads(r'''
[["dubai","HVAC / Air Conditioning","Daikin","Al Quoz","","","Split AC units, VRF systems, chillers, original spare parts",false],["dubai","HVAC / Air Conditioning","Marhaba (Veto)","Satwa","","","HVAC consumables, AC accessories, insulation, ducting items, tapes, fittings",false],["dubai","HVAC / Air Conditioning","Instacool (Samsung)","Al Barsha","","","Samsung split ACs, VRF systems, commercial AC solutions",false],["dubai","HVAC / Air Conditioning","Hamad Saeed (AC Shop)","Satwa","","","AC spare parts, compressors, motors, capacitors, refrigerant gas, HVAC materials",false],["dubai","HVAC / Air Conditioning","SKM Aircond","Sharjah","","","AHUs, FCUs, chillers, custom HVAC systems",false],["dubai","HVAC / Air Conditioning","MAH (AC)","Al Quoz","","","Copper pipes, insulation, refrigerants, AC fittings and accessories",false],["dubai","HVAC / Air Conditioning","Hollywood AC","Deira","","","AC spare parts, motors, blowers, HVAC consumables",false],["dubai","HVAC / Air Conditioning","Taqeef (O'General / Midea)","Ajman","","","O'General & Midea split units, VRF systems, AC spare parts",false],["dubai","HVAC / Air Conditioning","Skylight (AC)","Al Quoz","058 101 3083","","AC spare parts, VRF accessories, HVAC consumables",false],["dubai","HVAC / Air Conditioning","GulfSail (York)","Deira","","","York chillers, VRF systems, HVAC equipment",false],["dubai","HVAC / Air Conditioning","Juma Al Majid (Trane)","Ras Al Khor","","","Trane chillers, VRF systems, commercial HVAC equipment",false],["dubai","HVAC / Air Conditioning","York","Deira","","","HVAC systems, chillers, VRF units and spare parts",false],["dubai","HVAC / Air Conditioning","Invertex (Samsung)","Office BC3-10 AB Building, Al Barsha 1","050 555 2905","info@invertex.ae","AC Inverter Samsung. Alt contact: Mahy Khoory — 04 606 7300 / 050 678 7253",false],["dubai","Ducting / Insulation / HVAC Fabrication","Al Waseef","Al Quoz","","","Ducting materials, HVAC accessories, insulation products",false],["dubai","Ducting / Insulation / HVAC Fabrication","Starwell","Al Quoz","","","Insulation materials, HVAC accessories, MEP consumables",false],["dubai","Ducting / Insulation / HVAC Fabrication","EICG","Al Quoz","","","GI ducting, insulation materials, duct accessories, dampers",false],["dubai","Electrical & Lighting","Golden Way Electrical","Al Quoz","+971 50 280 5615 / +971 50 221 9826","sales@goldenwayelectrical.com","Electrical, Sanitary, Hardware, Tools & Air Conditioning. Cables, breakers, conduits, switches, consumables",false],["dubai","Electrical & Lighting","Legrand","Deira","","","Switchgear, sockets, distribution boards, smart systems",false],["dubai","Electrical & Lighting","Autolighting (Electrical)","Al Quoz","","","Indoor & outdoor lighting fixtures, electrical accessories",false],["dubai","Electrical & Lighting","BNT","Deira","","","Electrical & MEP consumables and fittings",false],["dubai","Electrical & Lighting","Soft Fixing","Al Quoz","","","Switches, sockets, lighting accessories, wiring devices",false],["dubai","Electrical & Lighting","Al Yousuf LG","Al Qusais","","","LG AC systems, appliances, electrical solutions",false],["dubai","Electrical & Lighting","Fanoos Lighting","Deira","","","Decorative and commercial lighting fixtures",false],["dubai","Electrical & Lighting","Sparks Electrical – Ariston","Al Quoz","","","Electrical supplies, Ariston water heaters",false],["dubai","Electrical & Lighting","Kepco (Electrical)","Deira","","","Electrical cables, breakers, panels, industrial materials",false],["dubai","Electrical & Lighting","Nasa Electricals","Deira","","","Electrical materials, cables, fittings",false],["dubai","Electrical & Lighting","Fortune SHJ (LG)","Sharjah","","","LG air conditioning systems and appliances",false],["dubai","Electrical & Lighting","Electrical Lighting","Deira","","","Lighting fixtures and electrical accessories",false],["dubai","Electrical & Lighting","Suroor Al Madena","Deira","","","Electrical, HVAC & plumbing consumables (general supplier)",false],["dubai","Plumbing / Sanitary / Water Systems","Ariston Middle East","Ras Al Khor","","","Water heaters, boilers, hot water systems",false],["dubai","Plumbing / Sanitary / Water Systems","Mahy Khoory (Grundfos)","Deira","","","Grundfos pumps, booster sets, water pressure systems",false],["dubai","Plumbing / Sanitary / Water Systems","Al Shamsi (Roca)","Ras Al Khor","","","Roca sanitary ware, basins, toilets, mixers, bathroom fittings",false],["dubai","Plumbing / Sanitary / Water Systems","Safinat Alsalam (Sanit Flush Mech)","Al Ameer Tower, Al Nahda, Sharjah","04 269 5529 / +971 50 650 8079","sales@safinathtrading.com","Sanit flush mechanisms",false],["dubai","Plumbing / Sanitary / Water Systems","Abu Saeed","Deira","","","Plumbing materials, pipes, valves, fittings",false],["dubai","Plumbing / Sanitary / Water Systems","Thermea (Solar w/ Spare)","Ras Al Khor","","","Solar water heaters, hot water systems, spare parts",false],["dubai","Plumbing / Sanitary / Water Systems","Leminar","Deira","","","Sanitary ware, bathroom fittings, accessories",false],["dubai","Plumbing / Sanitary / Water Systems","Faraidoni (Grohe / Geberit)","Deira","","","Grohe mixers, Geberit concealed tanks, premium fittings",false],["dubai","Plumbing / Sanitary / Water Systems","Challenger Booster Pumps","","058 892 4491","","Booster pumps",false],["dubai","Plumbing / Sanitary / Water Systems","Terry Glass (Sanit)","","058 910 8273","","Sanit products",false],["dubai","Building Materials / Miscellaneous","Sunrise Oasis – ONLY lights","Al Quoz","","","MEP supplies, fixtures and fittings",false],["dubai","Building Materials / Miscellaneous","Smooth Solution (Jotun)","Al Quoz","052 911 2600","","Jotun paints, coatings, finishing materials",false],["dubai","Building Materials / Miscellaneous","ALQZ Building Materials","Al Quoz","","","General building materials, MEP consumables",false],["dubai","Building Materials / Miscellaneous","Royal Apex (Midhun)","Al Quoz Industrial 3, Dubai","054 407 4209","info@royalapexuae.com","Authorized dealer for Legrand, Schneider, Honeywell, Siemens. Electrical hardware, HVAC & plumbing raw materials, building materials, safety products (3,800+ brands stocked)",false],["dubai","Building Materials / Miscellaneous","Mega City Building Materials (Aby)","Al Quoz Industrial Area 3, Dubai","055 210 1359","","Building materials, fitting & assembly tools, locks/ironware, electrical products",false],["dubai","Building Materials / Miscellaneous","Gratis Building & Construction Materials Trading LLC (Swalih)","","056 294 2262","","Building & construction materials",false]]
''')

# Each row is [category, name, location, phone, email, services, preferred, pricingNote, pricing]
# where pricing is a list of [label, price, note] rows (may be empty).
SEED_CONTRACTORS = json.loads(r'''
[["Cleaning Services","Trumax Group (Stanley Scharenguivel, Head of Soft Services)","1202, API Office Tower, Novotel, Al Barsha 1, Dubai","+971 52 660 6076 / +971 4 272 4834","stanley@trumaxgroup.com","Deep cleaning partner for MAG City community (managed by Handyman.ae, no markup added — tap to view pricing below). CC: adminsoftservices@trumaxgroup.com",true,"We do not add markup on these rates — quote as-is. Confirmed by Stanley Scharenguivel (Trumax), Aug 2026. VAT treatment not specified in their email — confirm before quoting customers.",[["Studio Apt (390–550 sq ft) – Unfurnished",600,"Per visit"],["Studio Apt (390–550 sq ft) – Furnished",650,"Per visit"],["1BR Apt (800–910 sq ft) – Unfurnished",750,"Per visit"],["1BR Apt (800–910 sq ft) – Furnished",800,"Per visit"],["2BR Townhouse (1,600–1,800 sq ft) – Unfurnished, Internal only",1400,""],["2BR Townhouse (1,600–1,800 sq ft) – Unfurnished, Internal + External",1850,""],["2BR Townhouse (1,600–1,800 sq ft) – Furnished, Internal only",1450,""],["2BR Townhouse (1,600–1,800 sq ft) – Furnished, Internal + External",1900,""],["3BR Townhouse (1,900–2,400 sq ft) – Unfurnished, Internal only",1500,""],["3BR Townhouse (1,900–2,400 sq ft) – Unfurnished, Internal + External",2000,""],["3BR Townhouse (1,900–2,400 sq ft) – Furnished, Internal only",1550,""],["3BR Townhouse (1,900–2,400 sq ft) – Furnished, Internal + External",2050,""],["4BR Townhouse (2,500–2,900 sq ft) – Unfurnished, Internal only",1650,""],["4BR Townhouse (2,500–2,900 sq ft) – Unfurnished, Internal + External",2150,""],["4BR Townhouse (2,500–2,900 sq ft) – Furnished, Internal only",1700,""],["4BR Townhouse (2,500–2,900 sq ft) – Furnished, Internal + External",2200,""],["MAG Park 4BR (approx. 5,350 sq ft) – Unfurnished, Internal only",3750,""],["MAG Park 4BR (approx. 5,350 sq ft) – Unfurnished, Internal + External",4750,""],["MAG Park 4BR (approx. 5,350 sq ft) – Furnished, Internal only",3850,""],["MAG Park 4BR (approx. 5,350 sq ft) – Furnished, Internal + External",4850,""],["MAG Park 5BR (approx. 8,650 sq ft) – Unfurnished, Internal only",4500,""],["MAG Park 5BR (approx. 8,650 sq ft) – Unfurnished, Internal + External",5500,""],["MAG Park 5BR (approx. 8,650 sq ft) – Furnished, Internal only",4600,""],["MAG Park 5BR (approx. 8,650 sq ft) – Furnished, Internal + External",5600,""],["Mattress Cleaning (team clean)",150,"Add-on"],["Sofa Steam Cleaning (2–3 seater)",200,"Add-on"]]],["Aluminum & Glass Works","Sunrise Aluminum & Glass","","+971 55 623 6190","","Aluminum & glass doors and windows, glass partitions and sliding doors, pergolas and parking structures, aluminum cladding, shower and bathroom glass, curtain wall and tower works, handrails and balustrades, general aluminum and glass works. Site visits and quotations available. Contact goes through the WhatsApp group named 'Sunrise Steel'. Sites: s-risealuminium.com / sunriseuae.ae",false,"",[]],["Interior Wrapping","Mayfair Wrapping","Warehouse 4, Street 28A, Al Quoz Industrial Area 1, Dubai","+971 4 268 7176","info@mayfairwrapping.ae","British-managed vinyl/architectural wrapping company (est. 2020) — kitchen, bathroom, furniture, door, wall and floor wrapping, plus LVT/SPC flooring. No demolition mess; most residential jobs done in 1-2 days. Website: mayfairwrapping.ae",false,"",[]],["Appliance Repair","Best in Town","","","","Home & commercial appliance repair (fridges, washing machines, ovens, AC, etc.). Note: several Dubai businesses trade under this name — confirm the correct contact number before using, then add it here via Admin.",false,"",[]],["Locksmith / Key & Lock","Hamza","","","","Key and lock issues — lockouts, lock repair/replacement, key cutting/duplication. Phone number not yet on file — add via Admin.",false,"",[]]]
''')

# ---------------------------------------------------------------------------
# Seed data: AMC Tracker clients + contract history, ported verbatim from the
# standalone "Handyman.ae AMC Tracker" prototype (real historical business
# data - GIJO Technical Services LLC / Handyman Dubai's actual AMC book).
# Every row goes to the 'dubai' team on first boot; MAG City starts with no
# AMC clients, same convention as Materials/Labour/Suppliers. Two clients
# (ids 24-25) are in a Meydan community literally named "Mag Eye" - that is
# unrelated to the MAG City team (a different, JVC-area MAG development) so
# they stay on 'dubai' like everything else here; reassign via the client's
# own Team field in Contracts & servicing if that's ever wrong.
# ---------------------------------------------------------------------------
SEED_AMC_CLIENTS = json.loads(r'''
[{"id":1,"customer":"Ioannis Vaxevanos","location":"JVT","address":"District 9 Townhouse E46, JVT","package":"Premium","ownerTenant":"Tenant","start":"2026-08-01","end":"2027-07-31","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 12-Aug-2026","v1Done":"","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Pending","wtcDate":"","hm1Status":"Done","hm1Date":"2026-08-12","hm1Job":"","hm2Status":"Done","hm2Date":"2026-09-02","hm2Job":"","notes":"Customer will be back Aug. Call for renewal.","flag":""},{"id":2,"customer":"Anas Ayman Al Halabi","location":"JVT","address":"District 9 D12, JVT","package":"Standard","ownerTenant":"Owner","start":"2026-07-20","end":"2027-07-25","payType":"50/50","payStatus":"Paid","payNotes":"50% 20-Jul-2025 / 50% 20-Oct-2026","v1Done":"2026-09-02","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Pending","wtcDate":"","hm1Status":"Done","hm1Date":"2026-07-21","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"He will renew the contract; contract renewed 20-Jul.","flag":""},{"id":3,"customer":"Vladimir Tatarchuk","location":"JVT","address":"TH C17 Dst 5, JVT","package":"Standard","ownerTenant":"Tenant","start":"2026-07-13","end":"2027-07-12","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 11-Jul-2026","v1Done":"2026-08-18","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Pending","wtcDate":"","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"After WTC do full AMC; sent new pricing for renewal Jul 10; renewed 13-Jul-2026.","flag":""},{"id":4,"customer":"Vladimir Tatarchuk","location":"Emaar South","address":"Unit 171, Greenview 3, Emaar South","package":"Standard","ownerTenant":"Tenant","start":"2026-07-13","end":"2027-07-12","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 11-Jul-2026","v1Done":"2026-08-17","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"N/A","wtcDate":"","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Second property; sent new pricing for renewal Jul 10; renewed 13-Jul-2026.","flag":""},{"id":5,"customer":"Sahar Al-Farra","location":"JP","address":"Villa S88 Street I5, Jumeirah Park","package":"Standard","ownerTenant":"Owner","start":"2025-07-11","end":"2026-07-11","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 11-Jul-2025","v1Done":"2025-07-12","v2Done":"2025-11-15","v3Done":"2026-06-01","v4Done":"","wtcStatus":"Done","wtcDate":"2025-11-18","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Customer is out of the country, will be back Sep 2026.","flag":""},{"id":6,"customer":"Doug Wilson","location":"JVT","address":"Villa 20 Dst 4D, JVT","package":"Standard","ownerTenant":"Owner","start":"2025-09-03","end":"2026-09-03","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 03-Sep-2025","v1Done":"2026-01-15","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2025-08-25","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":7,"customer":"Andrew Heap","location":"JVT","address":"Villa C5, District 9, JVT","package":"Basic","ownerTenant":"Owner","start":"2025-10-06","end":"2026-10-06","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 06-Oct-2025","v1Done":"2026-03-17","v2Done":"2026-06-26","v3Done":"","v4Done":"","wtcStatus":"N/A","wtcDate":"","hm1Status":"N/A","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"No WTC in contract.","flag":""},{"id":8,"customer":"Rami Abdallah Said","location":"Hayat","address":"Villa 274, Hayat Townhouse, Townsquare","package":"Basic","ownerTenant":"Owner","start":"2025-10-06","end":"2026-10-06","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 06-Oct-2025","v1Done":"2026-03-06","v2Done":"2026-08-03","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2025-08-07","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":9,"customer":"Consulate Singapore","location":"Satwa","address":"Consulate General of the Republic of Singapore","package":"Premium","ownerTenant":"Commercial","start":"2025-09-01","end":"2026-09-30","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 16-Aug-2025","v1Done":"2025-10-23","v2Done":"2025-11-14","v3Done":"2025-12-18","v4Done":"2026-01-09","wtcStatus":"Done","wtcDate":"2025-11-14","hm1Status":"Done","hm1Date":"2026-04-07","hm1Job":"","hm2Status":"Pending","hm2Date":"","hm2Job":"","notes":"Monthly PPM contract - 12 visits/yr.","flag":""},{"id":10,"customer":"Alexander Turnbull","location":"JVT","address":"Villa U25, District 8, JVT","package":"Standard","ownerTenant":"Owner","start":"2025-11-11","end":"2026-11-11","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 11-Nov-2025","v1Done":"2026-01-31","v2Done":"2026-05-25","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2025-07-22","hm1Status":"Done","hm1Date":"2025-05-08","hm1Job":"2528","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":11,"customer":"Chrystelle Azzouz","location":"JVC","address":"Villa B55, District 19, JVC","package":"Basic","ownerTenant":"Owner","start":"2025-12-04","end":"2026-12-02","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 04-Dec-2025","v1Done":"2026-01-28","v2Done":"2026-09-01","v3Done":"","v4Done":"","wtcStatus":"N/A","wtcDate":"","hm1Status":"N/A","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":12,"customer":"Kalidas Chodankar","location":"Arabian Ranches","address":"Villa 19 Street 3 Savanna, Arabian Ranches 1","package":"Standard","ownerTenant":"Owner","start":"2025-12-15","end":"2026-12-14","payType":"Upfront","payStatus":"Paid","payNotes":"50% paid 12-Dec-2025 / 50% paid 22-Jun-2026","v1Done":"2025-12-22","v2Done":"2026-06-29","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2025-12-23","hm1Status":"Done","hm1Date":"2026-05-04","hm1Job":"2484","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":13,"customer":"Michael Callianiotis","location":"JP","address":"Villa U39, District 9, Jumeirah Park","package":"Standard","ownerTenant":"Owner","start":"2026-01-06","end":"2027-01-06","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 06-Jan-2026","v1Done":"2026-03-28","v2Done":"2026-06-08","v3Done":"2026-08-18","v4Done":"","wtcStatus":"Done","wtcDate":"2026-07-08","hm1Status":"Done","hm1Date":"2025-11-11","hm1Job":"1096","hm2Status":"Done","hm2Date":"2026-06-08","hm2Job":"2986","notes":"Package corrected from Premium to Standard per contract.","flag":""},{"id":14,"customer":"Jenny Liu","location":"Tilal Al Ghaf","address":"Villa 27, Harmony 2, Tilal Al Ghaf","package":"Premium","ownerTenant":"Tenant","start":"2026-01-21","end":"2027-01-20","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 21-Jan-2026","v1Done":"2026-01-30","v2Done":"2026-06-20","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2026-07-16","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"WTC overdue.","flag":""},{"id":15,"customer":"Sayed Ahmed Ebrahim","location":"Meadows","address":"Villa 5, Street 11, Meadows 2, Dubai","package":"Premium","ownerTenant":"Owner","start":"2026-02-17","end":"2027-02-16","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 18-Feb-2026","v1Done":"2026-02-28","v2Done":"2026-06-12","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2026-05-04","hm1Status":"Done","hm1Date":"2026-06-08","hm1Job":"3006","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Package corrected from Standard to Premium per contract.","flag":""},{"id":16,"customer":"Humam Kadhim","location":"Hayat","address":"Villa 343, Street A, Hayat Townhouses","package":"Basic","ownerTenant":"Owner","start":"2026-02-28","end":"2027-02-27","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 28-Feb-2026","v1Done":"2026-03-18","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2025-08-13","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Client since 2023. Package corrected from Standard to Basic per contract.","flag":""},{"id":17,"customer":"Richard Rahman","location":"JVT","address":"Townhouse A5, District 5, JVT","package":"Standard","ownerTenant":"Owner","start":"2026-03-20","end":"2027-03-19","payType":"FOC","payStatus":"FOC","payNotes":"Owner's account - no charge","v1Done":"","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Pending","wtcDate":"","hm1Status":"N/A","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":18,"customer":"Mays and Richard","location":"JVT","address":"Villa E9 + Villa 9B (Airbnb), JVT","package":"Premium","ownerTenant":"Owner","start":"2026-04-01","end":"2027-03-31","payType":"FOC","payStatus":"FOC","payNotes":"Owner's account - no charge","v1Done":"2026-06-09","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Pending","wtcDate":"","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"Pending","hm2Date":"","hm2Job":"","notes":"Two units.","flag":""},{"id":19,"customer":"Ali Javed","location":"Naseem Townhouses","address":"Villa 112, Naseem Townhouses, Townsquare","package":"Basic","ownerTenant":"Tenant","start":"2026-04-03","end":"2027-04-02","payType":"50/50","payStatus":"50% Paid","payNotes":"50% paid 03-Apr-2026 / balance due 03-Oct-2026","v1Done":"2026-04-10","v2Done":"2026-08-27","v3Done":"","v4Done":"","wtcStatus":"N/A","wtcDate":"","hm1Status":"N/A","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Balance due Oct 2026.","flag":""},{"id":20,"customer":"Rosha Trading Co LLC","location":"Al Quoz","address":"21 14B Street, Al Quoz Industrial Area 2","package":"Basic","ownerTenant":"Commercial","start":"2026-04-20","end":"2027-04-19","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 20-Apr-2026","v1Done":"2026-04-29","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"N/A","wtcDate":"","hm1Status":"N/A","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Commercial unit.","flag":""},{"id":21,"customer":"Tobias Smallwood","location":"JVT","address":"Villa K4, District 8, JVT","package":"Standard","ownerTenant":"Owner","start":"2026-04-29","end":"2027-04-28","payType":"Upfront","payStatus":"Unpaid","payNotes":"NO PAYMENT RECEIVED - chase urgently","v1Done":"2026-04-30","v2Done":"2026-08-29","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2026-06-25","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Contract active since Apr 2026.","flag":""},{"id":22,"customer":"Kamola Sobirov","location":"Wadi Al Safa","address":"Villa 31, Al Habtoor Polo Resort, Wadi Al Safa 5","package":"Basic","ownerTenant":"Owner","start":"2026-05-30","end":"2027-05-29","payType":"50/50","payStatus":"50% Paid","payNotes":"50% paid 30-May-2026 / balance due 30-Nov-2026","v1Done":"2026-08-07","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2026-06-05","hm1Status":"Done","hm1Date":"2026-06-06","hm1Job":"2943","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"Balance due Nov 2026. Package corrected from Standard to Basic per contract.","flag":""},{"id":23,"customer":"Robert Papp","location":"Business Bay","address":"Apt WF505B, Peninsula One, Business Bay","package":"Basic","ownerTenant":"Tenant","start":"2026-06-10","end":"2027-06-09","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 10-Jun-2026","v1Done":"2026-06-12","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"N/A","wtcDate":"","hm1Status":"N/A","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"New client. Package corrected from Standard to Basic per contract.","flag":""},{"id":24,"customer":"Peter Rosewall","location":"Meydan Mag","address":"Villa TH13-03, Mag Eye City, Meydan, Dubai","package":"Premium","ownerTenant":"Owner","start":"2026-06-22","end":"2027-06-21","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 20-Jun-2026","v1Done":"","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2026-06-21","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"Pending","hm2Date":"","hm2Job":"","notes":"4 AC units.","flag":""},{"id":25,"customer":"Dr Pervaz Ahmad Mohammad","location":"Meydan Mag","address":"","package":"Basic","ownerTenant":"Owner","start":"2026-07-22","end":"2027-07-21","payType":"50/50","payStatus":"50% Paid","payNotes":"50% paid 22-Jul / balance due Jan 2027","v1Done":"2026-07-24","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Done","wtcDate":"2026-07-23","hm1Status":"","hm1Date":"","hm1Job":"","hm2Status":"","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":26,"customer":"Hitan Gohil","location":"Dubai Marina","address":"","package":"Premium","ownerTenant":"Owner","start":"2026-08-13","end":"2027-08-12","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 12-Aug-2026","v1Done":"2026-08-22","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"N/A","wtcDate":"","hm1Status":"Pending","hm1Date":"","hm1Job":"","hm2Status":"Pending","hm2Date":"","hm2Job":"","notes":"","flag":""},{"id":27,"customer":"Rahman Hussain","location":"Jumeirah Park","address":"","package":"Basic","ownerTenant":"Owner","start":"2027-09-01","end":"2027-09-01","payType":"Upfront","payStatus":"Paid","payNotes":"Paid 12-Aug-2026","v1Done":"","v2Done":"","v3Done":"","v4Done":"","wtcStatus":"Pending","wtcDate":"","hm1Status":"N/A","hm1Date":"","hm1Job":"","hm2Status":"N/A","hm2Date":"","hm2Job":"","notes":"","flag":"FLAG: start/end dates both read 01-Sep-2027 in source file - please verify contract start date with the client file."}]
''')

SEED_AMC_HISTORY = json.loads(r'''
[{"customer":"Ioannis Vaxevanos","location":"JVT","year":"2025-26","package":"Premium","start":"2025-06-10","end":"2026-06-10","status":"Active","payment":"Paid","notes":""},{"customer":"Anas Ayman Al Halabi","location":"JVT","year":"2025-26","package":"Standard","start":"2025-06-30","end":"2026-06-30","status":"Active","payment":"Paid","notes":"50%+50%"},{"customer":"Vladimir Tatarchuk","location":"JVT","year":"2025-26","package":"Standard","start":"2025-07-07","end":"2026-07-07","status":"Active","payment":"Paid","notes":""},{"customer":"Vladimir Tatarchuk","location":"Emaar South","year":"2025-26","package":"Standard","start":"2025-07-07","end":"2026-07-07","status":"Active","payment":"Paid","notes":""},{"customer":"Sahar Al-Farra","location":"JP","year":"2025-26","package":"Standard","start":"2025-07-11","end":"2026-07-11","status":"Active","payment":"Paid","notes":""},{"customer":"Doug Wilson","location":"JVT","year":"2025-26","package":"Standard","start":"2025-09-03","end":"2026-09-03","status":"Active","payment":"Paid","notes":""},{"customer":"Andrew Heap","location":"JVT","year":"2025-26","package":"Basic","start":"2025-10-06","end":"2026-10-06","status":"Active","payment":"Paid","notes":"No WTC, no existing invoice for this AMC"},{"customer":"Rami Abdallah Said","location":"Hayat","year":"2025-26","package":"Basic","start":"2025-10-06","end":"2026-10-06","status":"Active","payment":"Paid","notes":""},{"customer":"Consulate Singapore","location":"Satwa","year":"2025-26","package":"Premium","start":"2025-09-01","end":"2026-09-30","status":"Active","payment":"Paid","notes":"Monthly PPM"},{"customer":"Alexander Turnbull","location":"JVT","year":"2025-26","package":"Standard","start":"2025-11-11","end":"2026-11-11","status":"Active","payment":"Paid","notes":""},{"customer":"Chrystelle Azzouz","location":"JVC","year":"2025-26","package":"Basic","start":"2025-12-04","end":"2026-12-02","status":"Active","payment":"Paid","notes":""},{"customer":"Kalidas Chodankar","location":"Arabian Ranches","year":"2025-26","package":"Standard","start":"2025-12-15","end":"2026-12-14","status":"Active","payment":"50% due","notes":"Chase"},{"customer":"Michael Callianiotis","location":"JP","year":"2025-26","package":"Standard","start":"2026-01-06","end":"2027-01-06","status":"Active","payment":"Paid","notes":"Package corrected Premium -> Standard per contract"},{"customer":"Jenny Liu","location":"Tilal Al Ghaf","year":"2025-26","package":"Premium","start":"2026-01-21","end":"2027-01-20","status":"Active","payment":"Paid","notes":""},{"customer":"Sayed Ahmed Ebrahim","location":"Meadows","year":"2025-26","package":"Premium","start":"2026-02-17","end":"2027-02-16","status":"Active","payment":"Paid","notes":"Package corrected Standard -> Premium per contract"},{"customer":"Humam Kadhim","location":"Hayat","year":"2025-26","package":"Basic","start":"2026-02-28","end":"2027-02-27","status":"Active","payment":"Paid","notes":"Client since 2023; package corrected Standard -> Basic per contract"},{"customer":"Richard Rahman","location":"JVT","year":"2025-26","package":"Standard","start":"2026-03-20","end":"2027-03-19","status":"Active","payment":"FOC","notes":""},{"customer":"Mays and Richard","location":"JVT","year":"2025-26","package":"Premium","start":"2026-04-01","end":"2027-03-31","status":"Active","payment":"FOC","notes":"2 units"},{"customer":"Ali Javed","location":"Naseem Townhouses","year":"2025-26","package":"Basic","start":"2026-04-03","end":"2027-04-02","status":"Active","payment":"50% due","notes":"Oct 2026"},{"customer":"Rosha Trading Co LLC","location":"Al Quoz","year":"2025-26","package":"Basic","start":"2026-04-20","end":"2027-04-19","status":"Active","payment":"Paid","notes":""},{"customer":"Tobias Smallwood","location":"JVT","year":"2025-26","package":"Standard","start":"2026-04-29","end":"2027-04-28","status":"Active","payment":"Unpaid","notes":"No payment received"},{"customer":"Kamola Sobirov","location":"Wadi Al Safa","year":"2025-26","package":"Basic","start":"2026-05-30","end":"2027-05-29","status":"Active","payment":"50% due","notes":"Nov 2026"},{"customer":"Robert Papp","location":"Business Bay","year":"2025-26","package":"Basic","start":"2026-06-10","end":"2027-06-09","status":"Active","payment":"Paid","notes":"New client"},{"customer":"Peter Rosewall","location":"Meydan Mag","year":"2026-27","package":"Premium","start":"2026-06-22","end":"2027-06-21","status":"Active","payment":"Paid","notes":""},{"customer":"Michael Callianiotis","location":"JP","year":"2025-26 prev","package":"Standard","start":"2025-01-06","end":"2026-01-06","status":"Expired","payment":"Paid","notes":""},{"customer":"Richard Rahman","location":"JVT","year":"2025-26 prev","package":"Standard","start":"2025-03-20","end":"2026-03-19","status":"Expired","payment":"FOC","notes":""},{"customer":"Mays and Richard","location":"JVT","year":"2025-26 prev","package":"Premium","start":"2025-03-11","end":"2026-03-11","status":"Expired","payment":"FOC","notes":""},{"customer":"Olivier Mettraux","location":"JGE","year":"2025-26","package":"Standard","start":"2025-03-11","end":"2026-03-11","status":"Expired","payment":"Paid","notes":""},{"customer":"Michael Callianiotis","location":"JP","year":"2024-25","package":"Standard","start":"2025-01-02","end":"2026-01-02","status":"Expired","payment":"Paid","notes":""},{"customer":"Humam Kadhim","location":"Hayat","year":"2024-25","package":"Standard","start":"2025-01-09","end":"2026-01-09","status":"Expired","payment":"Paid","notes":""},{"customer":"Jahangir","location":"Furjan","year":"2024-25","package":"Standard","start":"2025-01-02","end":"2026-01-02","status":"Expired","payment":"Paid","notes":""},{"customer":"Vladimir Tatarchuk","location":"JVT","year":"2024-25","package":"Basic","start":"2024-06-14","end":"2025-06-14","status":"Expired","payment":"Paid","notes":""},{"customer":"Sarah Hammond","location":"JVT","year":"2024-25","package":"Standard","start":"2024-09-01","end":"2025-09-01","status":"Expired","payment":"Paid","notes":"50%+50%"},{"customer":"Doug Wilson","location":"JVT","year":"2024-25","package":"Standard","start":"2024-09-01","end":"2025-09-01","status":"Expired","payment":"Paid","notes":""},{"customer":"Andrew Heap","location":"JVT","year":"2024-25","package":"Basic","start":"2024-10-06","end":"2025-10-06","status":"Expired","payment":"Paid","notes":"No WTC"},{"customer":"Muhammad Ajmal","location":"JVT","year":"2024-25","package":"Standard","start":"2024-10-06","end":"2025-10-06","status":"Expired","payment":"Paid","notes":""},{"customer":"Rasha Alazem","location":"JP","year":"2024-25","package":"Standard","start":"2024-10-28","end":"2025-10-28","status":"Expired","payment":"Paid","notes":""},{"customer":"Rami Abdallah Said","location":"Hayat","year":"2024-25","package":"Standard","start":"2024-10-28","end":"2025-10-28","status":"Expired","payment":"Paid","notes":""},{"customer":"Alexander Turnbull","location":"JVT","year":"2024-25","package":"Standard","start":"2024-10-28","end":"2025-10-28","status":"Expired","payment":"Paid","notes":""},{"customer":"Boom Battle Bar","location":"JBR","year":"2024-25","package":"Premium","start":"2024-11-28","end":"2025-11-28","status":"Expired","payment":"Paid","notes":""},{"customer":"Rami Rahman","location":"JVT","year":"2023-24","package":"Standard","start":"2024-02-22","end":"2025-02-22","status":"Expired","payment":"Paid","notes":""},{"customer":"Andrew Heap","location":"JVT","year":"2023-24","package":"Basic","start":"2023-10-05","end":"2024-10-05","status":"Expired","payment":"Paid","notes":"No WTC"},{"customer":"Humam Kadhim","location":"Hayat","year":"2023-24","package":"Standard","start":"2023-10-18","end":"2024-10-18","status":"Expired","payment":"Paid","notes":""},{"customer":"Boom Battle Bar","location":"JBR","year":"2023-24","package":"Premium","start":"2023-09-28","end":"2024-09-28","status":"Expired","payment":"Paid","notes":""},{"customer":"Ahmed Farruq","location":"JVC","year":"2023-24","package":"Basic","start":"","end":"","status":"Cancelled","payment":"Unpaid","notes":"Rejected"}]
''')

# Category / unit lookups used when seeding pb_materials / pb_labour, shared
# between init_db()'s one-time seed and the admin "Reset This Team to
# Original" action (both must produce identical results).
SEED_MATERIAL_CATEGORIES = {
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
SEED_LABOUR_UNITS = {
    "In-house Labour (per day)": "per day", "In-house Labour (per hour)": "per hour",
    "Extended Area / Additional Painter (per day)": "per day", "Mason Work (per job)": "per job",
    "Electrician (per day)": "per day", "Plumber (per day)": "per day",
    "AC Technician (per day)": "per day", "Carpenter (per day)": "per day", "Admin Fee": "per job",
}


def insert_seed_materials(conn, team):
    """Only Dubai has historical seed data for Materials - a fresh MAG City
    catalog (or a MAG City reset) is legitimately empty, matching the source
    master file this was ported from."""
    if team != "dubai":
        return
    ts = now_iso()
    for name, price in SEED_MATERIALS:
        conn.execute(
            "INSERT INTO pb_materials (id, team, category, item_name, brand, model_or_size, unit, cost, default_sell, supplier, last_updated, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, team, SEED_MATERIAL_CATEGORIES.get(name, "General"), name, None, None, None, None, price, None, ts, ts),
        )
    for name, price in SEED_MATERIALS_V2:
        conn.execute(
            "INSERT INTO pb_materials (id, team, category, item_name, brand, model_or_size, unit, cost, default_sell, supplier, last_updated, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, team, "AC & Plumbing Equipment", name, None, None, None, None, price, None, ts, ts),
        )


def insert_seed_labour(conn, team):
    if team != "dubai":
        return
    ts = now_iso()
    for name, price in SEED_LABOR:
        conn.execute(
            "INSERT INTO pb_labour (id, team, role_name, labour_type, cost, default_sell, unit, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, team, name, "staff", None, price, SEED_LABOUR_UNITS.get(name), ts, ts),
        )


def insert_seed_suppliers(conn, team):
    ts = now_iso()
    for row_team, category, name, location, phone, email, services, preferred in SEED_SUPPLIERS:
        if row_team != team:
            continue
        conn.execute(
            "INSERT INTO pb_suppliers (id, team, category, name, location, phone, email, services, preferred, last_updated, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, team, category, name, location, phone, email, services, 1 if preferred else 0, ts, ts),
        )


ADMIN_SEED_EMAILS = ["ai2@legacygroup.me", "richard@handyman.ae", "tegan@handyman.ae"]
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{6,20}$")
APPROVAL_THRESHOLD_AED = 2000


class _PGCursor:
    """Wraps a psycopg2 cursor so the rest of this file - written against
    sqlite3's `?` placeholders and dict-like `sqlite3.Row` results - didn't
    need per-callsite changes for the Postgres move: `?` is rewritten to
    Postgres's `%s` here, and the connection's RealDictCursor factory
    (see get_conn) already gives `row["col"]` access on every result."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        self._cur.execute(sql.replace("?", "%s"), params)
        return self

    def executescript(self, sql):
        # psycopg2 runs a whole multi-statement SQL string in one call as
        # long as no bind parameters are passed - sqlite3's executescript
        # had no such restriction either, so every callsite (all DDL, no
        # params) carries over unchanged.
        self._cur.execute(sql)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class PGConnection:
    """Wraps a psycopg2 connection so it exposes the same surface this file
    was written against (sqlite3.Connection): `conn.execute(...)` as a
    shortcut that returns a cursor, and `conn.executescript(...)` for
    multi-statement DDL."""

    def __init__(self, raw):
        self._raw = raw

    def cursor(self):
        return _PGCursor(self._raw.cursor())

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, sql):
        cur = self.cursor()
        cur.executescript(sql)
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set - see README.txt for how to configure Postgres.")
    raw = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return PGConnection(raw)


QUOTE_NO_PREFIX = {"dubai": "QB-DXB", "magcity": "QB-MAG"}


def next_quote_seq(conn, team):
    """Allocate the next sequential quote number atomically, per team - Dubai
    and MAG City each have their own counter row (quote_no_dubai /
    quote_no_magcity) so their quote numbers count up independently. A
    single UPDATE ... RETURNING takes Postgres's row-level lock on that
    counter row implicitly, so two threads can't both read the same value
    before either writes it back - no explicit BEGIN IMMEDIATE needed
    (that was SQLite's way of getting the same guarantee)."""
    counter_name = "quote_no_" + team
    seq = conn.execute(
        "UPDATE counters SET value = value + 1 WHERE name = ? RETURNING value", (counter_name,)
    ).fetchone()["value"]
    conn.commit()
    return seq


def format_quote_no(seq, team):
    return "%s-%06d" % (QUOTE_NO_PREFIX.get(team, "QB-DXB"), seq)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # Final (already-migrated) schema, created directly rather than replayed
    # through SQLite's old incremental ALTER-guarded history - that history
    # only mattered for evolving a live SQLite file over time; this is a
    # fresh Postgres database, populated from a one-time data migration
    # (see migrate_to_postgres.py), so it starts at the shape the SQLite
    # version eventually reached.
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
            standard_price DOUBLE PRECISION,
            amc_price DOUBLE PRECISION,
            typical_hours DOUBLE PRECISION,
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
            transport_qty DOUBLE PRECISION,
            transport_fee DOUBLE PRECISION,
            call_out INTEGER,
            call_out_fee DOUBLE PRECISION,
            discount_pct DOUBLE PRECISION,
            override_price DOUBLE PRECISION,
            labour_margin_pct DOUBLE PRECISION,
            markup_material_pct DOUBLE PRECISION,
            markup_labour_pct DOUBLE PRECISION,
            markup_subcontractor_pct DOUBLE PRECISION,
            vat_pct DOUBLE PRECISION,
            material_cost DOUBLE PRECISION,
            labour_sell_base DOUBLE PRECISION,
            labour_cost DOUBLE PRECISION,
            sub_cost DOUBLE PRECISION,
            other_cost DOUBLE PRECISION,
            vehicle DOUBLE PRECISION,
            call_out_amount DOUBLE PRECISION,
            material_sell DOUBLE PRECISION,
            labour_sell DOUBLE PRECISION,
            sub_sell DOUBLE PRECISION,
            cost_price DOUBLE PRECISION,
            gross DOUBLE PRECISION,
            discount_amount DOUBLE PRECISION,
            net_selling DOUBLE PRECISION,
            selling_price DOUBLE PRECISION,
            profit DOUBLE PRECISION,
            margin_pct DOUBLE PRECISION,
            margin_band TEXT,
            vat_amount DOUBLE PRECISION,
            grand_total DOUBLE PRECISION,
            created_at TEXT,
            updated_at TEXT,
            created_by_email TEXT,
            status TEXT NOT NULL DEFAULT 'Draft',
            parent_quote_id TEXT,
            root_quote_id TEXT,
            revision_number INTEGER NOT NULL DEFAULT 1,
            quote_seq INTEGER,
            internal_notes TEXT,
            technician TEXT,
            prepared_by_email TEXT,
            markup_pct DOUBLE PRECISION,
            team TEXT NOT NULL DEFAULT 'dubai' CHECK(team IN ('dubai','magcity'))
        );
        CREATE TABLE IF NOT EXISTS quote_items (
            id SERIAL PRIMARY KEY,
            quote_id TEXT NOT NULL REFERENCES quotes(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK(kind IN ('material','staff_labour','outside_labour','fixed_service','project_management','other')),
            description TEXT,
            cost DOUBLE PRECISION NOT NULL DEFAULT 0,
            sell DOUBLE PRECISION NOT NULL DEFAULT 0,
            markup_pct DOUBLE PRECISION,
            qty DOUBLE PRECISION NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            price_book_ref_id TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            created_at TEXT,
            last_login_at TEXT,
            role TEXT NOT NULL DEFAULT 'staff'
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
            value DOUBLE PRECISION NOT NULL,
            updated_by_email TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS quote_audit_log (
            id SERIAL PRIMARY KEY,
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
            id SERIAL PRIMARY KEY,
            template_id TEXT NOT NULL REFERENCES quote_templates(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            description TEXT,
            default_cost DOUBLE PRECISION,
            default_sell DOUBLE PRECISION,
            default_qty DOUBLE PRECISION NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS pb_materials (
            id TEXT PRIMARY KEY,
            category TEXT,
            item_name TEXT NOT NULL,
            brand TEXT,
            model_or_size TEXT,
            unit TEXT,
            cost DOUBLE PRECISION,
            default_sell DOUBLE PRECISION,
            supplier TEXT,
            last_updated TEXT,
            created_at TEXT,
            team TEXT NOT NULL DEFAULT 'dubai'
        );
        CREATE TABLE IF NOT EXISTS pb_labour (
            id TEXT PRIMARY KEY,
            role_name TEXT NOT NULL,
            labour_type TEXT CHECK(labour_type IN ('staff','outside')),
            cost DOUBLE PRECISION,
            default_sell DOUBLE PRECISION,
            unit TEXT,
            last_updated TEXT,
            created_at TEXT,
            team TEXT NOT NULL DEFAULT 'dubai'
        );
        CREATE TABLE IF NOT EXISTS pb_fixed_services (
            id TEXT PRIMARY KEY,
            service_name TEXT NOT NULL,
            category TEXT,
            estimated_cost DOUBLE PRECISION,
            standard_sell DOUBLE PRECISION,
            last_updated TEXT,
            created_at TEXT,
            team TEXT NOT NULL DEFAULT 'dubai'
        );
        CREATE TABLE IF NOT EXISTS pb_suppliers (
            id TEXT PRIMARY KEY,
            team TEXT NOT NULL DEFAULT 'dubai' CHECK(team IN ('dubai','magcity')),
            category TEXT,
            name TEXT NOT NULL,
            location TEXT,
            phone TEXT,
            email TEXT,
            services TEXT,
            preferred INTEGER NOT NULL DEFAULT 0,
            last_updated TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pb_contractors (
            id TEXT PRIMARY KEY,
            team TEXT NOT NULL DEFAULT 'dubai' CHECK(team IN ('dubai','magcity')),
            category TEXT,
            name TEXT NOT NULL,
            location TEXT,
            phone TEXT,
            email TEXT,
            services TEXT,
            preferred INTEGER NOT NULL DEFAULT 0,
            pricing_note TEXT,
            last_updated TEXT,
            created_at TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS pb_contractor_pricing (
            id SERIAL PRIMARY KEY,
            contractor_id TEXT NOT NULL REFERENCES pb_contractors(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            price DOUBLE PRECISION,
            note TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS amc_clients (
            id TEXT PRIMARY KEY,
            team TEXT NOT NULL DEFAULT 'dubai' CHECK(team IN ('dubai','magcity')),
            customer TEXT NOT NULL,
            location TEXT,
            address TEXT,
            package TEXT NOT NULL CHECK(package IN ('Basic','Standard','Premium')),
            owner_tenant TEXT,
            start_date TEXT,
            end_date TEXT,
            pay_type TEXT,
            pay_status TEXT,
            pay_notes TEXT,
            v1_done TEXT, v2_done TEXT, v3_done TEXT, v4_done TEXT,
            wtc_status TEXT, wtc_date TEXT,
            hm1_status TEXT, hm1_date TEXT, hm1_job TEXT,
            hm2_status TEXT, hm2_date TEXT, hm2_job TEXT,
            notes TEXT,
            flag TEXT,
            created_at TEXT, updated_at TEXT,
            created_by_email TEXT, updated_by_email TEXT
        );
        CREATE TABLE IF NOT EXISTS amc_history (
            id SERIAL PRIMARY KEY,
            team TEXT NOT NULL DEFAULT 'dubai' CHECK(team IN ('dubai','magcity')),
            client_id TEXT REFERENCES amc_clients(id) ON DELETE SET NULL,
            customer TEXT, location TEXT, year TEXT, package TEXT,
            start_date TEXT, end_date TEXT, status TEXT, payment TEXT, notes TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS amc_action_handled (
            team TEXT NOT NULL,
            action_key TEXT NOT NULL,
            handled_at TEXT,
            handled_by_email TEXT,
            PRIMARY KEY (team, action_key)
        );
        CREATE TABLE IF NOT EXISTS amc_commercial_rates (
            team TEXT PRIMARY KEY CHECK(team IN ('dubai','magcity')),
            basic_base DOUBLE PRECISION NOT NULL, basic_per DOUBLE PRECISION NOT NULL,
            standard_base DOUBLE PRECISION NOT NULL, standard_per DOUBLE PRECISION NOT NULL,
            premium_base DOUBLE PRECISION NOT NULL, premium_per DOUBLE PRECISION NOT NULL,
            updated_at TEXT, updated_by_email TEXT
        );
        CREATE TABLE IF NOT EXISTS amc_proposal_highlights (
            team TEXT PRIMARY KEY CHECK(team IN ('dubai','magcity')),
            highlights_json TEXT NOT NULL,
            updated_at TEXT, updated_by_email TEXT
        );
        CREATE TABLE IF NOT EXISTS amc_proposal_prices (
            team TEXT NOT NULL CHECK(team IN ('dubai','magcity')),
            property_type TEXT NOT NULL CHECK(property_type IN ('apartment','villa')),
            units INTEGER NOT NULL,
            basic DOUBLE PRECISION NOT NULL,
            standard DOUBLE PRECISION NOT NULL,
            premium DOUBLE PRECISION NOT NULL,
            updated_at TEXT, updated_by_email TEXT,
            PRIMARY KEY (team, property_type, units)
        );
        CREATE TABLE IF NOT EXISTS amc_proposals (
            id TEXT PRIMARY KEY,
            team TEXT NOT NULL DEFAULT 'dubai' CHECK(team IN ('dubai','magcity')),
            client_name TEXT NOT NULL,
            property_address TEXT,
            property_type TEXT NOT NULL CHECK(property_type IN ('apartment','villa','commercial')),
            ac_units INTEGER NOT NULL,
            validity_days INTEGER NOT NULL,
            valid_until TEXT NOT NULL,
            price_basic INTEGER NOT NULL, price_standard INTEGER NOT NULL, price_premium INTEGER NOT NULL,
            override_basic INTEGER, override_standard INTEGER, override_premium INTEGER,
            is_custom INTEGER NOT NULL DEFAULT 0,
            pdf BYTEA NOT NULL,
            converted_to_contract_id TEXT,
            created_at TEXT NOT NULL,
            created_by_email TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS amc_contracts (
            id TEXT PRIMARY KEY,
            team TEXT NOT NULL DEFAULT 'dubai' CHECK(team IN ('dubai','magcity')),
            proposal_id TEXT REFERENCES amc_proposals(id) ON DELETE SET NULL,
            client_name TEXT NOT NULL,
            property_address TEXT,
            property_type TEXT NOT NULL CHECK(property_type IN ('apartment','villa','commercial')),
            ac_units INTEGER NOT NULL,
            package TEXT NOT NULL CHECK(package IN ('Basic','Standard','Premium')),
            pay_plan TEXT NOT NULL CHECK(pay_plan IN ('onetime','monthly')),
            start_date TEXT NOT NULL,
            signatory TEXT NOT NULL CHECK(signatory IN ('tegan','kristofer','govind')),
            contract_value_annual INTEGER NOT NULL,
            pdf BYTEA NOT NULL,
            created_at TEXT NOT NULL,
            created_by_email TEXT NOT NULL
        );
    """)
    conn.commit()

    # amc_proposals.converted_to_contract_id -> amc_contracts(id) is a circular
    # reference (amc_contracts.proposal_id points back the other way) - Postgres,
    # unlike SQLite, refuses a REFERENCES to a table that doesn't exist yet at
    # CREATE TABLE time, so this FK is added afterwards, once, guarded so it's
    # still safe to run every boot.
    cur.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_name = 'amc_proposals_contract_fk'
            ) THEN
                ALTER TABLE amc_proposals
                    ADD CONSTRAINT amc_proposals_contract_fk
                    FOREIGN KEY (converted_to_contract_id) REFERENCES amc_contracts(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)
    conn.commit()

    # migration: drop catalog items superseded by the dedicated Transport/Call-out
    # fields, so they can't be double-counted.
    conn.execute("DELETE FROM subcategories WHERE id IN ('labour.transportation-per-trip','labour.call-out-fee','labour.outside-worker-subcontractor')")
    conn.commit()

    # users.role defaults every existing row to 'staff' for free; admin_allowlist
    # is consulted only at the moment a brand-new user row is created (see
    # find_or_create_user), so it's re-seeded (ON CONFLICT DO NOTHING) every
    # boot rather than gated on "is this the first run" - safe to run forever,
    # and lets ADMIN_SEED_EMAILS grow later with no extra migration step.
    for admin_email in ADMIN_SEED_EMAILS:
        conn.execute(
            "INSERT INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?) ON CONFLICT (email) DO NOTHING",
            (admin_email, "system-seed", now_iso()),
        )
    conn.execute(
        "UPDATE users SET role='admin' WHERE email IN (SELECT email FROM admin_allowlist) AND role != 'admin'"
    )
    conn.commit()

    # migration: sequential quote numbering, one counter per team. A database
    # that still has the old single 'quote_no' counter (from before Dubai and
    # MAG City got separate sequences) carries its value forward into
    # quote_no_dubai, so existing quote numbers never collide with new ones;
    # quote_no_magcity always starts fresh since MAG City had no quotes yet.
    if not conn.execute("SELECT 1 FROM counters WHERE name='quote_no_dubai'").fetchone():
        old = conn.execute("SELECT value FROM counters WHERE name='quote_no'").fetchone()
        conn.execute("INSERT INTO counters (name, value) VALUES ('quote_no_dubai', ?)", (old["value"] if old else 0,))
        conn.commit()
    if not conn.execute("SELECT 1 FROM counters WHERE name='quote_no_magcity'").fetchone():
        conn.execute("INSERT INTO counters (name, value) VALUES ('quote_no_magcity', 0)")
        conn.commit()

    # migration: a database provisioned before Dubai/MAG City separation
    # won't have `team` on `quotes` or `pb_contractors` yet (both are already
    # in the CREATE TABLE above for a fresh database, so this is a no-op
    # there). ADD COLUMN ... DEFAULT 'dubai' backfills every existing row to
    # Dubai in one statement - consistent with how every other team-scoped
    # table was migrated (materials/labour/fixed-services), since Dubai was
    # always the original, sole office before MAG City existed.
    def _has_column(table, column):
        return bool(conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name=?",
            (table, column),
        ).fetchone())

    if not _has_column("quotes", "team"):
        conn.execute("ALTER TABLE quotes ADD COLUMN team TEXT NOT NULL DEFAULT 'dubai'")
        conn.execute("ALTER TABLE quotes ADD CONSTRAINT quotes_team_check CHECK (team IN ('dubai','magcity'))")
        conn.commit()
    if not _has_column("pb_contractors", "team"):
        conn.execute("ALTER TABLE pb_contractors ADD COLUMN team TEXT NOT NULL DEFAULT 'dubai'")
        conn.execute("ALTER TABLE pb_contractors ADD CONSTRAINT pb_contractors_team_check CHECK (team IN ('dubai','magcity'))")
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
        insert_seed_materials(conn, "dubai")
        insert_seed_labour(conn, "dubai")
        conn.commit()

    # migration: add the AC/plumbing equipment items from SEED_MATERIALS_V2
    # (real prices from 3 more job-costing sheets). Guarded per-item rather
    # than by "table empty" so it also backfills a pb_materials table that
    # was already seeded/populated in production.
    ts = now_iso()
    for name, price in SEED_MATERIALS_V2:
        exists = conn.execute("SELECT 1 FROM pb_materials WHERE item_name=? AND team='dubai'", (name,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO pb_materials (id, team, category, item_name, brand, model_or_size, unit, cost, default_sell, supplier, last_updated, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, "dubai", "AC & Plumbing Equipment", name, None, None, None, None, price, None, ts, ts),
            )
    conn.commit()

    # migration: seed Suppliers (team-scoped parts/materials vendors) from the
    # standalone master file, one-time on an empty table.
    if not conn.execute("SELECT 1 FROM pb_suppliers LIMIT 1").fetchone():
        insert_seed_suppliers(conn, "dubai")
        insert_seed_suppliers(conn, "magcity")
        conn.commit()

    # migration: seed Contractors (subcontractors who bill the client
    # directly, each with its own nested rate-card) from the standalone
    # master file, one-time on an empty table. team isn't in this INSERT's
    # column list, so every seeded contractor gets the table's DEFAULT
    # 'dubai' - MAG City starts with an empty Contractors list, same as
    # every other team-scoped catalog.
    if not conn.execute("SELECT 1 FROM pb_contractors LIMIT 1").fetchone():
        ts = now_iso()
        for category, name, location, phone, email, services, preferred, pricing_note, pricing in SEED_CONTRACTORS:
            cid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO pb_contractors (id, category, name, location, phone, email, services, preferred, pricing_note, last_updated, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cid, category, name, location, phone, email, services, 1 if preferred else 0, pricing_note, ts, ts),
            )
            for order, (label, price, note) in enumerate(pricing):
                conn.execute(
                    "INSERT INTO pb_contractor_pricing (contractor_id, label, price, note, sort_order) VALUES (?,?,?,?,?)",
                    (cid, label, price, note, order),
                )
        conn.commit()

    # migration: seed the AMC Tracker's real historical client book (all to
    # 'dubai', see the comment above SEED_AMC_CLIENTS), one-time on an empty
    # table. Ids in the source data become a generated uuid; the original
    # numeric id is kept as amc_id_map purely to link clients to any
    # history row - it has no meaning once seeding is done.
    if not conn.execute("SELECT 1 FROM amc_clients LIMIT 1").fetchone():
        ts = now_iso()
        amc_id_map = {}
        for c in SEED_AMC_CLIENTS:
            cid = uuid.uuid4().hex
            amc_id_map[c["id"]] = cid
            conn.execute(
                "INSERT INTO amc_clients (id, team, customer, location, address, package, owner_tenant, "
                "start_date, end_date, pay_type, pay_status, pay_notes, v1_done, v2_done, v3_done, v4_done, "
                "wtc_status, wtc_date, hm1_status, hm1_date, hm1_job, hm2_status, hm2_date, hm2_job, notes, flag, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, "dubai", c["customer"], c["location"], c["address"], c["package"], c["ownerTenant"],
                 c["start"], c["end"], c["payType"], c["payStatus"], c["payNotes"],
                 c["v1Done"], c["v2Done"], c["v3Done"], c["v4Done"],
                 c["wtcStatus"], c["wtcDate"], c["hm1Status"], c["hm1Date"], c["hm1Job"],
                 c["hm2Status"], c["hm2Date"], c["hm2Job"], c["notes"], c["flag"], ts, ts),
            )
        conn.commit()

    if not conn.execute("SELECT 1 FROM amc_history LIMIT 1").fetchone():
        ts = now_iso()
        for h in SEED_AMC_HISTORY:
            conn.execute(
                "INSERT INTO amc_history (team, customer, location, year, package, start_date, end_date, "
                "status, payment, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("dubai", h["customer"], h["location"], h["year"], h["package"],
                 h["start"], h["end"], h["status"], h["payment"], h["notes"], ts),
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
        "createdBy": row["created_by_email"], "team": row["team"],
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
    for it in items:
        try:
            item_cost = float(it.get("cost") or 0)
        except (TypeError, ValueError):
            item_cost = 0
        try:
            item_sell = float(it.get("sell") or 0)
        except (TypeError, ValueError):
            item_sell = 0
        if item_cost < 0:
            raise SaveError(400, "A line item's cost cannot be negative.")
        if item_sell < 0:
            raise SaveError(400, "A line item's sell price cannot be negative.")
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
        team = payload.get("team") if payload.get("team") in ("dubai", "magcity") else "dubai"
        seq = next_quote_seq(conn, team)
        quote_no = format_quote_no(seq, team)
        insert_cols = ["id", "quote_no", "quote_seq", "root_quote_id", "revision_number",
                       "prepared_by_email", "team"] + field_cols + ["created_by_email", "created_at", "updated_at"]
        insert_values = (quote_id, quote_no, seq, quote_id, 1, created_by_email, team) + fields + (created_by_email, ts, ts)
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


def pdf_response(pdf_bytes, filename):
    headers = [
        ("Content-Type", "application/pdf"),
        ("Content-Length", str(len(pdf_bytes))),
        ("Content-Disposition", 'attachment; filename="%s.pdf"' % filename.replace('"', "")),
    ]
    return 200, headers, pdf_bytes


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
    sql = "SELECT * FROM quotes WHERE team=?"
    params = [query.get("team") or "dubai"]
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
    seq = next_quote_seq(conn, src["team"])
    cols = [k for k in src.keys() if k not in ("id",)]
    values = [src[c] for c in cols]
    cols += ["id"]
    values += [new_id]
    placeholders = ",".join(["?"] * len(cols))
    conn.execute(f"INSERT INTO quotes ({','.join(cols)}) VALUES ({placeholders})", values)
    conn.execute(
        "UPDATE quotes SET status='Draft', parent_quote_id=NULL, root_quote_id=?, revision_number=1, quote_seq=?, quote_no=?, "
        "created_at=?, updated_at=?, created_by_email=?, prepared_by_email=? WHERE id=?",
        (new_id, seq, format_quote_no(seq, src["team"]), ts, ts, user["email"], user["email"], new_id),
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

def dashboard_data(query):
    team = query.get("team") or "dubai"
    conn = get_conn()
    approval_rows = conn.execute(
        "SELECT id, quote_no, client_name, selling_price, discount_pct, prepared_by_email, created_by_email, created_at "
        "FROM quotes WHERE status='Approval Required' AND team=? ORDER BY created_at ASC", (team,)
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
        "FROM quotes WHERE status IN ('Approval Required','Sent to Jobber') AND team=?", (team,)
    ).fetchone()
    item_count = (
        conn.execute("SELECT COUNT(*) AS c FROM pb_materials WHERE team=?", (team,)).fetchone()["c"]
        + conn.execute("SELECT COUNT(*) AS c FROM pb_labour WHERE team=?", (team,)).fetchone()["c"]
        + conn.execute("SELECT COUNT(*) AS c FROM pb_fixed_services WHERE team=?", (team,)).fetchone()["c"]
    )

    month_prefix = time.strftime("%Y-%m")
    quotes_this_month = conn.execute(
        "SELECT COUNT(*) AS c FROM quotes WHERE created_at LIKE ? AND team=?", (month_prefix + "%", team)
    ).fetchone()["c"]
    sent_to_jobber_this_month = conn.execute(
        "SELECT COUNT(*) AS c FROM quotes WHERE status='Sent to Jobber' AND created_at LIKE ? AND team=?", (month_prefix + "%", team)
    ).fetchone()["c"]
    drafts_pending = conn.execute("SELECT COUNT(*) AS c FROM quotes WHERE status='Draft' AND team=?", (team,)).fetchone()["c"]

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
    return {"id": r["id"], "team": r["team"], "category": r["category"], "itemName": r["item_name"], "brand": r["brand"],
            "modelOrSize": r["model_or_size"], "unit": r["unit"], "cost": r["cost"], "defaultSell": r["default_sell"],
            "supplier": r["supplier"], "lastUpdated": r["last_updated"]}


def pb_labour_to_dict(r):
    return {"id": r["id"], "team": r["team"], "roleName": r["role_name"], "labourType": r["labour_type"], "cost": r["cost"],
            "defaultSell": r["default_sell"], "unit": r["unit"], "lastUpdated": r["last_updated"]}


def pb_fixed_service_to_dict(r):
    return {"id": r["id"], "team": r["team"], "serviceName": r["service_name"], "category": r["category"],
            "estimatedCost": r["estimated_cost"], "standardSell": r["standard_sell"], "lastUpdated": r["last_updated"]}


def list_pb_materials(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_materials WHERE team=?"
    params = [query.get("team") or "dubai"]
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
        "INSERT INTO pb_materials (id, team, category, item_name, brand, model_or_size, unit, cost, default_sell, supplier, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (mid, body.get("team") or "dubai", body.get("category"), body.get("itemName") or "Untitled", body.get("brand"), body.get("modelOrSize"),
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
        "team": body.get("team", row["team"]),
        "category": body.get("category", row["category"]), "item_name": body.get("itemName", row["item_name"]),
        "brand": body.get("brand", row["brand"]), "model_or_size": body.get("modelOrSize", row["model_or_size"]),
        "unit": body.get("unit", row["unit"]), "cost": body.get("cost", row["cost"]),
        "default_sell": body.get("defaultSell", row["default_sell"]), "supplier": body.get("supplier", row["supplier"]),
    }
    conn.execute(
        "UPDATE pb_materials SET team=?, category=?, item_name=?, brand=?, model_or_size=?, unit=?, cost=?, default_sell=?, supplier=?, last_updated=? WHERE id=?",
        (fields["team"], fields["category"], fields["item_name"], fields["brand"], fields["model_or_size"], fields["unit"],
         fields["cost"], fields["default_sell"], fields["supplier"], now_iso(), mid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_materials WHERE id=?", (mid,)).fetchone()
    conn.close()
    return json_response(200, pb_material_to_dict(row))


def delete_pb_material(mid):
    conn = get_conn()
    conn.execute("DELETE FROM pb_materials WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True})


def list_pb_labour(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_labour WHERE team=?"
    params = [query.get("team") or "dubai"]
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
        "INSERT INTO pb_labour (id, team, role_name, labour_type, cost, default_sell, unit, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (lid, body.get("team") or "dubai", body.get("roleName") or "Untitled", body.get("labourType") or "staff", body.get("cost"),
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
        "UPDATE pb_labour SET team=?, role_name=?, labour_type=?, cost=?, default_sell=?, unit=?, last_updated=? WHERE id=?",
        (body.get("team", row["team"]), body.get("roleName", row["role_name"]), body.get("labourType", row["labour_type"]),
         body.get("cost", row["cost"]), body.get("defaultSell", row["default_sell"]),
         body.get("unit", row["unit"]), now_iso(), lid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_labour WHERE id=?", (lid,)).fetchone()
    conn.close()
    return json_response(200, pb_labour_to_dict(row))


def delete_pb_labour(lid):
    conn = get_conn()
    conn.execute("DELETE FROM pb_labour WHERE id=?", (lid,))
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True})


def list_pb_fixed_services(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_fixed_services WHERE team=?"
    params = [query.get("team") or "dubai"]
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
        "INSERT INTO pb_fixed_services (id, team, service_name, category, estimated_cost, standard_sell, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (fid, body.get("team") or "dubai", body.get("serviceName") or "Untitled", body.get("category"), body.get("estimatedCost"), body.get("standardSell"), ts, ts),
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
        "UPDATE pb_fixed_services SET team=?, service_name=?, category=?, estimated_cost=?, standard_sell=?, last_updated=? WHERE id=?",
        (body.get("team", row["team"]), body.get("serviceName", row["service_name"]), body.get("category", row["category"]),
         body.get("estimatedCost", row["estimated_cost"]), body.get("standardSell", row["standard_sell"]), now_iso(), fid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_fixed_services WHERE id=?", (fid,)).fetchone()
    conn.close()
    return json_response(200, pb_fixed_service_to_dict(row))


# ---------------------------------------------------------------------------
# Price Book v2 - Suppliers and Contractors (parts/materials vendors and
# subcontractors who bill the client directly). Both are team-scoped, same
# as Materials/Labour/Fixed Services - Dubai and MAG City each keep their
# own list, including each contractor's nested rate-card rows.
# ---------------------------------------------------------------------------

def pb_supplier_to_dict(r):
    return {"id": r["id"], "team": r["team"], "category": r["category"], "name": r["name"], "location": r["location"],
            "phone": r["phone"], "email": r["email"], "services": r["services"], "preferred": bool(r["preferred"]),
            "lastUpdated": r["last_updated"]}


def list_pb_suppliers(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_suppliers WHERE team=?"
    params = [query.get("team") or "dubai"]
    q = query.get("q")
    if q:
        like = "%" + q + "%"
        sql += " AND (name LIKE ? OR category LIKE ? OR location LIKE ? OR services LIKE ?)"
        params.extend([like, like, like, like])
    sql += " ORDER BY category, name"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return json_response(200, {"suppliers": [pb_supplier_to_dict(r) for r in rows]})


def create_pb_supplier(body):
    conn = get_conn()
    sid = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        "INSERT INTO pb_suppliers (id, team, category, name, location, phone, email, services, preferred, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sid, body.get("team") or "dubai", body.get("category"), body.get("name") or "Untitled", body.get("location"),
         body.get("phone"), body.get("email"), body.get("services"), 1 if body.get("preferred") else 0, ts, ts),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_suppliers WHERE id=?", (sid,)).fetchone()
    conn.close()
    return json_response(201, pb_supplier_to_dict(row))


def update_pb_supplier(sid, body):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pb_suppliers WHERE id=?", (sid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    preferred = body.get("preferred", bool(row["preferred"]))
    conn.execute(
        "UPDATE pb_suppliers SET team=?, category=?, name=?, location=?, phone=?, email=?, services=?, preferred=?, last_updated=? WHERE id=?",
        (body.get("team", row["team"]), body.get("category", row["category"]), body.get("name", row["name"]),
         body.get("location", row["location"]), body.get("phone", row["phone"]), body.get("email", row["email"]),
         body.get("services", row["services"]), 1 if preferred else 0, now_iso(), sid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_suppliers WHERE id=?", (sid,)).fetchone()
    conn.close()
    return json_response(200, pb_supplier_to_dict(row))


def delete_pb_supplier(sid):
    conn = get_conn()
    conn.execute("DELETE FROM pb_suppliers WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True})


def pb_contractor_pricing_to_dict(r):
    return {"id": r["id"], "label": r["label"], "price": r["price"], "note": r["note"]}


def pb_contractor_to_dict(r, pricing_rows):
    return {"id": r["id"], "team": r["team"], "category": r["category"], "name": r["name"], "location": r["location"],
            "phone": r["phone"], "email": r["email"], "services": r["services"], "preferred": bool(r["preferred"]),
            "pricingNote": r["pricing_note"], "notes": r["notes"], "lastUpdated": r["last_updated"],
            "pricing": [pb_contractor_pricing_to_dict(p) for p in pricing_rows]}


def list_pb_contractors(query):
    conn = get_conn()
    sql = "SELECT * FROM pb_contractors WHERE team=?"
    params = [query.get("team") or "dubai"]
    q = query.get("q")
    if q:
        like = "%" + q + "%"
        sql += " AND (name LIKE ? OR category LIKE ? OR services LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY category, name"
    rows = conn.execute(sql, params).fetchall()
    out = []
    for r in rows:
        pricing_rows = conn.execute(
            "SELECT * FROM pb_contractor_pricing WHERE contractor_id=? ORDER BY sort_order", (r["id"],)
        ).fetchall()
        out.append(pb_contractor_to_dict(r, pricing_rows))
    conn.close()
    return json_response(200, {"contractors": out})


def create_pb_contractor(body):
    conn = get_conn()
    cid = uuid.uuid4().hex
    ts = now_iso()
    conn.execute(
        "INSERT INTO pb_contractors (id, team, category, name, location, phone, email, services, preferred, pricing_note, notes, last_updated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, body.get("team") or "dubai", body.get("category"), body.get("name") or "Untitled", body.get("location"), body.get("phone"),
         body.get("email"), body.get("services"), 1 if body.get("preferred") else 0, body.get("pricingNote"),
         body.get("notes"), ts, ts),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_contractors WHERE id=?", (cid,)).fetchone()
    conn.close()
    return json_response(201, pb_contractor_to_dict(row, []))


def update_pb_contractor(cid, body):
    conn = get_conn()
    row = conn.execute("SELECT * FROM pb_contractors WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    preferred = body.get("preferred", bool(row["preferred"]))
    conn.execute(
        "UPDATE pb_contractors SET team=?, category=?, name=?, location=?, phone=?, email=?, services=?, preferred=?, pricing_note=?, notes=?, last_updated=? WHERE id=?",
        (body.get("team", row["team"]), body.get("category", row["category"]), body.get("name", row["name"]), body.get("location", row["location"]),
         body.get("phone", row["phone"]), body.get("email", row["email"]), body.get("services", row["services"]),
         1 if preferred else 0, body.get("pricingNote", row["pricing_note"]), body.get("notes", row["notes"]),
         now_iso(), cid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pb_contractors WHERE id=?", (cid,)).fetchone()
    pricing_rows = conn.execute("SELECT * FROM pb_contractor_pricing WHERE contractor_id=? ORDER BY sort_order", (cid,)).fetchall()
    conn.close()
    return json_response(200, pb_contractor_to_dict(row, pricing_rows))


def delete_pb_contractor(cid):
    conn = get_conn()
    conn.execute("DELETE FROM pb_contractors WHERE id=?", (cid,))
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True})


def create_pb_contractor_pricing(cid, body):
    conn = get_conn()
    crow = conn.execute("SELECT id FROM pb_contractors WHERE id=?", (cid,)).fetchone()
    if not crow:
        conn.close()
        return json_response(404, {"error": "contractor not found"})
    max_order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM pb_contractor_pricing WHERE contractor_id=?", (cid,)).fetchone()["m"]
    conn.execute(
        "INSERT INTO pb_contractor_pricing (contractor_id, label, price, note, sort_order) VALUES (?,?,?,?,?)",
        (cid, body.get("label") or "Untitled", body.get("price"), body.get("note"), max_order + 1),
    )
    conn.commit()
    conn.execute("UPDATE pb_contractors SET last_updated=? WHERE id=?", (now_iso(), cid))
    conn.commit()
    row = conn.execute("SELECT * FROM pb_contractors WHERE id=?", (cid,)).fetchone()
    pricing_rows = conn.execute("SELECT * FROM pb_contractor_pricing WHERE contractor_id=? ORDER BY sort_order", (cid,)).fetchall()
    conn.close()
    return json_response(201, pb_contractor_to_dict(row, pricing_rows))


def update_pb_contractor_pricing(cid, pid, body):
    conn = get_conn()
    prow = conn.execute("SELECT * FROM pb_contractor_pricing WHERE id=? AND contractor_id=?", (pid, cid)).fetchone()
    if not prow:
        conn.close()
        return json_response(404, {"error": "not found"})
    conn.execute(
        "UPDATE pb_contractor_pricing SET label=?, price=?, note=? WHERE id=?",
        (body.get("label", prow["label"]), body.get("price", prow["price"]), body.get("note", prow["note"]), pid),
    )
    conn.execute("UPDATE pb_contractors SET last_updated=? WHERE id=?", (now_iso(), cid))
    conn.commit()
    row = conn.execute("SELECT * FROM pb_contractors WHERE id=?", (cid,)).fetchone()
    pricing_rows = conn.execute("SELECT * FROM pb_contractor_pricing WHERE contractor_id=? ORDER BY sort_order", (cid,)).fetchall()
    conn.close()
    return json_response(200, pb_contractor_to_dict(row, pricing_rows))


def delete_pb_contractor_pricing(cid, pid):
    conn = get_conn()
    conn.execute("DELETE FROM pb_contractor_pricing WHERE id=? AND contractor_id=?", (pid, cid))
    conn.execute("UPDATE pb_contractors SET last_updated=? WHERE id=?", (now_iso(), cid))
    conn.commit()
    row = conn.execute("SELECT * FROM pb_contractors WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    pricing_rows = conn.execute("SELECT * FROM pb_contractor_pricing WHERE contractor_id=? ORDER BY sort_order", (cid,)).fetchall()
    conn.close()
    return json_response(200, pb_contractor_to_dict(row, pricing_rows))


# ---------------------------------------------------------------------------
# AMC Tracker - clients, contract history, "today's actions" handled-state.
# Team-scoped (dubai/magcity) like Materials/Labour/Suppliers. All the
# scheduling/status math lives in amc.py so it's identical whether it's
# computed here or unit-tested directly.
# ---------------------------------------------------------------------------

def amc_client_to_dict(r):
    client = {
        "id": r["id"], "team": r["team"], "customer": r["customer"], "location": r["location"],
        "address": r["address"], "package": r["package"], "ownerTenant": r["owner_tenant"],
        "start": r["start_date"], "end": r["end_date"], "payType": r["pay_type"],
        "payStatus": r["pay_status"], "payNotes": r["pay_notes"],
        "v1Done": r["v1_done"], "v2Done": r["v2_done"], "v3Done": r["v3_done"], "v4Done": r["v4_done"],
        "wtcStatus": r["wtc_status"], "wtcDate": r["wtc_date"],
        "hm1Status": r["hm1_status"], "hm1Date": r["hm1_date"], "hm1Job": r["hm1_job"],
        "hm2Status": r["hm2_status"], "hm2Date": r["hm2_date"], "hm2Job": r["hm2_job"],
        "notes": r["notes"], "flag": r["flag"], "lastUpdated": r["updated_at"],
    }
    client.update(amc.client_computed(client))
    return client


AMC_CLIENT_FIELD_COLUMNS = {
    "team": "team", "customer": "customer", "location": "location", "address": "address",
    "package": "package", "ownerTenant": "owner_tenant", "start": "start_date", "end": "end_date",
    "payType": "pay_type", "payStatus": "pay_status", "payNotes": "pay_notes",
    "v1Done": "v1_done", "v2Done": "v2_done", "v3Done": "v3_done", "v4Done": "v4_done",
    "wtcStatus": "wtc_status", "wtcDate": "wtc_date",
    "hm1Status": "hm1_status", "hm1Date": "hm1_date", "hm1Job": "hm1_job",
    "hm2Status": "hm2_status", "hm2Date": "hm2_date", "hm2Job": "hm2_job",
    "notes": "notes", "flag": "flag",
}


def list_amc_clients(query):
    team = query.get("team") or "dubai"
    rows = conn_fetch_amc_clients(team)
    return json_response(200, {"clients": [amc_client_to_dict(r) for r in rows]})


def conn_fetch_amc_clients(team):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM amc_clients WHERE team=? ORDER BY customer", (team,)).fetchall()
    conn.close()
    return rows


def create_amc_client(body, actor_email):
    team = body.get("team") or "dubai"
    package = body.get("package")
    if package not in amc.PACKAGES:
        return json_response(400, {"error": "package must be one of %s" % (amc.PACKAGES,)})
    if not (body.get("customer") or "").strip():
        return json_response(400, {"error": "customer is required"})
    if not body.get("start") or not body.get("end"):
        return json_response(400, {"error": "contract start and end dates are required"})
    if amc.parse_date(body.get("end")) < amc.parse_date(body.get("start")):
        return json_response(400, {"error": "contract end must be on or after the start date"})

    hm_allowed = amc.HANDYMAN_VISITS.get(package, 0)
    cid = uuid.uuid4().hex
    ts = now_iso()
    conn = get_conn()
    conn.execute(
        "INSERT INTO amc_clients (id, team, customer, location, address, package, owner_tenant, start_date, "
        "end_date, pay_type, pay_status, pay_notes, v1_done, v2_done, v3_done, v4_done, wtc_status, wtc_date, "
        "hm1_status, hm1_date, hm1_job, hm2_status, hm2_date, hm2_job, notes, flag, created_at, updated_at, "
        "created_by_email, updated_by_email) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, team, body.get("customer", "").strip(), (body.get("location") or "").strip(),
         (body.get("address") or "").strip(), package, body.get("ownerTenant") or "Owner",
         body.get("start"), body.get("end"), body.get("payType") or "Upfront", body.get("payStatus") or "Paid",
         (body.get("payNotes") or "").strip(), "", "", "", "",
         "Pending", "", "Pending" if hm_allowed >= 1 else "N/A", "", "",
         "Pending" if hm_allowed >= 2 else "N/A", "", "", "", "", ts, ts, actor_email, actor_email),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM amc_clients WHERE id=?", (cid,)).fetchone()
    conn.close()
    return json_response(201, amc_client_to_dict(row))


def update_amc_client(cid, body, actor_email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM amc_clients WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})
    sets, params = [], []
    for api_field, column in AMC_CLIENT_FIELD_COLUMNS.items():
        if api_field in body:
            sets.append(column + "=?")
            params.append(body[api_field])
    if not sets:
        conn.close()
        return json_response(200, amc_client_to_dict(row))
    sets.append("updated_at=?"); params.append(now_iso())
    sets.append("updated_by_email=?"); params.append(actor_email)
    params.append(cid)
    conn.execute("UPDATE amc_clients SET " + ", ".join(sets) + " WHERE id=?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM amc_clients WHERE id=?", (cid,)).fetchone()
    conn.close()
    return json_response(200, amc_client_to_dict(row))


def renew_amc_client(cid, actor_email):
    """Archives the client's current contract year into amc_history, then
    rolls start/end forward to the next year and resets everything that's
    per-year (visit sign-offs, WTC, handyman, payment) back to Pending -
    same defaults create_amc_client() uses for a brand new client. Without
    this, amc_history only ever holds the one-time historical import and a
    client's Contract History panel stays empty forever, no matter how many
    times they actually renew."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM amc_clients WHERE id=?", (cid,)).fetchone()
    if not row:
        conn.close()
        return json_response(404, {"error": "not found"})

    old_start, old_end = amc.parse_date(row["start_date"]), amc.parse_date(row["end_date"])
    if not old_start or not old_end:
        conn.close()
        return json_response(400, {"error": "this client has no valid start/end date to renew from"})

    new_start = old_end + timedelta(days=1)
    new_end = amc.one_year_minus_day(new_start)
    year_label = "%d-%02d" % (old_start.year, (old_start.year + 1) % 100)
    hm_allowed = amc.HANDYMAN_VISITS.get(row["package"], 0)
    ts = now_iso()

    conn.execute(
        "INSERT INTO amc_history (team, client_id, customer, location, year, package, start_date, end_date, "
        "status, payment, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["team"], cid, row["customer"], row["location"], year_label, row["package"],
         row["start_date"], row["end_date"], "Expired", row["pay_status"], row["pay_notes"], ts),
    )
    conn.execute(
        "UPDATE amc_clients SET start_date=?, end_date=?, pay_status='Unpaid', pay_notes='', "
        "v1_done='', v2_done='', v3_done='', v4_done='', wtc_status='Pending', wtc_date='', "
        "hm1_status=?, hm1_date='', hm1_job='', hm2_status=?, hm2_date='', hm2_job='', flag='', "
        "updated_at=?, updated_by_email=? WHERE id=?",
        (new_start.isoformat(), new_end.isoformat(),
         "Pending" if hm_allowed >= 1 else "N/A", "Pending" if hm_allowed >= 2 else "N/A",
         ts, actor_email, cid),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM amc_clients WHERE id=?", (cid,)).fetchone()
    conn.close()
    return json_response(200, amc_client_to_dict(row))


def amc_history_to_dict(r):
    return {"id": r["id"], "team": r["team"], "clientId": r["client_id"], "customer": r["customer"],
            "location": r["location"], "year": r["year"], "package": r["package"], "start": r["start_date"],
            "end": r["end_date"], "status": r["status"], "payment": r["payment"], "notes": r["notes"]}


def list_amc_history(query):
    team = query.get("team") or "dubai"
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM amc_history WHERE team=? ORDER BY start_date DESC, id DESC", (team,)
    ).fetchall()
    conn.close()
    return json_response(200, {"history": [amc_history_to_dict(r) for r in rows]})


def amc_meta():
    return json_response(200, {
        "packageSchedule": {
            pkg: [{"n": n, "off": off} for n, off in offs] for pkg, offs in amc.PACKAGE_SCHEDULE.items()
        },
        "handymanVisits": amc.HANDYMAN_VISITS,
        "packagePrices": amc.PACKAGE_PRICES,
        "packages": list(amc.PACKAGES),
        "ownerTenantOptions": list(amc.OWNER_TENANT_OPTIONS),
        "payTypeOptions": list(amc.PAY_TYPE_OPTIONS),
        "payStatusOptions": list(amc.PAY_STATUS_OPTIONS),
        "visitStatusOptions": list(amc.VISIT_STATUS_OPTIONS),
    })


def list_amc_actions_handled(query):
    team = query.get("team") or "dubai"
    conn = get_conn()
    rows = conn.execute("SELECT action_key FROM amc_action_handled WHERE team=?", (team,)).fetchall()
    conn.close()
    return json_response(200, {"handled": [r["action_key"] for r in rows]})


def set_amc_action_handled(body, actor_email):
    team = body.get("team") or "dubai"
    key = body.get("actionKey")
    if not key:
        return json_response(400, {"error": "actionKey is required"})
    conn = get_conn()
    if body.get("handled"):
        conn.execute(
            "INSERT INTO amc_action_handled (team, action_key, handled_at, handled_by_email) VALUES (?,?,?,?) "
            "ON CONFLICT(team, action_key) DO UPDATE SET handled_at=excluded.handled_at, handled_by_email=excluded.handled_by_email",
            (team, key, now_iso(), actor_email),
        )
    else:
        conn.execute("DELETE FROM amc_action_handled WHERE team=? AND action_key=?", (team, key))
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True})


# ---------------------------------------------------------------------------
# AMC Proposals & Contracts - proposal/contract PDF generation and storage.
# Team-scoped like the AMC Tracker. Pricing/inclusions data lives in
# amc_proposal.py (pure, ported 1:1 from the original tool) and PDF layout in
# amc_pdf.py; this section is just persistence + routing glue. Generated PDFs
# are stored as immutable BLOBs alongside their structured inputs - once
# issued, a proposal/contract's PDF never changes even if pricing/templates
# change later, which matters for a real legal document trail.
# ---------------------------------------------------------------------------

def _safe_filename(s):
    return re.sub(r'[\\/:*?"<>|]', "", s or "").strip() or "Client"


def amc_proposals_meta():
    return json_response(200, {
        "tiers": list(amc_proposal.TIERS),
        "tags": list(amc_proposal.TAGS),
        "propertyTypes": list(amc_proposal.PROPERTY_TYPES),
        "typeLabel": amc_proposal.TYPE_LABEL,
        "unitRange": {k: list(v) for k, v in amc_proposal.UNIT_RANGE.items()},
        "defaultUnits": amc_proposal.DEFAULT_UNITS,
        "apartmentPrices": {str(k): list(v) for k, v in amc_proposal.APARTMENT_PRICES.items()},
        "villaPrices": {str(k): list(v) for k, v in amc_proposal.VILLA_PRICES.items()},
        "commercialDefaults": amc_proposal.COMMERCIAL_RATE_DEFAULTS,
        "markup": amc_proposal.MARKUP, "deposit": amc_proposal.DEPOSIT,
        "instalments": amc_proposal.INSTALMENTS,
        "matThreshold": list(amc_proposal.MAT_THRESHOLD),
        "validityOptions": list(amc_proposal.VALIDITY_OPTIONS),
        "payPlans": list(amc_proposal.PAY_PLANS),
        "signatories": amc_proposal.SIGNATORIES,
    })


def get_commercial_rates_dict(team):
    conn = get_conn()
    row = conn.execute("SELECT * FROM amc_commercial_rates WHERE team=?", (team,)).fetchone()
    conn.close()
    if not row:
        return amc_proposal.COMMERCIAL_RATE_DEFAULTS
    return {
        "basic": {"base": row["basic_base"], "per": row["basic_per"]},
        "standard": {"base": row["standard_base"], "per": row["standard_per"]},
        "premium": {"base": row["premium_base"], "per": row["premium_per"]},
    }


def amc_commercial_rates_response(query):
    team = query.get("team") or "dubai"
    return json_response(200, {"team": team, "rates": get_commercial_rates_dict(team)})


def update_amc_commercial_rates(body, actor_email):
    team = body.get("team") or "dubai"
    if team not in ("dubai", "magcity"):
        return json_response(400, {"error": "team must be 'dubai' or 'magcity'"})
    rates = body.get("rates") or {}
    try:
        values = {}
        for tier in ("basic", "standard", "premium"):
            values[tier] = {"base": float(rates[tier]["base"]), "per": float(rates[tier]["per"])}
    except (KeyError, TypeError, ValueError):
        return json_response(400, {"error": "rates must include basic/standard/premium, each with base and per"})
    conn = get_conn()
    conn.execute(
        "INSERT INTO amc_commercial_rates (team, basic_base, basic_per, standard_base, standard_per, "
        "premium_base, premium_per, updated_at, updated_by_email) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(team) DO UPDATE SET basic_base=excluded.basic_base, basic_per=excluded.basic_per, "
        "standard_base=excluded.standard_base, standard_per=excluded.standard_per, "
        "premium_base=excluded.premium_base, premium_per=excluded.premium_per, "
        "updated_at=excluded.updated_at, updated_by_email=excluded.updated_by_email",
        (team, values["basic"]["base"], values["basic"]["per"], values["standard"]["base"],
         values["standard"]["per"], values["premium"]["base"], values["premium"]["per"],
         now_iso(), actor_email),
    )
    conn.commit()
    conn.close()
    return amc_commercial_rates_response({"team": team})


def get_proposal_highlights_template(team):
    """The admin-edited 3x5 bullet grid for this team, or None if nothing's
    been saved yet - callers fall back to amc_proposal.DEFAULT_HIGHLIGHTS."""
    conn = get_conn()
    row = conn.execute("SELECT highlights_json FROM amc_proposal_highlights WHERE team=?", (team,)).fetchone()
    conn.close()
    if not row:
        return None
    return json.loads(row["highlights_json"])


def amc_proposal_highlights_response(query):
    team = query.get("team") or "dubai"
    template = get_proposal_highlights_template(team) or amc_proposal.DEFAULT_HIGHLIGHTS
    return json_response(200, {"team": team, "highlights": template})


def update_amc_proposal_highlights(body, actor_email):
    team = body.get("team") or "dubai"
    if team not in ("dubai", "magcity"):
        return json_response(400, {"error": "team must be 'dubai' or 'magcity'"})
    highlights = body.get("highlights")
    if not isinstance(highlights, list) or len(highlights) != 3 or any(len(tier) != 5 for tier in highlights):
        return json_response(400, {"error": "highlights must be 3 tiers of exactly 5 bullets each"})
    cleaned = []
    for tier in highlights:
        row = []
        for b in tier:
            if not isinstance(b, dict) or not isinstance(b.get("text"), str):
                return json_response(400, {"error": "each bullet needs a text string"})
            row.append({"text": b["text"], "off": bool(b.get("off"))})
        cleaned.append(row)
    conn = get_conn()
    conn.execute(
        "INSERT INTO amc_proposal_highlights (team, highlights_json, updated_at, updated_by_email) VALUES (?,?,?,?) "
        "ON CONFLICT(team) DO UPDATE SET highlights_json=excluded.highlights_json, "
        "updated_at=excluded.updated_at, updated_by_email=excluded.updated_by_email",
        (team, json.dumps(cleaned), now_iso(), actor_email),
    )
    conn.commit()
    conn.close()
    return json_response(200, {"team": team, "highlights": cleaned})


def get_saved_proposal_price(team, property_type, units):
    """An admin-saved replacement for this team's default apartment/villa
    price at this unit count, or None if nothing's been saved (callers then
    fall back to amc_proposal.APARTMENT_PRICES/VILLA_PRICES). Commercial has
    its own separate mechanism (amc_commercial_rates) - never call this for
    property_type == 'commercial'."""
    conn = get_conn()
    row = conn.execute(
        "SELECT basic, standard, premium FROM amc_proposal_prices WHERE team=? AND property_type=? AND units=?",
        (team, property_type, units),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return {"basic": row["basic"], "standard": row["standard"], "premium": row["premium"]}


def amc_proposal_prices_response(query):
    team = query.get("team") or "dubai"
    property_type = query.get("propertyType")
    try:
        units = int(query.get("units"))
    except (TypeError, ValueError):
        return json_response(400, {"error": "units must be an integer"})
    if property_type not in ("apartment", "villa"):
        return json_response(400, {"error": "propertyType must be 'apartment' or 'villa' - commercial uses /amc-proposals/commercial-rates"})
    saved = get_saved_proposal_price(team, property_type, units)
    default_row = (amc_proposal.APARTMENT_PRICES if property_type == "apartment" else amc_proposal.VILLA_PRICES).get(units)
    default = {"basic": default_row[0], "standard": default_row[1], "premium": default_row[2]} if default_row else None
    return json_response(200, {
        "team": team, "propertyType": property_type, "units": units,
        "saved": saved, "default": default, "isCustom": saved is not None,
    })


def update_amc_proposal_price(body, actor_email):
    team = body.get("team") or "dubai"
    if team not in ("dubai", "magcity"):
        return json_response(400, {"error": "team must be 'dubai' or 'magcity'"})
    property_type = body.get("propertyType")
    if property_type not in ("apartment", "villa"):
        return json_response(400, {"error": "propertyType must be 'apartment' or 'villa'"})
    try:
        units = int(body.get("units"))
        basic, standard, premium = float(body.get("basic")), float(body.get("standard")), float(body.get("premium"))
    except (TypeError, ValueError):
        return json_response(400, {"error": "units, basic, standard and premium are required numbers"})
    conn = get_conn()
    conn.execute(
        "INSERT INTO amc_proposal_prices (team, property_type, units, basic, standard, premium, updated_at, updated_by_email) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(team, property_type, units) DO UPDATE SET basic=excluded.basic, standard=excluded.standard, "
        "premium=excluded.premium, updated_at=excluded.updated_at, updated_by_email=excluded.updated_by_email",
        (team, property_type, units, basic, standard, premium, now_iso(), actor_email),
    )
    conn.commit()
    conn.close()
    return amc_proposal_prices_response({"team": team, "propertyType": property_type, "units": str(units)})


def amc_proposal_to_dict(r):
    return {
        "id": r["id"], "team": r["team"], "clientName": r["client_name"],
        "propertyAddress": r["property_address"], "propertyType": r["property_type"],
        "acUnits": r["ac_units"], "validityDays": r["validity_days"], "validUntil": r["valid_until"],
        "prices": {"basic": r["price_basic"], "standard": r["price_standard"], "premium": r["price_premium"]},
        "override": {"basic": r["override_basic"], "standard": r["override_standard"],
                      "premium": r["override_premium"]},
        "isCustom": bool(r["is_custom"]),
        "convertedToContractId": r["converted_to_contract_id"],
        "createdAt": r["created_at"], "createdByEmail": r["created_by_email"],
    }


def list_amc_proposals(query):
    team = query.get("team") or "dubai"
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, team, client_name, property_address, property_type, ac_units, validity_days, "
        "valid_until, price_basic, price_standard, price_premium, override_basic, override_standard, "
        "override_premium, is_custom, converted_to_contract_id, created_at, created_by_email "
        "FROM amc_proposals WHERE team=? ORDER BY created_at DESC", (team,)
    ).fetchall()
    conn.close()
    return json_response(200, {"proposals": [amc_proposal_to_dict(r) for r in rows]})


def _num_or_none(v):
    try:
        return round(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def create_amc_proposal(body, actor_email):
    team = body.get("team") or "dubai"
    property_type = body.get("propertyType")
    try:
        ac_units = int(body.get("acUnits"))
    except (TypeError, ValueError):
        ac_units = None
    try:
        validity_days = int(body.get("validityDays"))
    except (TypeError, ValueError):
        validity_days = None

    errors = amc_proposal.validate_proposal_input(
        {"propertyType": property_type, "acUnits": ac_units, "validityDays": validity_days})
    if errors:
        return json_response(400, {"error": "; ".join(errors)})

    commercial_rates = get_commercial_rates_dict(team) if property_type == "commercial" else None
    saved_prices = get_saved_proposal_price(team, property_type, ac_units) if property_type != "commercial" else None
    ob, os_, op = (_num_or_none(body.get("overrideBasic")), _num_or_none(body.get("overrideStandard")),
                   _num_or_none(body.get("overridePremium")))
    override = (ob, os_, op) if any(v is not None for v in (ob, os_, op)) else None
    prices = amc_proposal.price_for(property_type, ac_units, commercial_rates=commercial_rates,
                                     override=override, saved_prices=saved_prices)

    today = date.today()
    valid_until = today + timedelta(days=validity_days)
    client_name = (body.get("clientName") or "").strip()
    property_address = (body.get("propertyAddress") or "").strip()

    pdf_bytes = amc_pdf.generate_proposal_pdf({
        "clientName": client_name, "propertyAddress": property_address, "propertyType": property_type,
        "acUnits": ac_units, "createdDate": today, "validUntil": valid_until, "prices": prices,
        "isCustom": override is not None,
        "highlightsTemplate": get_proposal_highlights_template(team),
    })

    pid = uuid.uuid4().hex
    ts = now_iso()
    conn = get_conn()
    conn.execute(
        "INSERT INTO amc_proposals (id, team, client_name, property_address, property_type, ac_units, "
        "validity_days, valid_until, price_basic, price_standard, price_premium, override_basic, "
        "override_standard, override_premium, is_custom, pdf, created_at, created_by_email) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, team, client_name, property_address, property_type, ac_units, validity_days,
         valid_until.isoformat(), prices[0], prices[1], prices[2], ob, os_, op,
         1 if override is not None else 0, psycopg2.Binary(pdf_bytes), ts, actor_email),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id, team, client_name, property_address, property_type, ac_units, validity_days, "
        "valid_until, price_basic, price_standard, price_premium, override_basic, override_standard, "
        "override_premium, is_custom, converted_to_contract_id, created_at, created_by_email "
        "FROM amc_proposals WHERE id=?", (pid,)
    ).fetchone()
    conn.close()
    return json_response(201, amc_proposal_to_dict(row))


def get_amc_proposal_pdf(pid):
    conn = get_conn()
    row = conn.execute("SELECT client_name, created_at, pdf FROM amc_proposals WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        return not_found()
    stamp = (row["created_at"] or "")[:10]
    filename = "AMC Proposal - %s - %s" % (_safe_filename(row["client_name"]), stamp)
    return pdf_response(bytes(row["pdf"]), filename)


def amc_contract_to_dict(r):
    return {
        "id": r["id"], "team": r["team"], "proposalId": r["proposal_id"], "clientName": r["client_name"],
        "propertyAddress": r["property_address"], "propertyType": r["property_type"],
        "acUnits": r["ac_units"], "package": r["package"], "payPlan": r["pay_plan"],
        "startDate": r["start_date"], "signatory": r["signatory"],
        "contractValueAnnual": r["contract_value_annual"],
        "createdAt": r["created_at"], "createdByEmail": r["created_by_email"],
    }


def list_amc_contracts(query):
    team = query.get("team") or "dubai"
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, team, proposal_id, client_name, property_address, property_type, ac_units, package, "
        "pay_plan, start_date, signatory, contract_value_annual, created_at, created_by_email "
        "FROM amc_contracts WHERE team=? ORDER BY created_at DESC", (team,)
    ).fetchall()
    conn.close()
    return json_response(200, {"contracts": [amc_contract_to_dict(r) for r in rows]})


def create_amc_contract(body, actor_email):
    team = body.get("team") or "dubai"
    property_type = body.get("propertyType")
    try:
        ac_units = int(body.get("acUnits"))
    except (TypeError, ValueError):
        ac_units = None

    errors = amc_proposal.validate_contract_input({
        "propertyType": property_type, "acUnits": ac_units, "package": body.get("package"),
        "payPlan": body.get("payPlan"), "signatory": body.get("signatory"),
        "startDate": body.get("startDate"), "clientName": body.get("clientName"),
    })
    if errors:
        return json_response(400, {"error": "; ".join(errors)})

    start = amc.parse_date(body.get("startDate"))
    if not start:
        return json_response(400, {"error": "startDate must be YYYY-MM-DD"})

    commercial_rates = get_commercial_rates_dict(team) if property_type == "commercial" else None
    saved_prices = get_saved_proposal_price(team, property_type, ac_units) if property_type != "commercial" else None
    row = amc_proposal.contract_price_for(property_type, ac_units, commercial_rates=commercial_rates,
                                           saved_prices=saved_prices)
    tier_idx = amc_proposal.TIERS.index(body["package"])
    annual = row[tier_idx]

    client_name = (body.get("clientName") or "").strip()
    property_address = (body.get("propertyAddress") or "").strip()
    today = date.today()

    pdf_bytes = amc_pdf.generate_contract_pdf({
        "clientName": client_name, "propertyAddress": property_address, "propertyType": property_type,
        "acUnits": ac_units, "package": body["package"], "payPlan": body["payPlan"], "startDate": start,
        "signatory": body["signatory"], "contractDate": today, "commercialRates": commercial_rates,
        "savedPrices": saved_prices,
    })

    cid = uuid.uuid4().hex
    ts = now_iso()
    proposal_id = body.get("proposalId") or None
    conn = get_conn()
    conn.execute(
        "INSERT INTO amc_contracts (id, team, proposal_id, client_name, property_address, property_type, "
        "ac_units, package, pay_plan, start_date, signatory, contract_value_annual, pdf, created_at, "
        "created_by_email) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (cid, team, proposal_id, client_name, property_address, property_type, ac_units, body["package"],
         body["payPlan"], body["startDate"], body["signatory"], annual, psycopg2.Binary(pdf_bytes), ts, actor_email),
    )
    if proposal_id:
        conn.execute("UPDATE amc_proposals SET converted_to_contract_id=? WHERE id=?", (cid, proposal_id))
    conn.commit()
    crow = conn.execute(
        "SELECT id, team, proposal_id, client_name, property_address, property_type, ac_units, package, "
        "pay_plan, start_date, signatory, contract_value_annual, created_at, created_by_email "
        "FROM amc_contracts WHERE id=?", (cid,)
    ).fetchone()
    conn.close()
    return json_response(201, amc_contract_to_dict(crow))


def get_amc_contract_pdf(cid):
    conn = get_conn()
    row = conn.execute(
        "SELECT client_name, package, ac_units, created_at, pdf FROM amc_contracts WHERE id=?", (cid,)
    ).fetchone()
    conn.close()
    if not row:
        return not_found()
    stamp = (row["created_at"] or "")[:10]
    filename = "AMC Contract - %s - %s %s AC units - %s" % (
        _safe_filename(row["client_name"]), row["package"], row["ac_units"], stamp)
    return pdf_response(bytes(row["pdf"]), filename)


def reset_pricebook_team(team):
    """Admin 'Reset This Team to Original': wipes this team's Materials,
    Labour, Fixed Services and Suppliers, then reseeds from the same
    constants init_db() uses on a fresh install. Contractors are untouched -
    there's no seed data for them to reset back to (they're user-entered
    from the start), even though they're team-scoped like everything else."""
    if team not in ("dubai", "magcity"):
        return json_response(400, {"error": "team must be 'dubai' or 'magcity'"})
    conn = get_conn()
    conn.execute("DELETE FROM pb_materials WHERE team=?", (team,))
    conn.execute("DELETE FROM pb_labour WHERE team=?", (team,))
    conn.execute("DELETE FROM pb_fixed_services WHERE team=?", (team,))
    conn.execute("DELETE FROM pb_suppliers WHERE team=?", (team,))
    conn.commit()
    insert_seed_materials(conn, team)
    insert_seed_labour(conn, team)
    insert_seed_suppliers(conn, team)
    conn.commit()
    conn.close()
    return json_response(200, {"ok": True, "team": team})


def export_pricebook(query):
    """Admin 'Export Price Book (JSON)': everything for one team - Materials,
    Labour, Fixed Services, Suppliers and Contractors are all team-scoped."""
    team = query.get("team") or "dubai"
    if team not in ("dubai", "magcity"):
        return json_response(400, {"error": "team must be 'dubai' or 'magcity'"})
    conn = get_conn()
    materials = conn.execute("SELECT * FROM pb_materials WHERE team=? ORDER BY category, item_name", (team,)).fetchall()
    labour = conn.execute("SELECT * FROM pb_labour WHERE team=? ORDER BY role_name", (team,)).fetchall()
    fixed = conn.execute("SELECT * FROM pb_fixed_services WHERE team=? ORDER BY category, service_name", (team,)).fetchall()
    suppliers = conn.execute("SELECT * FROM pb_suppliers WHERE team=? ORDER BY category, name", (team,)).fetchall()
    contractor_rows = conn.execute("SELECT * FROM pb_contractors WHERE team=? ORDER BY category, name", (team,)).fetchall()
    contractors = []
    for r in contractor_rows:
        pricing_rows = conn.execute(
            "SELECT * FROM pb_contractor_pricing WHERE contractor_id=? ORDER BY sort_order", (r["id"],)
        ).fetchall()
        contractors.append(pb_contractor_to_dict(r, pricing_rows))
    conn.close()
    payload = {
        "exportedAt": now_iso(),
        "team": team,
        "materials": [pb_material_to_dict(r) for r in materials],
        "labour": [pb_labour_to_dict(r) for r in labour],
        "fixedServices": [pb_fixed_service_to_dict(r) for r in fixed],
        "suppliers": [pb_supplier_to_dict(r) for r in suppliers],
        "contractors": contractors,
    }
    filename = "pricebook-%s-%s.json" % (team, time.strftime("%Y%m%d"))
    return json_response(200, payload, extra_headers=[("Content-Disposition", 'attachment; filename="%s"' % filename)])


def quotes_autocomplete_fields():
    """Distinct Technician / Client / Property values from past quotes, most
    recently used first, to drive the New Quote autocomplete lists."""
    conn = get_conn()
    technicians = conn.execute(
        "SELECT technician AS v, MAX(created_at) AS latest FROM quotes "
        "WHERE technician IS NOT NULL AND TRIM(technician) != '' GROUP BY technician ORDER BY latest DESC LIMIT 50"
    ).fetchall()
    clients = conn.execute(
        "SELECT client_name AS v, MAX(created_at) AS latest FROM quotes "
        "WHERE client_name IS NOT NULL AND TRIM(client_name) != '' GROUP BY client_name ORDER BY latest DESC LIMIT 50"
    ).fetchall()
    properties = conn.execute(
        "SELECT client_address AS v, MAX(created_at) AS latest FROM quotes "
        "WHERE client_address IS NOT NULL AND TRIM(client_address) != '' GROUP BY client_address ORDER BY latest DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return json_response(200, {
        "technicians": [r["v"] for r in technicians],
        "clients": [r["v"] for r in clients],
        "properties": [r["v"] for r in properties],
    })


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
        conn.execute("INSERT INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?) ON CONFLICT (email) DO NOTHING", (email, actor_email, ts))
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
        conn.execute("INSERT INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?) ON CONFLICT (email) DO NOTHING", (row["email"], actor_email, now_iso()))
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
    conn.execute("INSERT INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?) ON CONFLICT (email) DO NOTHING", (email, actor_email, now_iso()))
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
            return dashboard_data(query)
        if path == "/api/quotes":
            return list_quotes(query)
        if path == "/api/quotes/autocomplete":
            return quotes_autocomplete_fields()
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
        if path == "/api/pricebook/suppliers":
            return list_pb_suppliers(query)
        if path == "/api/pricebook/contractors":
            return list_pb_contractors(query)
        if path == "/api/pricebook/export":
            if not is_admin(user):
                return forbidden()
            return export_pricebook(query)
        if path == "/api/amc/meta":
            return amc_meta()
        if path == "/api/amc/clients":
            return list_amc_clients(query)
        if path == "/api/amc/history":
            return list_amc_history(query)
        if path == "/api/amc/actions-handled":
            return list_amc_actions_handled(query)
        if path == "/api/amc-proposals/meta":
            return amc_proposals_meta()
        if path == "/api/amc-proposals/commercial-rates":
            return amc_commercial_rates_response(query)
        if path == "/api/amc-proposals/highlights":
            return amc_proposal_highlights_response(query)
        if path == "/api/amc-proposals/prices":
            return amc_proposal_prices_response(query)
        if path == "/api/amc-proposals":
            return list_amc_proposals(query)
        m = re.match(r"^/api/amc-proposals/([\w-]+)/pdf$", path)
        if m:
            return get_amc_proposal_pdf(m.group(1))
        if path == "/api/amc-contracts":
            return list_amc_contracts(query)
        m = re.match(r"^/api/amc-contracts/([\w-]+)/pdf$", path)
        if m:
            return get_amc_contract_pdf(m.group(1))
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
        if path == "/api/pricebook/suppliers":
            if not is_admin(user):
                return forbidden()
            return create_pb_supplier(body)
        if path == "/api/pricebook/contractors":
            if not is_admin(user):
                return forbidden()
            return create_pb_contractor(body)
        m = re.match(r"^/api/pricebook/contractors/([\w-]+)/pricing$", path)
        if m:
            if not is_admin(user):
                return forbidden()
            return create_pb_contractor_pricing(m.group(1), body)
        if path == "/api/pricebook/reset":
            if not is_admin(user):
                return forbidden()
            return reset_pricebook_team(body.get("team"))
        if path == "/api/amc/clients":
            return create_amc_client(body, user["email"])
        m = re.match(r"^/api/amc/clients/([\w-]+)/renew$", path)
        if m:
            return renew_amc_client(m.group(1), user["email"])
        if path == "/api/amc/actions-handled":
            return set_amc_action_handled(body, user["email"])
        if path == "/api/amc-proposals":
            return create_amc_proposal(body, user["email"])
        if path == "/api/amc-contracts":
            return create_amc_contract(body, user["email"])
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

    m = re.match(r"^/api/pricebook/suppliers/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_pb_supplier(m.group(1), body)

    m = re.match(r"^/api/amc/clients/([\w-]+)$", path)
    if m:
        return update_amc_client(m.group(1), body, user["email"])

    if path == "/api/amc-proposals/commercial-rates":
        if not is_admin(user):
            return forbidden()
        return update_amc_commercial_rates(body, user["email"])

    if path == "/api/amc-proposals/highlights":
        if not is_admin(user):
            return forbidden()
        return update_amc_proposal_highlights(body, user["email"])

    if path == "/api/amc-proposals/prices":
        if not is_admin(user):
            return forbidden()
        return update_amc_proposal_price(body, user["email"])

    m = re.match(r"^/api/pricebook/contractors/([\w-]+)/pricing/(\d+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_pb_contractor_pricing(m.group(1), int(m.group(2)), body)

    m = re.match(r"^/api/pricebook/contractors/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return update_pb_contractor(m.group(1), body)

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
            quote = save_quote(body, existing_id=m.group(1), created_by_email=user["email"])
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

    m = re.match(r"^/api/pricebook/materials/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return delete_pb_material(m.group(1))

    m = re.match(r"^/api/pricebook/labour/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return delete_pb_labour(m.group(1))

    m = re.match(r"^/api/pricebook/suppliers/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return delete_pb_supplier(m.group(1))

    m = re.match(r"^/api/pricebook/contractors/([\w-]+)/pricing/(\d+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return delete_pb_contractor_pricing(m.group(1), int(m.group(2)))

    m = re.match(r"^/api/pricebook/contractors/([\w-]+)$", path)
    if m:
        if not is_admin(user):
            return forbidden()
        return delete_pb_contractor(m.group(1))

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
        # Never leak internals to the client, but never swallow them either -
        # without this, an unhandled error in any route is completely silent
        # and undebuggable from Railway's logs.
        traceback.print_exc()
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
