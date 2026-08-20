import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests
from dotenv import load_dotenv

# API URL is built into this script — only put your key in .env
BASE_URL = "https://gems-api.bovi-analytics.org"
load_dotenv(Path(__file__).resolve().parent / ".env")
API_KEY = os.environ.get("GEMS_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("Add GEMS_API_KEY to a .env file next to this script.")


def gems_headers():
    # Open Auth0 in the browser, then return X-API-Key + Bearer headers.
    cfg = requests.get(f"{BASE_URL}/auth/client-config", timeout=30).json()
    domain = cfg["domain"].rstrip("/")
    client_id = cfg["client_id"]
    audience = (cfg.get("audience") or "").strip()
    scopes = cfg.get("scopes") or "openid profile email"
    port = 8765
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    def b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    verifier = b64url(secrets.token_bytes(32))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = b64url(secrets.token_bytes(16))
    result, error = {}, {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if qs.get("state", [""])[0] != state:
                error["m"] = "state mismatch"
                self.send_response(400)
            elif "error" in qs:
                error["m"] = qs.get("error_description", qs.get("error", ["error"]))[0]
                self.send_response(400)
            else:
                result["code"] = qs.get("code", [""])[0]
                self.send_response(200)
            body = b"GEMS login done. You can close this tab."
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if audience:
        params["audience"] = audience
    url = f"https://{domain}/authorize?{urllib.parse.urlencode(params)}"
    print("Opening browser for Auth0 login…")
    webbrowser.open(url)
    deadline = time.time() + 300
    while time.time() < deadline and not result and not error:
        time.sleep(0.2)
    server.server_close()
    if error:
        raise RuntimeError(error["m"])
    if not result.get("code"):
        raise RuntimeError("Auth0 login timed out.")
    token = requests.post(
        f"https://{domain}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": result["code"],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    token.raise_for_status()
    access_token = token.json().get("access_token") or ""
    if not access_token:
        raise RuntimeError("Auth0 did not return an access token.")
    return {"X-API-Key": API_KEY, "Authorization": f"Bearer {access_token}"}

import json
from pathlib import Path

DATA_DIR = Path("gems_data")
DATA_DIR.mkdir(exist_ok=True)
headers = gems_headers()

def read_metadata(table):
    path = DATA_DIR / f"{table}.metadata.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())

def write_metadata(table, metadata):
    path = DATA_DIR / f"{table}.metadata.json"
    path.write_text(json.dumps(metadata, indent=2, default=str))

def get_json(path):
    response = requests.get(f"{BASE_URL}{path}", headers=headers, timeout=60)
    response.raise_for_status()
    return response.json()

tables = get_json("/tables")["tables"]
updated = 0
skipped = 0
print("Checking GEMS tables...")

for table in tables:
    remote = get_json(f"/version/{table}")
    local = read_metadata(table)
    remote_version = remote["version"]
    local_version = None if local is None else local.get("version")

    if local_version == remote_version:
        print(f"{table} is already up to date. version={remote_version}")
        skipped += 1
        continue

    if local_version is None:
        print(f"{table} has no local copy. Downloading version {remote_version}...")
    else:
        print(
            f"{table} has a newer version. "
            f"local={local_version} remote={remote_version}. Downloading..."
        )

    response = requests.get(
        f"{BASE_URL}/export/{table}.csv",
        headers=headers,
        timeout=600,
    )
    response.raise_for_status()
    csv_path = DATA_DIR / f"{table}.csv"
    csv_path.write_bytes(response.content)
    write_metadata(table, remote)
    print(f"Saved {csv_path}")
    updated += 1

print(f"Done. Updated {updated} table(s); skipped {skipped} table(s).")