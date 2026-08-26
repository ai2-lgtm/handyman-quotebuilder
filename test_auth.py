"""
Exercises everything about the Google-Sign-In auth flow that doesn't require
a real Google account and a human clicking "Allow" - domain restriction,
session/cookie mechanics, route gating, and that a malformed OAuth callback
fails safely rather than crashing.

What this can't verify: the actual Google handshake (needs a real
GOOGLE_CLIENT_ID/SECRET and a real browser login) - that part has to be
checked by hand once Google Cloud credentials are set up (see README.txt).

Run:  python test_auth.py   (server must already be running)
"""
import http.cookiejar
import json
import urllib.error
import urllib.request

import auth
import server

BASE = "http://127.0.0.1:8743"


def request(method, path, cookie_jar=None, allow_redirects=False):
    handlers = [] if allow_redirects else [_NoRedirect()]
    if cookie_jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with opener.open(req) as resp:
            body = resp.read()
            return resp.status, resp.getheader("Location"), body
    except urllib.error.HTTPError as e:
        return e.code, e.getheader("Location"), e.read()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    failures = []

    def check(label, cond):
        print(("PASS" if cond else "FAIL"), "-", label)
        if not cond:
            failures.append(label)

    # ---- pure domain-check logic, no server needed ----
    check("allows @kenzieclean.ae", auth.is_allowed_email("person@kenzieclean.ae"))
    check("allows @legacygroup.me", auth.is_allowed_email("person@legacygroup.me"))
    check("rejects gmail.com", not auth.is_allowed_email("person@gmail.com"))
    check("rejects a lookalike domain", not auth.is_allowed_email("person@kenzieclean.ae.evil.com"))
    check("case-insensitive domain match", auth.is_allowed_email("Person@KenzieClean.AE"))

    # ---- route gating, without needing a real Google login ----
    status, _, _ = request("GET", "/api/quotes")
    check("no cookie at all -> /api/quotes is 401", status == 401)

    status, _, _ = request("GET", "/api/auth/me")
    check("no cookie -> /api/auth/me is 401", status == 401)

    # simulate what a completed Google login leaves behind: a user row and a
    # session token, without actually calling Google.
    test_email = "qa-test-user@kenzieclean.ae"
    user_id = server.find_or_create_user(test_email, "QA Test User")
    token = server.create_session(user_id)

    jar = http.cookiejar.CookieJar()
    jar.set_cookie(http.cookiejar.Cookie(
        0, server.SESSION_COOKIE_NAME, token, None, False, "127.0.0.1", False, False, "/", True,
        False, None, False, None, None, {},
    ))

    status, _, _ = request("GET", "/api/auth/me", cookie_jar=jar)
    check("with a manually-issued session, /api/auth/me succeeds", status == 200)

    status, _, _ = request("GET", "/api/quotes", cookie_jar=jar)
    check("with a valid session, /api/quotes is reachable", status == 200)

    status, _, _ = request("POST", "/api/auth/logout", cookie_jar=jar)
    check("logout succeeds", status == 200)

    status, _, _ = request("GET", "/api/quotes", cookie_jar=jar)
    check("after logout, the old session no longer works", status == 401)

    # ---- roles: admin_allowlist decides a brand-new user's initial role ----
    conn = server.get_conn()
    conn.execute("INSERT OR IGNORE INTO admin_allowlist (email, added_by_email, added_at) VALUES (?,?,?)",
                 ("qa-admin-user@kenzieclean.ae", "test", server.now_iso()))
    conn.commit()
    conn.close()

    admin_email = "qa-admin-user@kenzieclean.ae"
    staff_email = "qa-staff-user@kenzieclean.ae"
    admin_user_id = server.find_or_create_user(admin_email, "QA Admin")
    staff_user_id = server.find_or_create_user(staff_email, "QA Staff")

    conn = server.get_conn()
    admin_role = conn.execute("SELECT role FROM users WHERE id=?", (admin_user_id,)).fetchone()["role"]
    staff_role = conn.execute("SELECT role FROM users WHERE id=?", (staff_user_id,)).fetchone()["role"]
    conn.close()
    check("a new user whose email is on admin_allowlist gets role='admin'", admin_role == "admin")
    check("a new user NOT on admin_allowlist gets role='staff'", staff_role == "staff")

    admin_token = server.create_session(admin_user_id)
    staff_token = server.create_session(staff_user_id)
    admin_jar = http.cookiejar.CookieJar()
    admin_jar.set_cookie(http.cookiejar.Cookie(
        0, server.SESSION_COOKIE_NAME, admin_token, None, False, "127.0.0.1", False, False, "/", True,
        False, None, False, None, None, {},
    ))
    staff_jar = http.cookiejar.CookieJar()
    staff_jar.set_cookie(http.cookiejar.Cookie(
        0, server.SESSION_COOKIE_NAME, staff_token, None, False, "127.0.0.1", False, False, "/", True,
        False, None, False, None, None, {},
    ))

    status, _, _ = request("GET", "/api/auth/me", cookie_jar=admin_jar)
    check("admin session reaches /api/auth/me", status == 200)

    status, _, _ = request("POST", "/api/pricebook/categories", cookie_jar=staff_jar)
    check("a staff user is forbidden from an admin-only route", status == 403)

    status, _, _ = request("POST", "/api/pricebook/categories", cookie_jar=admin_jar)
    check("an admin user is NOT forbidden from an admin-only route (400 for missing body is fine, just not 403)", status != 403)

    # cleanup roles test users
    conn = server.get_conn()
    for uid in (admin_user_id, staff_user_id):
        conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.execute("DELETE FROM admin_allowlist WHERE email=?", (admin_email,))
    conn.commit()
    conn.close()

    # ---- OAuth entry points fail safely without real Google credentials ----
    status, location, body = request("GET", "/api/auth/google/start")
    if server.GOOGLE_CLIENT_ID:
        check("google/start redirects to accounts.google.com when configured", status == 302 and "accounts.google.com" in (location or ""))
    else:
        check("google/start reports missing config instead of crashing (no GOOGLE_CLIENT_ID set)", status == 500)

    status, location, body = request("GET", "/api/auth/google/callback?error=access_denied")
    check("callback with an error param redirects back to login.html with a message", status == 302 and "/login.html?error=" in (location or ""))

    status, location, body = request("GET", "/api/auth/google/callback?code=fake&state=not-a-real-state")
    check("callback with an unrecognised state redirects back to login.html (no crash)", status == 302 and "/login.html?error=" in (location or ""))

    # cleanup
    conn = server.get_conn()
    conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    conn.commit()
    remaining = conn.execute("SELECT COUNT(*) AS c FROM users WHERE email=?", (test_email,)).fetchone()["c"]
    conn.close()
    check("test user cleaned up from the database", remaining == 0)

    print()
    if failures:
        print(len(failures), "FAILED:", failures)
        raise SystemExit(1)
    print("ALL AUTH TESTS PASSED")
    if not server.GOOGLE_CLIENT_ID:
        print("\nNote: GOOGLE_CLIENT_ID isn't set, so the real Google handshake was not exercised.")
        print("Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REDIRECT_URI and test the")
        print("\"Sign in with Google\" button in an actual browser to verify that part.")


if __name__ == "__main__":
    main()
