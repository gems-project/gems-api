# Packages install automatically if missing: jsonlite, httr2, arrow, writexl
# Parquet-backed shares only; Delta response format is not supported in pure R.

.ensure_pkg <- function(pkg) {
  if (requireNamespace(pkg, quietly = TRUE)) {
    return(invisible(NULL))
  }
  install.packages(pkg, repos = "https://cloud.r-project.org", quiet = TRUE)
  if (!requireNamespace(pkg, quietly = TRUE)) {
    stop("Could not install package: ", pkg)
  }
}

for (p in c("jsonlite", "httr2", "arrow", "writexl")) {
  .ensure_pkg(p)
}

script_dir <- function() {
  args <- commandArgs(trailingOnly = FALSE)
  f <- sub("^--file=", "", args[grepl("^--file=", args)])
  if (length(f) >= 1L && nzchar(f[[1L]])) {
    return(normalizePath(dirname(f[[1L]]), winslash = "/", mustWork = TRUE))
  }
  normalizePath(getwd(), winslash = "/", mustWork = TRUE)
}

CONFIG_NAMES <- c("config.share", "config.json")

find_config_path <- function() {
  bases <- c(script_dir(), normalizePath(getwd(), winslash = "/", mustWork = TRUE))
  bases <- unique(bases)
  for (dir in bases) {
    for (nm in CONFIG_NAMES) {
      p <- file.path(dir, nm)
      if (file.exists(p)) {
        return(normalizePath(p, winslash = "/", mustWork = TRUE))
      }
    }
  }
  stop(
    "Missing profile. Save your activation file as config.share or config.json ",
    "next to this script, or set the working directory to that folder."
  )
}

.read_profile <- function(path) {
  raw <- readBin(path, "raw", file.info(path)$size)
  if (length(raw) >= 3L && raw[[1L]] == as.raw(0xef) && raw[[2L]] == as.raw(0xbb) && raw[[3L]] == as.raw(0xbf)) {
    raw <- raw[-(1L:3L)]
  }
  j <- jsonlite::parse_json(rawToChar(raw), simplifyVector = TRUE)
  ep <- j[["endpoint"]]
  if (is.null(ep) || !nzchar(ep)) {
    stop("Profile missing endpoint")
  }
  ep <- sub("/+$", "", ep)
  tok <- j[["bearerToken"]]
  if (is.null(tok) || !nzchar(tok)) {
    stop("Profile missing bearerToken")
  }
  list(endpoint = ep, bearer_token = tok)
}

.ds_get <- function(prof, path, page_token = NULL) {
  url <- paste0(prof$endpoint, path)
  req <- httr2::request(url) |>
    httr2::req_headers(
      Authorization = paste("Bearer", prof$bearer_token),
      Accept = "application/json; charset=utf-8",
      `User-Agent` = "delta-sharing-r/1.0"
    )
  if (!is.null(page_token) && nzchar(page_token)) {
    req <- httr2::req_url_query(req, pageToken = page_token)
  }
  resp <- httr2::req_perform(req)
  httr2::resp_body_json(resp, simplifyVector = TRUE)
}

.ds_post_ndjson <- function(prof, path, body = NULL) {
  url <- paste0(prof$endpoint, path)
  req <- httr2::request(url) |>
    httr2::req_method("POST") |>
    httr2::req_headers(
      Authorization = paste("Bearer", prof$bearer_token),
      Accept = "application/json; charset=utf-8",
      `Content-Type` = "application/json; charset=utf-8",
      `User-Agent` = "delta-sharing-r/1.0"
    )
  if (is.null(body) || length(body) == 0L) {
    req <- httr2::req_body_raw(req, charToRaw("{}"), type = "application/json; charset=utf-8")
  } else {
    req <- httr2::req_body_json(req, body)
  }
  resp <- httr2::req_perform(req)
  caps <- tryCatch(
    as.character(httr2::resp_header(resp, "delta-sharing-capabilities")),
    error = function(e) NA_character_
  )
  if (!is.na(caps) && grepl("responseformat=delta", caps, ignore.case = TRUE)) {
    stop("This share uses Delta response format (not Parquet file URLs). This R reader cannot load it.")
  }
  txt <- httr2::resp_body_string(resp)
  lines <- strsplit(txt, "\n", fixed = TRUE)[[1L]]
  sub("\r$", "", lines, fixed = FALSE)
}

.list_share_names <- function(prof) {
  names_chr <- character()
  tok <- NULL
  repeat {
    sh <- .ds_get(prof, "/shares", page_token = tok)
    items <- sh[["items"]]
    if (length(items)) {
      if (is.data.frame(items)) {
        names_chr <- c(names_chr, as.character(items$name))
      } else {
        names_chr <- c(names_chr, vapply(items, function(x) as.character(x[["name"]]), ""))
      }
    }
    tok <- sh[["nextPageToken"]]
    if (is.null(tok) || !nzchar(as.character(tok)[1L])) {
      break
    }
    tok <- as.character(tok)[1L]
  }
  unique(names_chr[nzchar(names_chr)])
}

.list_all_tables <- function(prof) {
  out <- list()
  for (sn in .list_share_names(prof)) {
    enc <- utils::URLencode(sn, reserved = TRUE)
    tok <- NULL
    repeat {
      tb <- .ds_get(prof, paste0("/shares/", enc, "/all-tables"), page_token = tok)
      items <- tb[["items"]]
      if (length(items)) {
        if (is.data.frame(items)) {
          for (i in seq_len(nrow(items))) {
            out[[length(out) + 1L]] <- list(
              name = as.character(items$name[i]),
              share = as.character(items$share[i]),
              schema = as.character(items$schema[i])
            )
          }
        } else {
          for (it in items) {
            out[[length(out) + 1L]] <- list(
              name = it[["name"]],
              share = it[["share"]],
              schema = it[["schema"]]
            )
          }
        }
      }
      tok <- tb[["nextPageToken"]]
      if (is.null(tok) || !nzchar(as.character(tok)[1L])) {
        break
      }
      tok <- as.character(tok)[1L]
    }
  }
  out
}

.safe_stem <- function(share, schema, name) {
  raw <- paste(share, schema, name, sep = "__")
  gsub("[^A-Za-z0-9._-]", "_", raw)
}

# Drop lineage / ingest metadata (match case-insensitive; ignore spaces/underscores in names).
# Keep in sync with load_shared_table.py `_DROP_COL_NORMS`.
.drop_pipeline_cols <- function(df) {
  if (!ncol(df)) {
    return(df)
  }
  norm <- function(x) gsub("[^a-z0-9]", "", tolower(as.character(x)))
  drop_norms <- c(
    "sequence", "workbookfile", "workbookpath",
    "gaterunid", "ingestid", "excelsourcerow"
  )
  nms <- names(df)
  keep <- !vapply(nms, function(nm) norm(nm) %in% drop_norms, logical(1L))
  df[, keep, drop = FALSE]
}

# Excel 2007+ sheet limit (writexl / Excel).
EXCEL_MAX_ROWS <- 1048576L
# Full HTML for millions of rows is unusable; preview only.
HTML_PREVIEW_ROWS <- 10000L

.html_escape <- function(x) {
  x <- as.character(x)
  x <- gsub("&", "&amp;", x, fixed = TRUE)
  x <- gsub("<", "&lt;", x, fixed = TRUE)
  x <- gsub(">", "&gt;", x, fixed = TRUE)
  x <- gsub("\"", "&quot;", x, fixed = TRUE)
  x[is.na(x)] <- ""
  x
}

.strip_tz_for_excel <- function(df) {
  if (!ncol(df)) {
    return(df)
  }
  for (j in seq_len(ncol(df))) {
    x <- df[[j]]
    if (inherits(x, "POSIXct")) {
      utc <- as.POSIXct(format(x, tz = "UTC"), tz = "UTC")
      attr(utc, "tzone") <- NULL
      df[[j]] <- utc
    }
  }
  df
}

.df_to_html_table <- function(df) {
  cn <- names(df)
  th <- paste0("<th>", .html_escape(cn), "</th>", collapse = "")
  if (!nrow(df)) {
    return(paste0(
      "<table class=\"data\" border=\"0\"><thead><tr>", th,
      "</tr></thead><tbody></tbody></table>"
    ))
  }
  parts <- vector("list", nrow(df))
  for (i in seq_len(nrow(df))) {
    cells <- vapply(seq_len(ncol(df)), function(k) {
      .html_escape(df[[k]][i])
    }, "")
    parts[[i]] <- paste0("<tr>", paste0("<td>", cells, "</td>", collapse = ""), "</tr>")
  }
  paste0(
    "<table class=\"data\" border=\"0\"><thead><tr>", th,
    "</tr></thead><tbody>\n", paste0(parts, collapse = "\n"), "\n</tbody></table>"
  )
}

.write_html_doc <- function(path, title, inner_body) {
  doc <- paste0(
    "<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"/><title>",
    .html_escape(title), "</title>\n<style>\n",
    "body{font-family:system-ui,sans-serif;margin:16px;background:#f5f5f5;}\n",
    ".wrap{overflow:auto;max-width:100%;background:#fff;padding:12px;border-radius:8px;}\n",
    "table.data{border-collapse:collapse;font-size:12px;}\n",
    "table.data th,table.data td{border:1px solid #ccc;padding:6px 10px;}\n",
    "table.data th{background:#222;color:#fff;position:sticky;top:0;}\n",
    "</style></head><body><p><b>", .html_escape(title), "</b></p>",
    "<div class=\"wrap\">", inner_body, "</div></body></html>"
  )
  writeLines(doc, path, useBytes = FALSE)
}

.write_table_exports <- function(df, stem, out_dir) {
  xlsx_path <- file.path(out_dir, paste0(stem, ".xlsx"))
  csv_path <- file.path(out_dir, paste0(stem, ".csv"))
  html_path <- file.path(out_dir, paste0(stem, ".html"))
  n <- nrow(df)
  if (n <= EXCEL_MAX_ROWS) {
    writexl::write_xlsx(df, path = xlsx_path)
    message("  ", basename(xlsx_path))
  } else {
    message(
      "  (skipped ", basename(xlsx_path), ": ", format(n, big.mark = ","),
      " rows exceeds Excel limit ", format(EXCEL_MAX_ROWS, big.mark = ","),
      "; writing CSV instead)"
    )
    utils::write.csv(df, csv_path, row.names = FALSE, fileEncoding = "UTF-8")
    message("  ", basename(csv_path))
  }
  if (n <= HTML_PREVIEW_ROWS) {
    html_df <- df
    html_title <- paste0(stem, " — ", n, " rows × ", ncol(df), " cols")
    inner <- .df_to_html_table(html_df)
  } else {
    html_df <- head(df, HTML_PREVIEW_ROWS)
    html_title <- paste0(
      stem, " — preview: first ", format(HTML_PREVIEW_ROWS, big.mark = ","), " of ",
      format(n, big.mark = ","), " rows × ", ncol(df), " cols"
    )
    full_ext <- if (n > EXCEL_MAX_ROWS) ".csv" else ".xlsx"
    inner <- paste0(
      "<p class=\"note\" style=\"margin:0 0 12px;padding:8px;background:#fff3cd;",
      "border-radius:6px;\">Showing first ", format(HTML_PREVIEW_ROWS, big.mark = ","),
      " rows only. Open the <b>", full_ext, "</b> export for the full table.</p>",
      .df_to_html_table(html_df)
    )
  }
  .write_html_doc(html_path, html_title, inner)
  message("  ", basename(html_path))
}

.read_table_parquet <- function(prof, share, schema, table_name) {
  path <- sprintf(
    "/shares/%s/schemas/%s/tables/%s/query",
    utils::URLencode(share, reserved = TRUE),
    utils::URLencode(schema, reserved = TRUE),
    utils::URLencode(table_name, reserved = TRUE)
  )
  lines <- .ds_post_ndjson(prof, path, body = NULL)
  lines <- lines[nzchar(lines)]
  if (length(lines) < 2L) {
    stop("Unexpected empty query response")
  }
  file_lines <- lines[-(1:2)]
  if (!length(file_lines)) {
    return(data.frame())
  }
  urls <- character()
  for (ln in file_lines) {
    o <- jsonlite::parse_json(ln)
    u <- o[["file"]][["url"]]
    if (!is.null(u) && nzchar(u)) {
      urls <- c(urls, u)
    }
  }
  if (!length(urls)) {
    return(data.frame())
  }
  parts <- lapply(urls, function(u) arrow::read_parquet(u, as_data_frame = TRUE))
  out <- if (length(parts) == 1L) {
    parts[[1L]]
  } else {
    do.call(rbind, parts)
  }
  .drop_pipeline_cols(out)
}

gems_client <- function(config_path = NULL) {
  if (is.null(config_path)) {
    config_path <- find_config_path()
  }
  list(profile = .read_profile(config_path), config_path = config_path)
}

gems_list_tables <- function(client = gems_client()) {
  .list_all_tables(client$profile)
}

gems_load_table <- function(share, schema, name, client = gems_client()) {
  .read_table_parquet(client$profile, share, schema, name)
}

load_gems_tables <- function(client = gems_client(), write_xlsx = TRUE) {
  tabs <- gems_list_tables(client)
  if (!length(tabs)) {
    stop("No shared tables found.")
  }
  out_dir <- file.path(script_dir(), "shared_table_exports")
  if (isTRUE(write_xlsx)) {
    dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
  }
  result <- stats::setNames(vector("list", length(tabs)), character(length(tabs)))
  for (i in seq_along(tabs)) {
    t <- tabs[[i]]
    stem <- .safe_stem(t$share, t$schema, t$name)
    names(result)[i] <- stem
    result[[i]] <- .read_table_parquet(client$profile, t$share, t$schema, t$name)
    if (isTRUE(write_xlsx)) {
      message("Loading ", t$share, ".", t$schema, ".", t$name, " ...")
      df <- .strip_tz_for_excel(result[[i]])
      .write_table_exports(df, stem, out_dir)
    }
  }
  if (isTRUE(write_xlsx)) {
    message("Done. Output folder: ", out_dir)
  }
  result
}

load_gems_table <- function(name = NULL, ...) {
  tabs <- load_gems_tables(...)
  if (is.null(name)) {
    return(tabs[[1L]])
  }
  if (!name %in% names(tabs)) {
    stop("No table named ", encodeString(name), ". Available: ", paste(names(tabs), collapse = ", "))
  }
  tabs[[name]]
}

args <- commandArgs(trailingOnly = FALSE)
if (any(grepl("^--file=", args))) {
  tabs <- load_gems_tables()
  message("Loaded ", length(tabs), " table(s).")
  for (n in names(tabs)) {
    d <- tabs[[n]]
    message(n, ": ", nrow(d), " rows, ", ncol(d), " columns")
  }
}
