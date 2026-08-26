"""
Entry point for Passenger (SiteGround's shared/GrowBig "Python App" tool).

Passenger imports this exact file and looks for a module-level `application`
callable - it does not run `python server.py` or call any main()/serve_forever(),
it owns the web server itself and calls `application(environ, start_response)`
per request. All the actual routing/business logic lives in server.py; this
file only has to exist, under this exact name, at the app's configured root.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from server import application, init_db

# Passenger never calls main(), so the one-time database setup that main()
# would normally trigger has to happen here instead, at import time.
init_db()
