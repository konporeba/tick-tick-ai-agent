"""
One-time script to obtain a TickTick OAuth2 refresh token.

Steps:
  1. Set TICKTICK_CLIENT_ID and TICKTICK_CLIENT_SECRET in your .env file.
  2. In your TickTick developer app settings, add this redirect URL:
       http://localhost:8080/callback
  3. Run:  python get_refresh_token.py
  4. A browser window will open. Log in and approve access.
  5. The refresh token will be printed and written to your .env file automatically.
"""

import http.server
import threading
import urllib.parse
import webbrowser
import base64
import os
import re
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["TICKTICK_CLIENT_ID"]
CLIENT_SECRET = os.environ["TICKTICK_CLIENT_SECRET"]
REDIRECT_URI = "http://localhost:8080/callback"
AUTH_URL = "https://ticktick.com/oauth/authorize"
TOKEN_URL = "https://ticktick.com/oauth/token"
SCOPE = "tasks:read tasks:write"
PORT = 8080

auth_code: str | None = None


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        global auth_code
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "code" in params:
            auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authorization successful! You can close this tab.</h2>")
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h2>Error: {error}</h2>".encode())

    def log_message(self, format, *args):
        pass  # suppress access log noise


def exchange_code_for_tokens(code: str) -> dict:
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Authorization": f"Basic {credentials}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def write_refresh_token_to_env(token: str) -> None:
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "r") as f:
        content = f.read()
    updated = re.sub(
        r"^TICKTICK_ACCESS_TOKEN=.*$",
        f"TICKTICK_ACCESS_TOKEN={token}",
        content,
        flags=re.MULTILINE,
    )
    with open(env_path, "w") as f:
        f.write(updated)
    print(f"  .env updated with the new refresh token.")


def main():
    # Build authorization URL
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
    })
    url = f"{AUTH_URL}?{params}"

    # Start local callback server in background
    server = http.server.HTTPServer(("localhost", PORT), CallbackHandler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()

    print(f"\nOpening browser for TickTick authorization...")
    print(f"If the browser doesn't open, visit:\n  {url}\n")
    webbrowser.open(url)

    thread.join(timeout=120)

    if not auth_code:
        print("ERROR: No authorization code received within 120 seconds.")
        return

    print("Authorization code received. Exchanging for tokens...")
    tokens = exchange_code_for_tokens(auth_code)

    access_token = tokens.get("access_token")
    if not access_token:
        print("ERROR: No access_token in response. Full response:")
        print(tokens)
        return

    print(f"\nAccess token:\n  {access_token}\n")
    write_refresh_token_to_env(access_token)
    print("Done. You can now run the agent with:  python -m src.main")


if __name__ == "__main__":
    main()
