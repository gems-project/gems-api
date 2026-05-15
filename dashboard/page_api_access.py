from __future__ import annotations

import os
import sys
from pathlib import Path
from textwrap import dedent

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).resolve().parent))

from permissions import has_api_access  # noqa: E402
from gems_api_keys import ApiKeyStore  # noqa: E402
from gems_auth import get_current_user_info, require_authorized_user  # noqa: E402
from gems_ui import page_header, sidebar_user  # noqa: E402

st.set_page_config(page_title="API Access · GEMS", layout="wide", page_icon="🔑")
page_header(
    "API Access",
    "Generate API keys and use version-aware scripts to query or refresh GEMS data.",
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
        "Your account has dashboard access but not API data access. "
        "Contact admin to upgrade."
    )
    st.stop()

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
    "Create an API key for Python, R, curl, or other tools. Keys are shown only once. "
    "Only allowlisted dashboard users can access this page."
)

with st.form("create_api_key"):
    key_name = st.text_input(
        "Key name",
        placeholder="e.g., PN laptop, RStudio script, shared analysis workflow",
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
base_display = api_base_url or "https://YOUR-GEMS-API.azurewebsites.net"
docs_display = f"{base_display}/docs"
st.code(base_display, language="text")
st.markdown("Interactive API documentation:")
st.markdown(f"[Open Swagger UI]({docs_display})")
st.markdown(
    "Every data request must include an `X-API-Key` header. The examples below "
    "read the key from `GEMS_API_KEY` instead of pasting it directly into commands."
)

if allowed_tables:
    with st.expander("Available table names", expanded=False):
        st.write(", ".join(f"`{table}`" for table in allowed_tables))


python_all_tables = dedent(
    f"""
    import json
    import os
    from pathlib import Path

    import requests
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")

    API_KEY = os.environ["GEMS_API_KEY"]
    BASE_URL = "{base_display}"
    DATA_DIR = Path("gems_data")
    DATA_DIR.mkdir(exist_ok=True)

    headers = {{"X-API-Key": API_KEY}}

    def read_metadata(table):
        path = DATA_DIR / f"{{table}}.metadata.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def write_metadata(table, metadata):
        path = DATA_DIR / f"{{table}}.metadata.json"
        path.write_text(json.dumps(metadata, indent=2, default=str))

    def get_json(path):
        response = requests.get(f"{{BASE_URL}}{{path}}", headers=headers, timeout=60)
        response.raise_for_status()
        return response.json()

    tables = get_json("/tables")["tables"]
    updated = 0
    skipped = 0

    print("Checking GEMS tables...")

    for table in tables:
        remote = get_json(f"/version/{{table}}")
        local = read_metadata(table)
        remote_version = remote["version"]
        local_version = None if local is None else local.get("version")

        if local_version == remote_version:
            print(f"{{table}} is already up to date. version={{remote_version}}")
            skipped += 1
            continue

        if local_version is None:
            print(f"{{table}} has no local copy. Downloading version {{remote_version}}...")
        else:
            print(
                f"{{table}} has a newer version. "
                f"local={{local_version}} remote={{remote_version}}. Downloading..."
            )

        response = requests.get(
            f"{{BASE_URL}}/export/{{table}}.csv",
            headers=headers,
            timeout=600,
        )
        response.raise_for_status()

        csv_path = DATA_DIR / f"{{table}}.csv"
        csv_path.write_bytes(response.content)
        write_metadata(table, remote)
        print(f"Saved {{csv_path}}")
        updated += 1

    print(f"Done. Updated {{updated}} table(s); skipped {{skipped}} table(s).")
    """
).strip()

python_query = dedent(
    f"""
    import os
    from pathlib import Path

    import pandas as pd
    import requests
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")

    API_KEY = os.environ.get("GEMS_API_KEY", "")
    if not API_KEY:
        raise RuntimeError("GEMS_API_KEY is not set. Add it to .env or the environment.")

    BASE_URL = "{base_display}"
    TABLE = "goldbodyweight"

    headers = {{"X-API-Key": API_KEY}}

    response = requests.get(
        f"{{BASE_URL}}/preview/{{TABLE}}",
        headers=headers,
        params={{"limit": 100}},
        timeout=60,
    )
    response.raise_for_status()

    payload = response.json()
    df = pd.DataFrame(payload["rows"])
    print(df.head())
    """
).strip()

r_query = dedent(
    f"""
    library(httr2)
    library(jsonlite)

    # Load .env next to this script
    args <- commandArgs(trailingOnly = FALSE)
    file_line <- grep("^--file=", args, value = TRUE)
    env_path <- if (length(file_line)) {{
      script_file <- sub("^--file=", "", file_line)
      file.path(dirname(normalizePath(script_file, winslash = "/")), ".env")
    }} else {{
      file.path(getwd(), ".env")
    }}
    if (file.exists(env_path)) {{
      readRenviron(env_path)
    }}

    api_key <- Sys.getenv("GEMS_API_KEY", unset = "")
    if (!nzchar(api_key)) {{
      stop("GEMS_API_KEY is not set. Add it to .env or the environment.", call. = FALSE)
    }}

    base_url <- "{base_display}"
    table <- "goldbodyweight"

    req <- request(paste0(base_url, "/preview/", table)) |>
      req_headers("X-API-Key" = api_key) |>
      req_url_query(limit = 100) |>
      req_timeout(60)

    resp <- req_perform(req)
    resp_check_status(resp)
    payload <- resp_body_json(resp, simplifyVector = TRUE)

    df <- as.data.frame(payload$rows)
    print(head(df))
    """
).strip()

r_all_tables = dedent(
    f"""
    library(httr2)
    library(jsonlite)

    # Load .env next to this script
    args <- commandArgs(trailingOnly = FALSE)
    file_line <- grep("^--file=", args, value = TRUE)
    env_path <- if (length(file_line)) {{
      script_file <- sub("^--file=", "", file_line)
      file.path(dirname(normalizePath(script_file, winslash = "/")), ".env")
    }} else {{
      file.path(getwd(), ".env")
    }}
    if (file.exists(env_path)) {{
      readRenviron(env_path)
    }}

    api_key <- Sys.getenv("GEMS_API_KEY", unset = "")
    if (!nzchar(api_key)) {{
      stop("GEMS_API_KEY is not set. Add it to .env or the environment.", call. = FALSE)
    }}

    base_url <- "{base_display}"
    data_dir <- "gems_data"
    dir.create(data_dir, showWarnings = FALSE)

    get_json <- function(path) {{
      req <- request(paste0(base_url, path)) |>
        req_headers("X-API-Key" = api_key) |>
        req_timeout(60)
      resp <- req_perform(req)
      resp_check_status(resp)
      resp_body_json(resp)
    }}

    read_metadata <- function(table) {{
      path <- file.path(data_dir, paste0(table, ".metadata.json"))
      if (!file.exists(path)) return(NULL)
      fromJSON(path)
    }}

    write_metadata <- function(table, metadata) {{
      path <- file.path(data_dir, paste0(table, ".metadata.json"))
      write_json(metadata, path, auto_unbox = TRUE, pretty = TRUE)
    }}

    tables <- get_json("/tables")$tables
    updated <- 0
    skipped <- 0

    message("Checking GEMS tables...")

    for (table in tables) {{
      remote <- get_json(paste0("/version/", table))
      local <- read_metadata(table)
      remote_version <- remote$version
      local_version <- if (is.null(local)) NULL else local$version

      if (identical(local_version, remote_version)) {{
        message(table, " is already up to date. version=", remote_version)
        skipped <- skipped + 1
        next
      }}

      if (is.null(local_version)) {{
        message(table, " has no local copy. Downloading version ", remote_version, "...")
      }} else {{
        message(table, " has a newer version. local=", local_version,
                " remote=", remote_version, ". Downloading...")
      }}

      req <- request(paste0(base_url, "/export/", table, ".csv")) |>
        req_headers("X-API-Key" = api_key) |>
        req_timeout(600)
      resp <- req_perform(req)
      resp_check_status(resp)

      csv_path <- file.path(data_dir, paste0(table, ".csv"))
      writeBin(resp_body_raw(resp), csv_path)
      write_metadata(table, remote)
      message("Saved ", csv_path)
      updated <- updated + 1
    }}

    message("Done. Updated ", updated, " table(s); skipped ", skipped, " table(s).")
    """
).strip()

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

with st.expander("API documentation and examples", expanded=False):
    quick, python_tab, r_tab, endpoints_tab, security_tab = st.tabs(
        ["Quick start", "Python", "R", "Endpoints", "Security"]
    )

    with quick:
        st.markdown(
            """
            **Recommended workflow**

            1. Generate an API key above.
            2. Create a `.env` file in the same folder as your Python or R script:
            """
        )
        st.code('GEMS_API_KEY="gems_live_your_real_key_here"', language="text")
        st.markdown(
            """
            3. Use the Python or R **All-table version-aware refresh** script in the next tabs.
               It checks table versions, prints which tables are already current or changed,
               downloads only changed tables, and overwrites local CSV files.

            Run the refresh script whenever you want to check for updates. If a table has a
            newer version, the script tells you and downloads the latest snapshot. If nothing
            changed, it skips the download.
            """
        )
        st.markdown(
            "Opening the base API URL directly may show a short JSON service message. "
            "For interactive browser testing, use Swagger UI:"
        )
        st.code(docs_display, language="text")

    with python_tab:
        st.markdown("All-table version-aware refresh:")
        st.code(python_all_tables, language="python")
        st.markdown("Small preview query:")
        st.code(python_query, language="python")

    with r_tab:
        st.markdown("All-table version-aware refresh:")
        st.code(r_all_tables, language="r")
        st.markdown("Small preview query:")
        st.code(r_query, language="r")

    with endpoints_tab:
        st.dataframe(endpoints, use_container_width=True, hide_index=True)

    with security_tab:
        st.markdown(
            """
            - Store your key in a `.env` file next to your Python/R script as `GEMS_API_KEY="gems_live_..."`.
            - The Python/R examples load `.env` automatically.
            - Do not commit API keys to GitHub, shared notebooks, manuscripts, or email.
            - Revoke a key immediately if it is exposed.
            - Generate separate keys for separate computers or workflows.
            """
        )
        st.code('GEMS_API_KEY="gems_live_..."', language="text")