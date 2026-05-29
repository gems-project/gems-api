library(httr2)
library(jsonlite)

# Load .env next to this script
args <- commandArgs(trailingOnly = FALSE)
file_line <- grep("^--file=", args, value = TRUE)
env_path <- if (length(file_line)) {
  script_file <- sub("^--file=", "", file_line)
  file.path(dirname(normalizePath(script_file, winslash = "/")), ".env")
} else {
  file.path(getwd(), ".env")
}
if (file.exists(env_path)) {
  readRenviron(env_path)
}

api_key <- Sys.getenv("GEMS_API_KEY", unset = "")
if (!nzchar(api_key)) {
  stop("GEMS_API_KEY is not set. Add it to .env or the environment.", call. = FALSE)
}

base_url <- "https://gems-api.bovi-analytics.org"
data_dir <- "gems_data"
dir.create(data_dir, showWarnings = FALSE)

get_json <- function(path) {
  req <- request(paste0(base_url, path)) |>
    req_headers("X-API-Key" = api_key) |>
    req_timeout(60)
  resp <- req_perform(req)
  resp_check_status(resp)
  resp_body_json(resp)
}

read_metadata <- function(table) {
  path <- file.path(data_dir, paste0(table, ".metadata.json"))
  if (!file.exists(path)) return(NULL)
  fromJSON(path)
}

write_metadata <- function(table, metadata) {
  path <- file.path(data_dir, paste0(table, ".metadata.json"))
  write_json(metadata, path, auto_unbox = TRUE, pretty = TRUE)
}

tables <- get_json("/tables")$tables
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
}

message("Done. Updated ", updated, " table(s); skipped ", skipped, " table(s).")