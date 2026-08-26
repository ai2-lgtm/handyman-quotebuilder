Handyman.ae Quote Builder - local app
======================================

HOW TO START
1. Double-click start.bat  (or run: python server.py)
2. Your browser opens automatically at http://127.0.0.1:8743
3. Keep the black command window open while you use the app - closing it stops the server.

WHAT'S INSIDE
- server.py        The backend - a WSGI application (Python standard library only, no
                    installs needed). Runs a small SQLite database at data\handyman.db.
                    Structured as request-in/response-out functions around one
                    `application(environ, start_response)` entry point (not a script
                    that binds its own socket), so the exact same code runs both:
                      - locally via `python server.py` (wsgiref's dev server), and
                      - on SiteGround's shared/GrowBig "Python App" tool, which hosts
                        Python through Phusion Passenger and only knows how to call a
                        WSGI `application` callable, not run an arbitrary script.
- passenger_wsgi.py  The file Passenger actually looks for by name - it just imports
                    `application` from server.py. This is what makes SiteGround shared
                    hosting deployment work; you never run this file directly yourself.
- auth.py           Email-domain restriction + session token generation (stdlib only).
- pricing.py        The pricing engine - a verified, line-by-line port of the pricing
                    formula from the original Handyman-Quoting-Suite.html app.
- test_pricing.py             Unit tests: the 24 hand-verified scenarios against pricing.py,
                    plus validation rules (no negative money, discount 0-100%) and the
                    Mark-up% vs Gross Margin% distinction.
- test_server_integration.py  Same scenarios, but saved to and reloaded from the real
                    database (the exact path the running app uses), plus the AED 2,000
                    approval-threshold workflow end to end.
- test_auth.py                Everything about login that doesn't need a real Google
                    account (domain restriction, session cookies, route gating, roles/
                    admin_allowlist, safe failure on a bad callback). Server must
                    already be running.  Run any of these with, e.g.: python test_pricing.py
- test_migration_safety.py    Not part of the regular suite - builds a synthetic
                    pre-v2 database and runs the real migration against it, to check
                    the backfill (status/quote_no/cost-sell reconstruction) before it
                    ever touches production data. Run manually after any schema change.
- public\           The web app (Home / New Quote / Saved Quotes / Price Book / Templates).
- public\login.html / login.js   The "Sign in with Google" page.
- public\wizard-fields.json  The Guided Service Wizard's question sets - 64 services
                    across 13 trades, extracted directly from the original app's source
                    (not retyped by hand, so it's an exact match).
- data\handyman.db  Your database. Back this file up occasionally (just copy it).

LOGIN - GOOGLE SIGN-IN SETUP (required before anyone can log in)
Access is Google Sign-In only, restricted to @kenzieclean.ae, @legacygroup.me and
@handyman.ae Google accounts - Google verifies the person's identity; the app only checks the
resulting email's domain. Everyone who signs in shares the same Price Book and Saved
Quotes list - each saved quote is tagged with who created it (see the "Created By"
column in Saved Quotes).

This needs a Google Cloud OAuth client before it will work - the "Sign in with
Google" button will show a "not configured yet" message until you do this once:

1. Go to https://console.cloud.google.com/ and create a project (or pick an existing
   one) - top-left project dropdown -> "New Project".
2. Left sidebar -> APIs & Services -> OAuth consent screen.
     - User type: External (this works regardless of whether kenzieclean.ae,
       legacygroup.me and handyman.ae are on the same Google Workspace or different
       ones - the app's own domain check is what actually restricts access, not
       this screen).
     - App name: anything (e.g. "Handyman Quote Builder"), fill in the required
       support email fields, save.
3. Left sidebar -> APIs & Services -> Credentials -> "+ Create Credentials" ->
   "OAuth client ID".
     - Application type: Web application.
     - Authorized redirect URIs - add BOTH of these (one per environment):
         http://127.0.0.1:8743/api/auth/google/callback        (local testing)
         https://your-real-domain.com/api/auth/google/callback  (production - use
         the actual domain you deploy to; you can add this one later once you know it)
     - Click Create. Google shows you a Client ID and Client Secret - copy both.
4. Set these as environment variables before starting the server:
     GOOGLE_CLIENT_ID=<the client id>
     GOOGLE_CLIENT_SECRET=<the client secret>
     GOOGLE_REDIRECT_URI=http://127.0.0.1:8743/api/auth/google/callback   (must
       exactly match one of the URIs you added in step 3, protocol and all)
   On Windows PowerShell, that's:
     $env:GOOGLE_CLIENT_ID="..."; $env:GOOGLE_CLIENT_SECRET="..."; $env:GOOGLE_REDIRECT_URI="http://127.0.0.1:8743/api/auth/google/callback"; python server.py
5. Click "Sign in with Google" on the login page and sign in with a
   @kenzieclean.ae, @legacygroup.me or @handyman.ae Google account. Any other
   domain gets turned back at the last step with a generic "not approved" message
   (it deliberately doesn't list the allowed domains) - the domain check happens
   after Google confirms who they are.

Note: this only works if kenzieclean.ae, legacygroup.me and handyman.ae actually use
Google (Google Workspace) for their email. If any of them uses a different email
provider, nobody on that domain has a Google Account tied to their work address, and
Google Sign-In simply won't work for them - they'd need a different login method.

GUIDED SERVICE WIZARD
Click "Start Guided Add" (top of the Quote Builder tab) to pick a trade, then a
specific service (e.g. AC / HVAC -> Split AC), then answer that service's own
questions (brand, access, hours, technicians, etc. - only the ones relevant to that
service). On "Add to Quote" it adds one Labour line for you, and any material a
toggled question suggests (e.g. "Gas top-up required" adds a suggested R410A Gas line
at AED 0 - fill in the real price before saving).

The labour quantity is hours x technicians (or, for day-rate services, days x 8 x
technicians; for AMC contracts, visits/year x hours/visit x technicians) - always
review it before saving, but it's a much closer starting point than a blank line.

PRICING ENGINE (v2 - per-line Cost/Sell)
Every quote line (Material / Staff Labour / Outside Labour / Fixed Service / Project
Management / Other) carries its own Cost and Sell price. Materials additionally carry
a Mark-up % that stays linked to Cost/Sell - edit any one of the three and the other
two update to match (edit Mark-up% -> Sell recalculates; edit Sell -> Mark-up%
recalculates; edit Cost -> Sell recalculates from the mark-up% you last set). Every
other line kind has no mark-up control at all - its billed rate already has margin
baked in, so Cost and Sell are just two independent numbers.

The quote summary always shows two different-on-purpose numbers with the same
numerator (profit): Mark-up % (profit / total cost) and Gross Margin % (profit /
selling price) - these get confused for each other easily, so both are always labelled
and visible side by side. The Live Margin gauge bands on Gross Margin % using the same
30% / 40% / 50% thresholds as before (CRITICAL / WARN / ON TARGET / ABOVE TARGET).
Business rules enforced server-side, not just in the UI: no negative cost, sell or
override price; discount must be between 0% and 100%. Every one of these behaviours is
covered by test_pricing.py.

APPROVAL WORKFLOW & QUOTE STATUS
Every quote is Draft, Approval Required, or Sent to Jobber.
- Draft is always freely editable, at any price.
- A quote with a Selling Price over AED 2,000 cannot go straight to Sent to Jobber -
  it must be Submitted for Approval first. AED 2,000 and under can go straight
  Draft -> Sent to Jobber if you'd rather skip the approval step.
- Only an admin can Approve & Send a quote that's in Approval Required.
- Sent to Jobber quotes are locked - the server rejects any edit to one (not just the
  UI graying it out), so it stays an accurate historical record of exactly what was
  quoted. To change a locked quote, use Create Revision, which opens a new linked
  Draft under the SAME quote number (shown as e.g. QB-000042-R2) - the original stays
  exactly as it was, permanently.
- Duplicate as New Quote instead makes a fully independent copy with its own new
  quote number - use this for a different job that just happens to look similar,
  not a correction to this one.
- Quote numbers are sequential (QB-000001, QB-000002, ...), assigned by the server
  the moment a quote is first saved - never date/time-based, never editable.
- Every create/edit/status-change is written to an audit log (who, when, before/after)
  - not yet exposed in the UI, but queryable directly from the quote_audit_log table
  if you ever need to know who changed a price and when.

ROLES & PERMISSIONS
Two roles: staff (default) and admin. Admins-only can edit the Price Book (Materials /
Labour / Fixed Services), Quote Templates, and approve/send quotes out of Approval
Required. Anyone signed in can create and edit quotes (while still in Draft) and apply
a template. The very first admin is seeded from ADMIN_SEED_EMAILS in server.py
(currently ai2@legacygroup.me) - from then on, any admin can promote or demote another
user's role via PUT /api/users/{id}/role (no dedicated UI screen yet, but the endpoint
is there and admin-gated). A brand-new user's starting role is decided by the
admin_allowlist table at the moment they first sign in.

THE FIVE TABS
- Home            Create New Quote / Search Price Book shortcuts, a Quick Price Search
                   across all three Price Book catalogs, the Approval Required queue
                   (quotes over AED 2,000 waiting on an admin), and a Pricing Snapshot
                   (average mark-up, average gross margin, quotes awaiting approval,
                   Price Book item count).
- New Quote       Client details, scope of work, internal notes (never shown to the
                   client), the Guided Service Wizard, one unified Quote Items table,
                   pricing settings, the Live Margin gauge, and Terms. "Save Quote"
                   writes it to the database - no auto-save. The status actions below
                   the header (Submit for Approval / Send to Jobber / Approve & Send /
                   Create Revision / Duplicate) only appear once the quote is saved.
- Price Book      Three catalogs - Materials (Category/Item/Brand/Model or Size/Unit/
                   Cost/Default Sell/Supplier), Labour (Role/Type/Cost/Default Sell/
                   Unit), and Fixed Services (Category/Service/Estimated Cost/Standard
                   Sell) - each searchable. Adding/editing is admin-only; everyone can
                   browse and pull items into a quote.
- Templates       Reusable line-item bundles for regularly-quoted jobs (e.g. "50L Water
                   Heater Replacement"). Applying one adds every line to your current
                   quote at once - adjust anything afterwards. Creating/editing a
                   template is admin-only.
- Saved Quotes    Every quote ever saved, filterable by date range, status, or a text
                   search (client / quote number / property). "Copy for Jobber" copies
                   a formatted summary of one quote to your clipboard; "Copy Table"
                   copies the whole filtered list as a tab-separated table - both are
                   meant to be pasted straight into Jobber. A Draft can be deleted;
                   anything past Draft can only be revised or duplicated, never deleted
                   - the database is the permanent record once a quote leaves Draft.

NOTES
- HOST/PORT/FORCE_SECURE_COOKIE only matter for the LOCAL dev server (python
  server.py) - Passenger deployments ignore them, since Passenger owns the socket
  and the port itself:
    HOST=0.0.0.0            listen on all network interfaces, not just this PC
    PORT=8743               which port to listen on
    FORCE_SECURE_COOKIE=1   mark the session cookie Secure - set this once the app
                            is served over HTTPS.
  Example: HOST=0.0.0.0 PORT=8080 FORCE_SECURE_COOKIE=1 python server.py
- Data lives in data\handyman.db (SQLite). Copy that one file to back up everything -
  price book, every saved quote, and every user account.

DEPLOYING TO RAILWAY (current plan - no Passenger/WSGI gymnastics needed)
Railway just runs `python server.py` as a normal long-lived process and proxies
traffic to whatever port it tells the app to bind - no Passenger, no
passenger_wsgi.py involved (that file is only used if you deploy to a
Passenger host later; Railway ignores it).

1. Push this repo to GitHub if you haven't already (see the GitHub section
   above), including the new Procfile and requirements.txt at the repo root.
2. Go to https://railway.app -> New Project -> Deploy from GitHub repo ->
   pick this repo. Railway auto-detects Python from requirements.txt and
   uses the Procfile's `web: python server.py` as the start command.
3. Add a Volume (Railway project -> your service -> Settings -> Volumes ->
   "+ New Volume"). Mount path: /data. This is what makes your database
   survive redeploys - without it, Railway's container filesystem resets
   every time you deploy new code, and you'd lose every quote and user.
4. Set these environment variables (service -> Variables):
     HOST=0.0.0.0
     DATA_DIR=/data
     FORCE_SECURE_COOKIE=1
     GOOGLE_CLIENT_ID=<your client id>
     GOOGLE_CLIENT_SECRET=<your client secret>
     GOOGLE_REDIRECT_URI=https://<your-service>.up.railway.app/api/auth/google/callback
   (Railway sets PORT for you automatically - the app already reads it from
   the environment, don't set it yourself.)
5. Railway -> Settings -> Networking -> "Generate Domain" gives you the
   https://<something>.up.railway.app URL. Use that exact URL (with
   /api/auth/google/callback appended) as GOOGLE_REDIRECT_URI above, AND
   add it to Google Cloud Console -> Credentials -> your OAuth client ->
   Authorized redirect URIs.
6. Deploy (Railway does this automatically on every push to your connected
   branch). Visit the generated domain - it should redirect to /login.html;
   confirm a @kenzieclean.ae / @legacygroup.me / @handyman.ae account can sign in and a
   different domain is correctly turned away.
7. Every future `git push` to the connected branch auto-redeploys - no
   manual "Restart" step like Passenger needed.

DEPLOYING TO SITEGROUND (shared / GrowBig - Passenger, no Cloud/VPS needed)
NOTE: this path is on hold - the account this was tested against has no
"Python App" tool under Site Tools -> Devs, and Passenger requires that tool
(or server-level Apache config shared hosting doesn't expose) to route to
passenger_wsgi.py at all. Keeping these steps here in case SiteGround
support enables it on this plan later, or for a different SiteGround
account that does have the tool.
1. Site Tools -> Devs -> Python App (or similar, naming varies by SiteGround plan) ->
   create a new Python app, pick a Python version (3.9+), and note the app's root
   folder path SiteGround gives you (something like ~/python-apps/handyman/).
2. Upload this whole app folder's contents into that root folder - either via the
   Site Tools File Manager, SFTP, or (recommended) `git clone` your GitHub repo
   directly into it over SSH (SiteGround's GrowBig+ plans include SSH access).
3. passenger_wsgi.py must end up at that exact root - SiteGround's Python App tool
   looks for it there automatically, no extra config needed for routing.
4. In the Python App tool's environment variables section, set:
     GOOGLE_CLIENT_ID=<your client id>
     GOOGLE_CLIENT_SECRET=<your client secret>
     GOOGLE_REDIRECT_URI=https://your-domain.com/api/auth/google/callback
     FORCE_SECURE_COOKIE=1
   (register that same https://.../api/auth/google/callback URL in Google Cloud
   Console -> Credentials -> your OAuth client -> Authorized redirect URIs)
5. This app has no pip dependencies, so there's nothing to add to requirements.txt -
   SiteGround's Python App tool may still want one present; an empty file is fine.
6. Restart the Python app from Site Tools whenever you deploy new code (pull the
   latest git commit, then hit "Restart" - Passenger doesn't auto-reload on file
   changes the way `python server.py`'s dev server effectively does on a restart).
7. SiteGround's Site Tools -> Security -> SSL Manager issues a free Let's Encrypt
   certificate for your domain - turn that on so the app is served over HTTPS
   (required for FORCE_SECURE_COOKIE=1 to actually keep sessions working).
8. Visit https://your-domain.com - it should redirect to /login.html - click
   "Sign in with Google" and confirm a @kenzieclean.ae / @legacygroup.me account
   can log in and a different domain is correctly turned away.
