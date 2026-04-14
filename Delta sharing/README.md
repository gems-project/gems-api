# GEMS Delta Sharing — download shared tables (Python & R)

This folder contains two scripts that connect to a **Databricks Delta Sharing** share using a small credential file you get from us. They list every table you are allowed to see, download the data, and save files you can open in Excel or a browser.

| File | Language | What it does |
|------|----------|----------------|
| `load_shared_table.py` | Python | Installs missing packages if needed, exports **Excel (`.xlsx`)** and **HTML (`.html`)** per table |
| `load_shared_table.R` | R | Installs missing CRAN packages if needed, exports **Excel (`.xlsx`)** per table (Parquet-backed shares only) |

---

## 1. What you get from us

We send you the credential file itself — **`config.share`** — through a **private** channel (for example encrypted email or a secure file transfer). Save it **as delivered**; you do not need to download anything from a separate activation page.

(You can rename it to **`config.json`** if you prefer — both names work.)

**Important:** Put `config.share` (or `config.json`) in the **same folder** as `load_shared_table.py` and `load_shared_table.R` — the folder from which you run the script (see below).

---

## 2. You only need **one** `config.share` file

The config file is **not a copy of the data**. In practice it bundles **three things**:

- your **access token** (secret),
- the server **endpoint** (where to connect),
- and which **share(s)** you are allowed to use.

You can think of it as:

**`config.share` ≈ token + endpoint + access**

With a valid file, the sharing client can **connect to the share**, **list tables** you are permitted to see, and **read or download** the table data. These scripts **do not** open a browser login or ask for a username and password — there is **no separate identity check** beyond the token embedded in the file.

Think of it like this:

- **`config.share`** = key to the door  
- **share** = shared “database” we maintain  
- **tables** = the actual datasets inside that share  

When we **add new tables**, **update schemas**, or **append rows** to tables in the **same** share, you **do not** need a new file. The next time you run the script, it asks the server what tables exist **right now** and downloads the current data.

You need a **new** `config.share` only in situations such as:

- the **token expired** (we can set long lifetimes to reduce this),
- we created a **new recipient** for you and issued **new** credentials,
- we **rotated** your token for security,
- or you were given access to a **different share** that requires a **different** profile.

**Tip for our team:** keep one stable share (e.g. one share name) and add or update **tables** inside it, instead of creating a new share or new recipient for every change — that avoids forcing everyone to request new config files.

---

## 3. How to run (after the config is in this folder)

You can run the scripts in either of these ways:

1. **Terminal** — Open a terminal (PowerShell, Command Prompt, Terminal on Mac/Linux), **change directory** to this folder (the one that contains the scripts and `config.share`), then run **one** of the commands below.

2. **Code editor or IDE** — Open this folder in a **code editor** or **integrated development environment (IDE)** and run the script from there. Examples include **Visual Studio Code**, **Cursor**, **RStudio**, **PyCharm**, or **Jupyter**. In most of these tools you open the script file, set the working directory to this folder (or open the folder as the project root), then use **Run** / **Run Python File** / **Source** / **Run Cell** as your tool provides. The important part is the same: **`config.share` must live next to the script**, and the process’s **working directory** should be that folder when the script runs.

Then run **one** of the following (from a terminal, or from the equivalent “run” action in your editor).

### Python

```bash
python load_shared_table.py
```

On some computers the command is `python3` instead of `python`.

**First run:** the script may install packages automatically (`pandas`, `pyarrow`, `openpyxl`, `delta-sharing`). That can take a minute or two and needs internet access.

### R

```bash
Rscript load_shared_table.R
```

**First run:** R may install packages from CRAN (`jsonlite`, `httr2`, `arrow`, `writexl`). Again, internet is required.

**From inside R (e.g. RStudio):** the auto-run at the bottom of the script is tied to `Rscript`. After sourcing, call the loader yourself:

```r
setwd("/path/to/this/folder")   # folder that contains the scripts + config.share
source("load_shared_table.R")
tabs <- load_gems_tables()
```

---

## 4. Getting **updated** data

Nothing special: **run the same command again** (same `config.share` in the same place). The scripts always fetch the **current** list of tables and the **current** data the server exposes.

---

## 5. Where files are written

Both scripts write under a subfolder next to the scripts:

**`shared_table_exports/`**

- **Python:** for each table, one **`.xlsx`** and one **`.html`** (open the HTML in any web browser).
- **R:** one **`.xlsx`** per table (no HTML in the R script).

File names look like: `shareName__schemaName__tableName.xlsx` (special characters are sanitized).

The scripts also remove a few **internal pipeline columns** from the exports (for example lineage fields such as `sequence`, `workbookFile`, `workbookPath`, `gateRunId`, `ingestRunId`) so the files are easier to use for analysis.

---

## 6. Optional: use the scripts as a library (examples)

### Python (in a notebook or another script)

You can run the file as-is, or reuse the same idea with the official client:

```python
from pathlib import Path
from delta_sharing import SharingClient, load_as_pandas

profile = str(Path("config.share").resolve())  # or config.json
client = SharingClient(profile)
tables = list(client.list_all_tables())
for t in tables:
    url = f"{profile}#{t.share}.{t.schema}.{t.name}"
    df = load_as_pandas(url)
    print(t.name, df.shape)
```

(If you use this pattern, install the same packages as in `load_shared_table.py`.)

### R (after `source("load_shared_table.R")`)

```r
# All tables as a named list of data.frames; also writes .xlsx under shared_table_exports/
all <- load_gems_tables()

# First table only
one <- load_gems_table()

# One table by export stem name (see filenames in shared_table_exports)
one <- load_gems_table("myShare__mySchema__myTable")

# List what the share exposes (share / schema / name)
gems_list_tables()
```

**R limitation:** the R script uses the Delta Sharing REST API and **Parquet** file URLs. If the server responds in **Delta** response format only, this R code will error; the **Python** script is the more general option in that case.

---

## 7. If something goes wrong

These scripts have been **tested** end-to-end with a valid profile: when the profile is correct, the network allows access, and Python/R can install packages, downloads succeed.

If you hit errors:

1. Confirm **`config.share`** (or **`config.json`**) is in the **same folder** as the script you run, and that your terminal’s **current directory** is that folder (or use `setwd` in R).
2. Check **internet** and any **VPN** or firewall rules.
3. If the error mentions **401 / 403** or **expired**, ask us for a **new** `config.share` file.
4. Use an **AI assistant** (or your IDE’s AI) with the **full error message** and this README — that often speeds up debugging.

---

## 8. Security

**Rule:** **Anyone who has your `config.share` can access the shared data** that credential was issued for — connect, list tables, and download — the same way anyone with an API key or database password can use that secret. There is **no extra login** inside these scripts; possession of the file is enough.

**`config.share` is an access credential**, not the dataset itself. Treat it like a **password or API key**.

**Do not** commit it to GitHub, post it in chat, or forward it casually. Publish only the **repository link** with these **scripts** and this **README**; give each collaborator their **own** `config.share` through a **private** channel when they need access.
