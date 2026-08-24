"""
Twitch OAuth token generator for Arc.
Uses ONLY real Twitch scopes - verified against
https://dev.twitch.tv/docs/authentication/scopes/

Before running:
  1. Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env (never hardcode them here)
  2. Make sure http://localhost:3000 is in your Twitch app's OAuth Redirect URLs
  3. Log into twitch.tv in your browser as the account you want the token FOR
"""

import http.server
import socketserver
import urllib.parse
import webbrowser
import json
import os
import sys
from pathlib import Path
import threading
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Credentials are read from the environment (.env), never stored in this file:
# this script is committed, .env is not. See _require_credentials() below.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).with_name('.env'), override=False)
except Exception:  # python-dotenv optional; plain env vars still work
    pass

CLIENT_ID = os.getenv("TWITCH_CLIENT_ID", "").strip()
CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET", "").strip()

REDIRECT_URI = "http://localhost:3000"
PORT = 3000

# Real Twitch scopes. Verified against official docs.
# channel.raid EventSub does NOT require a scope - raids just work.
SCOPES = [
    "chat:read",
    "chat:edit",
    "channel:read:subscriptions",
    "channel:read:redemptions",
    "channel:read:ads",
    "channel:manage:raids",
    "bits:read",
    "moderator:read:followers",
    "user:read:chat",
    "user:write:chat",
    "channel:bot",
    "user:bot",
]

def auth_url() -> str:
    """Authorize URL for the configured client. Call only after
    _require_credentials() so an unset client id can't build a broken URL."""
    return (
        "https://id.twitch.tv/oauth2/authorize"
        f"?client_id={urllib.parse.quote(CLIENT_ID, safe='')}"
        f"&redirect_uri={urllib.parse.quote(REDIRECT_URI, safe='')}"
        "&response_type=code"
        f"&scope={urllib.parse.quote(' '.join(SCOPES))}"
        "&force_verify=true"
    )


def _require_credentials() -> None:
    """Fail fast, and never echo the values themselves."""
    missing = [name for name, value in
               (("TWITCH_CLIENT_ID", CLIENT_ID), ("TWITCH_CLIENT_SECRET", CLIENT_SECRET))
               if not value]
    if missing:
        print("ERROR: missing required environment variable(s): " + ", ".join(missing))
        print("Set them in .env (which is gitignored) or in your shell, then re-run:")
        print("  TWITCH_CLIENT_ID=<your app's client id>")
        print("  TWITCH_CLIENT_SECRET=<your app's client secret>")
        print("Find both at https://dev.twitch.tv/console/apps")
        sys.exit(1)

captured_code = {"value": None}


class CallbackHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args, **kwargs):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            captured_code["value"] = params["code"][0]
            html = b"<html><body style='font-family:sans-serif;background:#0e0e0e;color:#ddd;padding:40px'><h1>OK - got it</h1><p>Close this tab and go back to PowerShell.</p></body></html>"
        elif "error" in params:
            captured_code["value"] = f"__ERROR__:{params.get('error_description', ['unknown'])[0]}"
            html = b"<html><body><h1>Error</h1><p>Check PowerShell.</p></body></html>"
        else:
            html = b"<html><body><h1>Waiting...</h1></body></html>"

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


def exchange_code_for_tokens(code):
    url = "https://id.twitch.tv/oauth2/token"
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    }).encode("utf-8")

    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"\n[ERROR] Twitch rejected token exchange (HTTP {e.code}):")
        print(body)
        return None


def validate_token(access_token):
    req = Request("https://id.twitch.tv/oauth2/validate")
    req.add_header("Authorization", f"OAuth {access_token}")
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError:
        return None


def main():
    _require_credentials()

    print("=" * 60)
    print("Twitch OAuth Token Generator")
    print("=" * 60)
    print()
    print("Make sure you are logged into twitch.tv in your browser")
    print("as the account you want this token FOR.")
    print()
    print("Scopes being requested:")
    for s in SCOPES:
        print(f"  - {s}")
    print()
    input("Press Enter when ready...")
    print()

    httpd = socketserver.TCPServer(("localhost", PORT), CallbackHandler)
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    print(f"[OK] Listening on http://localhost:{PORT}")

    print("[OK] Opening browser...")
    print()
    print("If the browser doesn't open, paste this URL:")
    print(auth_url())
    print()
    webbrowser.open(auth_url())

    print("Waiting for authorization...")
    start = time.time()
    while captured_code["value"] is None:
        if time.time() - start > 300:
            print("\n[TIMEOUT]")
            httpd.shutdown()
            sys.exit(1)
        time.sleep(0.5)

    httpd.shutdown()

    code = captured_code["value"]
    if code.startswith("__ERROR__"):
        print(f"\n[ERROR] {code}")
        sys.exit(1)

    print("\n[OK] Got authorization code")
    print("[...] Exchanging for tokens...")

    tokens = exchange_code_for_tokens(code)
    if not tokens:
        sys.exit(1)

    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", "unknown")

    info = validate_token(access_token)
    who = info.get("login", "unknown") if info else "unknown"
    scopes = info.get("scopes", []) if info else []

    print()
    print("=" * 60)
    print("SUCCESS")
    print("=" * 60)
    print(f"Account:     {who}")
    print(f"Expires in:  {expires_in} seconds (~{int(expires_in)//3600} hours)")
    print(f"Scopes ({len(scopes)}):")
    for s in scopes:
        print(f"  - {s}")
    print()
    print("ACCESS TOKEN:")
    print(access_token)
    print()
    print("REFRESH TOKEN:")
    print(refresh_token)
    print()

    outfile = f"twitch_tokens_{who}.txt"
    with open(outfile, "w") as f:
        f.write(f"# Tokens for {who}, generated {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"ACCESS_TOKEN={access_token}\n")
        f.write(f"REFRESH_TOKEN={refresh_token}\n")

    print(f"[OK] Saved to {outfile}")


if __name__ == "__main__":
    main()
