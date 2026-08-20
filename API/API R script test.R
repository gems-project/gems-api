# install.packages(c("httr2", "httpuv", "jsonlite", "openssl"))
library(httr2)
library(httpuv)
library(jsonlite)
library(openssl)

BASE_URL <- "https://gems-api.bovi-analytics.org"

# Find .env next to this script (works with source() and Rscript).
.env_path <- local({
  for (i in seq_len(sys.nframe())) {
    ofile <- sys.frame(i)$ofile
    if (!is.null(ofile) && nzchar(ofile)) {
      return(file.path(dirname(normalizePath(ofile, winslash = "/", mustWork = FALSE)), ".env"))
    }
  }
  args <- commandArgs(trailingOnly = FALSE)
  file_line <- grep("^--file=", args, value = TRUE)
  if (length(file_line)) {
    script_file <- sub("^--file=", "", file_line)
    return(file.path(dirname(normalizePath(script_file, winslash = "/", mustWork = FALSE)), ".env"))
  }
  file.path(getwd(), ".env")
})

load_gems_env <- function(path) {
  if (!file.exists(path)) return(invisible(FALSE))
  lines <- readLines(path, warn = FALSE, encoding = "UTF-8")
  for (line in lines) {
    line <- sub("^\\s+", "", sub("\\s+$", "", line))
    if (!nzchar(line) || startsWith(line, "#")) next
    if (!grepl("=", line, fixed = TRUE)) next
    key <- sub("^\\s+", "", sub("\\s+$", "", sub("=.*$", "", line)))
    val <- sub("^\\s+", "", sub("\\s+$", "", sub("^[^=]*=", "", line)))
    if (startsWith(val, "\"") && endsWith(val, "\"")) {
      val <- substr(val, 2, nchar(val) - 1)
    } else if (startsWith(val, "'") && endsWith(val, "'")) {
      val <- substr(val, 2, nchar(val) - 1)
    }
    if (nzchar(key)) do.call(Sys.setenv, setNames(list(val), key))
  }
  invisible(TRUE)
}

load_gems_env(.env_path)
API_KEY <- Sys.getenv("GEMS_API_KEY", unset = "")
if (!nzchar(API_KEY)) {
  stop(
    "Add GEMS_API_KEY to a .env file next to this script.\n",
    "Looked for: ", .env_path,
    call. = FALSE
  )
}

b64url <- function(raw) {
  x <- openssl::base64_encode(raw)
  x <- gsub("+", "-", x, fixed = TRUE)
  x <- gsub("/", "_", x, fixed = TRUE)
  gsub("=+$", "", x)
}

parse_qs <- function(q) {
  if (is.null(q) || !nzchar(q)) return(list())
  q <- sub("^\\?", "", q)
  parts <- strsplit(q, "&", fixed = TRUE)[[1]]
  out <- list()
  for (p in parts) {
    kv <- strsplit(p, "=", fixed = TRUE)[[1]]
    key <- utils::URLdecode(kv[[1]])
    val <- if (length(kv) > 1) utils::URLdecode(paste(kv[-1], collapse = "=")) else ""
    out[[key]] <- val
  }
  out
}

gems_headers <- function() {
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
    call = function(req) {
      qs <- parse_qs(req$QUERY_STRING)
      if (!identical(qs$state, state)) {
        ready$error <- "state mismatch"
        status <- 400L
      } else if (!is.null(qs$error)) {
        ready$error <- if (!is.null(qs$error_description)) qs$error_description else qs$error
        status <- 400L
      } else {
        ready$code <- qs$code
        status <- 200L
      }
      list(
        status = status,
        headers = list("Content-Type" = "text/plain; charset=utf-8"),
        body = "GEMS login done. You can close this tab."
      )
    }
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
  while (is.null(ready$code) && is.null(ready$error) && Sys.time() < deadline) {
    httpuv::service(200)
  }
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
  if (is.null(access_token) || !nzchar(access_token)) {
    stop("Auth0 did not return an access token.", call. = FALSE)
  }
  c("X-API-Key" = API_KEY, Authorization = paste("Bearer", access_token))
}

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