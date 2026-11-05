"""
Shared pytest setup.

app.main refuses to start without BOT_TOKEN, so a throwaway test token is set
here before any test module imports the app.  Never put a real token here.
"""

import os

os.environ.setdefault("BOT_TOKEN", "test-only-bot-token")
