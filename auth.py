"""
Authentication helpers - pure standard library, no external dependencies.

Sign-in is "Sign in with Google" only (no password), restricted to email
addresses ending in one of ALLOWED_DOMAINS. Google verifies the person's
identity; this module only checks the resulting email's domain and manages
session tokens - the actual OAuth HTTP calls live in server.py (they need
urllib + the client id/secret, which are deployment config, not auth logic).
"""
import secrets

ALLOWED_DOMAINS = ["kenzieclean.ae", "legacygroup.me", "handyman.ae"]


def is_allowed_email(email):
    email = (email or "").strip().lower()
    if "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1]
    return domain in ALLOWED_DOMAINS


def normalize_email(email):
    return (email or "").strip().lower()


def generate_session_token():
    return secrets.token_urlsafe(32)


def generate_state_token():
    """Random value sent to Google and checked on the way back, so a
    forged callback request can't be used to log someone into an account
    they didn't initiate (CSRF protection on the OAuth redirect)."""
    return secrets.token_urlsafe(24)
