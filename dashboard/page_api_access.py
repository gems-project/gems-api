# -*- coding: utf-8 -*-
"""API Access: generate keys and copy Python/R examples (key + Auth0)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from textwrap import dedent

import pandas as pd
import requests
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent))

from gems_api_keys import ApiKeyStore  # noqa: E402
from gems_auth import get_current_user_info, require_authorized_user  # noqa: E402
from gems_ui import page_header, sidebar_user  # noqa: E402
from permissions import has_api_access  # noqa: E402

st.set_page_config(page_title="API Access", layout="wide", page_icon="key")
page_header(
    "API Access",
    "Generate API keys and download data with Auth0 login + your personal key.",
)

user = require_authorized_user()
sidebar_user(user)
user_info = get_current_user_info()

api_base_url = os.environ.get("GEMS_API_BASE_URL", "").strip().rstrip("/")
allowed_tables = [
    item.strip()
    for item in os.environ.get("ALLOWED_TABLES", "").split(",")
    if item.strip()
]

if not api_base_url:
    st.warning(
        "`GEMS_API_BASE_URL` is not set. Add the FastAPI App Service URL to dashboard "
        "environment variables so users see working examples."
    )

if not has_api_access(user_info, user_info.bearer_token):
    st.warning(
        f"Your account ({user_info.email}) has dashboard access but is not in the API tier. "
        "To request API key access, contact the GEMS team. The administrator must add "
        "your email to ALLOWED_API_USERS on gems-api."
    )
    st.stop()

# Soft check — do not lock users out if dashboard token audience differs from API.
if api_base_url and user_info.bearer_token:
    try:
        response = requests.get(
            f"{api_base_url}/authz/me",
            headers={"Authorization": f"Bearer {user_info.bearer_token}"},
            timeout=15,
        )
        if response.ok and not response.json().get("allowed_api", True):
            st.info(
                "The API reports your account may not be on ALLOWED_API_USERS yet. "
                "Ask an admin to confirm that list if key use fails."
            )
    except Exception:
        pass

store = ApiKeyStore()
if not store.enabled:
    st.error(
        "API key storage is not configured. Set `AZURE_TABLES_CONNECTION_STRING`, "
        "`AZURE_API_KEYS_TABLE`, and `API_KEY_PEPPER` in the dashboard App Service."
    )
    if store.error:
        st.caption(f"Storage error: {store.error}")
    st.stop()

st.markdown(
    "Create an API key, put it in `.env`, then copy a Python example. "
    "When Auth0 opens, sign in with the **same account** you use on this dashboard."
)

with st.form("create_api_key"):
    key_name = st.text_input(
        "Key name",
        placeholder="e.g., laptop, RStudio script, analysis workflow",
    )
    submitted = st.form_submit_button("Generate API key", type="primary")

if submitted:
    try:
        raw_key, _ = store.create_key(user, key_name or "API key")
        st.success("API key created. Copy it now; it will not be shown again.")
        st.code(raw_key, language="text")
    except Exception as exc:
        st.error(f"Could not create API key: {exc}")

st.markdown("### Your API keys")
keys = store.list_keys(user)
if keys:
    display = pd.DataFrame(
        [
            {
                "Name": row["name"],
                "Prefix": row["prefix"],
                "Created": row["created_at"],
                "Last used": row["last_used_at"],
                "Status": row["status"],
            }
            for row in keys
        ]
    )
    st.dataframe(display, use_container_width=True, hide_index=True)

    active_keys = [row for row in keys if row["status"] == "Active"]
    if active_keys:
        st.markdown("#### Revoke a key")
        revoke_options = {
            f"{row['name']} ({row['prefix']})": row["id"] for row in active_keys
        }
        selected = st.selectbox("Active key", list(revoke_options.keys()))
        if st.button("Revoke selected key"):
            if store.revoke_key(revoke_options[selected], user):
                st.success("API key revoked.")
                st.rerun()
            else:
                st.error("Could not revoke API key.")
else:
    st.info("No API keys yet.")

st.markdown("### API connection details")
base_display = api_base_url or "https://gems-api.bovi-analytics.org"
docs_display = f"{base_display}/docs"

st.code(base_display, language="text")
st.markdown(
    f"[Open Swagger UI]({docs_display}) — **Authorize**: **Log in**, then paste your **API key**."
)

if allowed_tables:
    with st.expander("Available table names", expanded=False):
        st.write(", ".join(f"`{table}`" for table in allowed_tables))

env_example = 'GEMS_API_KEY="gems_live_your_real_key_here"'

# Shared Auth0+key helper embedded in every Python example (one file to copy).
_python_auth_helper = dedent(
    f"""
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
    BASE_URL = "{base_display}"
    load_dotenv(Path(__file__).resolve().parent / ".env")
    API_KEY = os.environ.get("GEMS_API_KEY", "").strip()
    if not API_KEY:
        raise SystemExit("Add GEMS_API_KEY to a .env file next to this script.")


    def gems_headers():
        # Open Auth0 in the browser, then return X-API-Key + Bearer headers.
        cfg = requests.get(f"{{BASE_URL}}/auth/client-config", timeout=30).json()
        domain = cfg["domain"].rstrip("/")
        client_id = cfg["client_id"]
        audience = (cfg.get("audience") or "").strip()
        scopes = cfg.get("scopes") or "openid profile email"
        port = 8765
        redirect_uri = f"http://127.0.0.1:{{port}}/callback"

        def b64url(raw: bytes) -> str:
            return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

        verifier = b64url(secrets.token_bytes(32))
        challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = b64url(secrets.token_bytes(16))
        result, error = {{}}, {{}}

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
        params = {{
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }}
        if audience:
            params["audience"] = audience
        url = f"https://{{domain}}/authorize?{{urllib.parse.urlencode(params)}}"
        print("Opening browser for Auth0 login…")
        print(
            "If Auth0 shows Callback URL mismatch, add this exact URL to "
            "Auth0 → Applications → GEMS API Clients → Allowed Callback URLs:"
        )
        print(f"  {{redirect_uri}}")
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
            f"https://{{domain}}/oauth/token",
            data={{
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": result["code"],
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            }},
            timeout=30,
        )
        token.raise_for_status()
        access_token = token.json().get("access_token") or ""
        if not access_token:
            raise RuntimeError("Auth0 did not return an access token.")
        return {{"X-API-Key": API_KEY, "Authorization": f"Bearer {{access_token}}"}}
    """
).strip()

python_all_tables = (
    _python_auth_helper
    + "\n\n"
    + dedent(
        """
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
        """
    ).strip()
)

python_query = (
    _python_auth_helper
    + "\n\n"
    + dedent(
        """
        import pandas as pd

        TABLE = "goldbodyweight"
        headers = gems_headers()
        response = requests.get(
            f"{BASE_URL}/preview/{TABLE}",
            headers=headers,
            params={"limit": 100},
            timeout=60,
        )
        response.raise_for_status()
        df = pd.DataFrame(response.json()["rows"])
        print(df.head())
        """
    ).strip()
)

# Shared Auth0+key helper embedded in every R example (one file to copy).
# Packages: httr2, httpuv, jsonlite, openssl
_r_auth_helper = dedent(
    f"""
    # install.packages(c("httr2", "httpuv", "jsonlite", "openssl"))
    library(httr2)
    library(httpuv)
    library(jsonlite)
    library(openssl)

    BASE_URL <- "{base_display}"

    # Find .env next to this script (works with source() and Rscript).
    .env_path <- local({{
      for (i in seq_len(sys.nframe())) {{
        ofile <- sys.frame(i)$ofile
        if (!is.null(ofile) && nzchar(ofile)) {{
          return(file.path(dirname(normalizePath(ofile, winslash = "/", mustWork = FALSE)), ".env"))
        }}
      }}
      args <- commandArgs(trailingOnly = FALSE)
      file_line <- grep("^--file=", args, value = TRUE)
      if (length(file_line)) {{
        script_file <- sub("^--file=", "", file_line)
        return(file.path(dirname(normalizePath(script_file, winslash = "/", mustWork = FALSE)), ".env"))
      }}
      file.path(getwd(), ".env")
    }})

    load_gems_env <- function(path) {{
      if (!file.exists(path)) return(invisible(FALSE))
      lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
      for (line in lines) {{
        line <- sub("^\\\\s+", "", sub("\\\\s+$", "", line))
        if (!nzchar(line) || startsWith(line, "#")) next
        if (!grepl("=", line, fixed = TRUE)) next
        key <- sub("^\\\\s+", "", sub("\\\\s+$", "", sub("=.*$", "", line)))
        val <- sub("^\\\\s+", "", sub("\\\\s+$", "", sub("^[^=]*=", "", line)))
        if (startsWith(val, "\\"") && endsWith(val, "\\"")) {{
          val <- substr(val, 2, nchar(val) - 1)
        }} else if (startsWith(val, "'") && endsWith(val, "'")) {{
          val <- substr(val, 2, nchar(val) - 1)
        }}
        if (nzchar(key)) do.call(Sys.setenv, setNames(list(val), key))
      }}
      invisible(TRUE)
    }}

    load_gems_env(.env_path)
    API_KEY <- Sys.getenv("GEMS_API_KEY", unset = "")
    if (!nzchar(API_KEY)) {{
      stop(
        "Add GEMS_API_KEY to a .env file next to this script.\\n",
        "Looked for: ", .env_path,
        call. = FALSE
      )
    }}

    b64url <- function(raw) {{
      x <- openssl::base64_encode(raw)
      x <- gsub("+", "-", x, fixed = TRUE)
      x <- gsub("/", "_", x, fixed = TRUE)
      gsub("=+$", "", x)
    }}

    parse_qs <- function(q) {{
      if (is.null(q) || !nzchar(q)) return(list())
      q <- sub("^\\\\?", "", q)
      parts <- strsplit(q, "&", fixed = TRUE)[[1]]
      out <- list()
      for (p in parts) {{
        kv <- strsplit(p, "=", fixed = TRUE)[[1]]
        key <- utils::URLdecode(kv[[1]])
        val <- if (length(kv) > 1) utils::URLdecode(paste(kv[-1], collapse = "=")) else ""
        out[[key]] <- val
      }}
      out
    }}

    gems_headers <- function() {{
      cfg <- resp_body_json(
        request(paste0(BASE_URL, "/auth/client-config")) |> req_timeout(30) |> req_perform()
      )
      domain <- sub("/+$", "", cfg$domain)
      client_id <- cfg$client_id
      audience <- if (!is.null(cfg$audience)) cfg$audience else ""
      scopes <- if (!is.null(cfg$scopes) && nzchar(cfg$scopes)) cfg$scopes else "openid profile email"
      port <- 8765L
      redirect_uri <- sprintf("http://127.0.0.1:%s/callback", port)

      verifier <- b64url(openssl::rand_bytes(32))
      challenge <- b64url(openssl::sha256(charToRaw(verifier)))
      state <- b64url(openssl::rand_bytes(16))
      ready <- new.env(parent = emptyenv())
      ready$code <- NULL
      ready$error <- NULL

      srv <- httpuv::startServer("127.0.0.1", port, list(
        call = function(req) {{
          qs <- parse_qs(req$QUERY_STRING)
          if (!identical(qs$state, state)) {{
            ready$error <- "state mismatch"
            status <- 400L
          }} else if (!is.null(qs$error)) {{
            ready$error <- if (!is.null(qs$error_description)) qs$error_description else qs$error
            status <- 400L
          }} else {{
            ready$code <- qs$code
            status <- 200L
          }}
          list(
            status = status,
            headers = list("Content-Type" = "text/plain; charset=utf-8"),
            body = "GEMS login done. You can close this tab."
          )
        }}
      ))
      on.exit(try(httpuv::stopServer(srv), silent = TRUE), add = TRUE)

      params <- list(
        response_type = "code",
        client_id = client_id,
        redirect_uri = redirect_uri,
        scope = scopes,
        state = state,
        code_challenge = challenge,
        code_challenge_method = "S256"
      )
      if (nzchar(audience)) params$audience <- audience
      auth_url <- paste0(
        "https://", domain, "/authorize?",
        paste(sprintf("%s=%s", names(params), vapply(params, utils::URLencode, "", reserved = TRUE)), collapse = "&")
      )
      message("Opening browser for Auth0 login…")
      message(
        "If Auth0 shows Callback URL mismatch, add this exact URL to ",
        "Auth0 → Applications → GEMS API Clients → Allowed Callback URLs:"
      )
      message("  ", redirect_uri)
      utils::browseURL(auth_url)

      deadline <- Sys.time() + 300
      while (is.null(ready$code) && is.null(ready$error) && Sys.time() < deadline) {{
        httpuv::service(200)
      }}
      if (!is.null(ready$error)) stop(ready$error, call. = FALSE)
      if (is.null(ready$code) || !nzchar(ready$code)) stop("Auth0 login timed out.", call. = FALSE)

      token <- resp_body_json(
        request(paste0("https://", domain, "/oauth/token")) |>
          req_body_form(
            grant_type = "authorization_code",
            client_id = client_id,
            code = ready$code,
            redirect_uri = redirect_uri,
            code_verifier = verifier
          ) |>
          req_timeout(30) |>
          req_perform()
      )
      access_token <- token$access_token
      if (is.null(access_token) || !nzchar(access_token)) {{
        stop("Auth0 did not return an access token.", call. = FALSE)
      }}
      c("X-API-Key" = API_KEY, Authorization = paste("Bearer", access_token))
    }}
    """
).strip()

r_query = (
    _r_auth_helper
    + "\n\n"
    + dedent(
        """
        TABLE <- "goldbodyweight"
        headers <- gems_headers()
        resp <- request(paste0(BASE_URL, "/preview/", TABLE)) |>
          req_headers(!!!as.list(headers)) |>
          req_url_query(limit = 100) |>
          req_timeout(60) |>
          req_perform()
        payload <- resp_body_json(resp, simplifyVector = TRUE)
        print(utils::head(as.data.frame(payload$rows)))
        """
    ).strip()
)

r_all_tables = (
    _r_auth_helper
    + "\n\n"
    + dedent(
        """
        data_dir <- "gems_data"
        dir.create(data_dir, showWarnings = FALSE)
        headers <- gems_headers()

        get_json <- function(path) {
          resp <- request(paste0(BASE_URL, path)) |>
            req_headers(!!!as.list(headers)) |>
            req_timeout(60) |>
            req_perform()
          resp_body_json(resp)
        }

        read_metadata <- function(table) {
          path <- file.path(data_dir, paste0(table, ".metadata.json"))
          if (!file.exists(path)) return(NULL)
          jsonlite::fromJSON(path)
        }

        write_metadata <- function(table, metadata) {
          path <- file.path(data_dir, paste0(table, ".metadata.json"))
          jsonlite::write_json(metadata, path, auto_unbox = TRUE, pretty = TRUE)
        }

        tables <- unlist(get_json("/tables")$tables)
        updated <- 0
        skipped <- 0
        message("Checking GEMS tables...")

        for (table in tables) {
          remote <- get_json(paste0("/version/", table))
          local <- read_metadata(table)
          remote_version <- remote$version
          local_version <- if (is.null(local)) NULL else local$version

          if (identical(local_version, remote_version)) {
            message(table, " is already up to date. version=", remote_version)
            skipped <- skipped + 1
            next
          }

          if (is.null(local_version)) {
            message(table, " has no local copy. Downloading version ", remote_version, "...")
          } else {
            message(table, " has a newer version. local=", local_version,
                    " remote=", remote_version, ". Downloading...")
          }

          resp <- request(paste0(BASE_URL, "/export/", table, ".csv")) |>
            req_headers(!!!as.list(headers)) |>
            req_timeout(600) |>
            req_perform()
          csv_path <- file.path(data_dir, paste0(table, ".csv"))
          writeBin(resp_body_raw(resp), csv_path)
          write_metadata(table, remote)
          message("Saved ", csv_path)
          updated <- updated + 1
        }

        message("Done. Updated ", updated, " table(s); skipped ", skipped, " table(s).")
        """
    ).strip()
)


endpoints = pd.DataFrame(
    [
        {"Purpose": "List tables", "Method": "GET", "Endpoint": "/tables"},
        {"Purpose": "Check one table version", "Method": "GET", "Endpoint": "/version/{table}"},
        {"Purpose": "Check all table versions", "Method": "GET", "Endpoint": "/versions"},
        {"Purpose": "Get schema", "Method": "GET", "Endpoint": "/schema/{table}"},
        {"Purpose": "Preview rows", "Method": "GET", "Endpoint": "/preview/{table}?limit=100"},
        {"Purpose": "Download CSV", "Method": "GET", "Endpoint": "/export/{table}.csv"},
        {"Purpose": "Read-only SQL query", "Method": "POST", "Endpoint": "/query"},
    ]
)

with st.expander("API documentation and examples", expanded=True):
    quick, python_tab, r_tab, endpoints_tab, tips_tab = st.tabs(
        ["Quick start", "Python", "R", "Endpoints", "Tips"]
    )

    with quick:
        st.markdown(
            """
            1. **Generate an API key** above and copy it right away (it is shown only once).
            2. Create a `.env` file next to your script with only:
            """
        )
        st.code(env_example, language="text")
        st.markdown(
            f"""
            3. Copy a script from the **Python** or **R** tab into your own file and run it.
            4. A browser window opens for Auth0 — sign in with the **same account** you use on this dashboard.
            5. Downloaded tables (full refresh example) are saved under `gems_data/`.

            **Optional check in the browser:** open [Swagger]({docs_display}) → **Authorize** →
            **Log in** → paste **API key** → **GET /tables** → **Execute**.
            """
        )

    with python_tab:
        st.markdown(
            "Copy one script below into your own `.py` file. Only `.env` needs your key. "
            "When the browser opens, sign in with the same account you use on this dashboard."
        )
        st.markdown("`.env`:")
        st.code(env_example, language="text")
        st.markdown("Small preview (good first test):")
        st.code(python_query, language="python")
        st.markdown("Refresh / download all tables:")
        st.code(python_all_tables, language="python")

    with r_tab:
        st.markdown(
            "Copy one script below into your own `.R` file. Only `.env` needs your key. "
            "Packages: `httr2`, `httpuv`, `jsonlite`, `openssl`. "
            "When the browser opens, sign in with the same account you use on this dashboard."
        )
        st.markdown("`.env`:")
        st.code(env_example, language="text")
        st.markdown("Small preview (good first test):")
        st.code(r_query, language="r")
        st.markdown("Refresh / download all tables:")
        st.code(r_all_tables, language="r")

    with endpoints_tab:
        st.dataframe(endpoints, use_container_width=True, hide_index=True)

    with tips_tab:
        st.markdown(
            f"""
            - Sign in to Auth0 with the **same account** you use on this dashboard. The API checks that it matches the key owner.
            - `.env` only needs `GEMS_API_KEY`. The API URL and Auth0 settings are in the copied script.
            - Do not commit your key or share it by email.
            - Revoke a key on this page if a computer is lost or a key leaks.
            - Swagger, Python, and R examples all send your API key and Auth0 login.
              The API checks that the Auth0 email matches the email that owns the key.
            """
        )
