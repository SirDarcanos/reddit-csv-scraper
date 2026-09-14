<div align="center">

# Reddit CSV Scraper

**Export public subreddit submissions and comments to a resumable CSV file.**

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![PRAW](https://img.shields.io/badge/Reddit_API-PRAW-FF4500?style=flat-square&logo=reddit&logoColor=white)
[![MIT License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

[Features](#features) · [Get started](#getting-started) · [Usage](#usage) · [CSV output](#csv-output) · [Troubleshooting](#troubleshooting) · [License](#license)

</div>

Reddit CSV Scraper is a small Python command-line tool for collecting public
posts and their complete comment trees from one subreddit. It uses Reddit's
read-only OAuth API, writes progress after every submission, and resumes an
interrupted scrape without duplicating completed submissions.

## Features

- Exports submission metadata and comments to UTF-8 CSV
- Selects a subreddit through a command-line option or environment variable
- Filters submissions with inclusive start and end dates
- Expands nested comments, including additional comment batches
- Handles Reddit rate limits and transient API failures with bounded retries
- Saves after each submission and resumes from an existing output file
- Requires API credentials, but no Reddit user login or 2FA

## Getting started

### Prerequisites

- Python 3.10 or newer
- A Reddit account
- A Reddit API **script** application

### 1. Create Reddit API credentials

1. Open [Reddit's app preferences](https://www.reddit.com/prefs/apps).
2. Select **create another app**.
3. Choose the **script** application type.
4. Enter a name and a redirect URI such as `http://localhost:8080`.
5. Save the application.
6. Copy the client ID shown beneath the application name and the client secret.

> [!IMPORTANT]
> Keep the client secret private. Store real credentials only in `.env` or in
> environment variables.

### 2. Install the project

Clone or download this repository, open it in a terminal, and create a virtual
environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

### 3. Configure the scraper

Copy the example configuration:

```bash
cp .env.example .env
```

Edit `.env` with your credentials and preferred subreddit:

```dotenv
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=reddit-csv-scraper/1.0 by your_reddit_username
REDDIT_SUBREDDIT=Python
```

| Variable | Required | Description |
| --- | --- | --- |
| `REDDIT_CLIENT_ID` | Yes | Client ID from the Reddit script application |
| `REDDIT_CLIENT_SECRET` | Yes | Client secret from the Reddit script application |
| `REDDIT_USER_AGENT` | Recommended | Descriptive identifier for your API client |
| `REDDIT_SUBREDDIT` | Unless `--subreddit` is used | Default subreddit, with or without `r/` |

Exported environment variables take precedence over values in `.env`.
Command-line options take precedence over the configured subreddit.

### 4. Run a scrape

Scrape the configured subreddit from August 15 through today:

```bash
python reddit_scanner.py --start 2026-08-15
```

The CSV is created automatically under `scrapes/`.

## Usage

```text
python reddit_scanner.py [--subreddit NAME] [--start YYYY-MM-DD]
                          [--end YYYY-MM-DD] [--output PATH]
```

| Option | Description |
| --- | --- |
| `--subreddit NAME` | Subreddit to scrape, such as `Python` or `r/Python` |
| `--start YYYY-MM-DD` | Oldest submission date to include |
| `--end YYYY-MM-DD` | Newest submission date to include; the whole day is included |
| `--output PATH` | Override the generated CSV path |
| `--help` | Show command-line help |

### Common examples

Use a subreddit from `.env` and scrape through today:

```bash
python reddit_scanner.py --start 2026-08-15
```

Override the configured subreddit and use an inclusive date range:

```bash
python reddit_scanner.py \
  --subreddit r/Python \
  --start 2026-08-15 \
  --end 2026-09-14
```

Choose a custom output location:

```bash
python reddit_scanner.py \
  --subreddit Python \
  --start 2026-08-15 \
  --output ~/exports/python.csv
```

When running in an interactive terminal, the script prompts for omitted dates.
Leave the start date empty to scrape as far back as Reddit makes available, or
leave the end date empty to use today. In non-interactive environments, the
same values are the automatic defaults.

## CSV output

The default path is relative to the project directory:

```text
scrapes/topics_with_comments_<start>_to_<end>.csv
```

The `scrapes/` directory is created automatically and excluded from Git. Each
CSV row combines submission fields with one comment. A submission without
comments receives one row with empty comment fields.

| Column | Description |
| --- | --- |
| `submission_id` | Reddit submission ID |
| `submission_author` | Author username or `[deleted]` |
| `submission_title` | Submission title |
| `submission_created` | UTC creation time |
| `submission_flair` | Link flair, when present |
| `submission_over_18` | Whether Reddit marks the submission as NSFW |
| `submission_num_comments` | Comment count reported by Reddit |
| `submission_url` | Submission target URL |
| `comment_id` | Reddit comment ID |
| `comment_author` | Comment author username or `[deleted]` |
| `comment_body` | Comment text |
| `comment_created` | UTC creation time |
| `comment_score` | Comment score reported by Reddit |

### Resume an interrupted scrape

Progress is flushed after every submission. Run the same command again with the
same output path to continue; submission IDs already present in the CSV are
skipped. Pressing <kbd>Ctrl</kbd>+<kbd>C</kbd> stops the process without losing
completed submissions.

> [!NOTE]
> Resuming detects completed submission IDs, not individual comments. Existing
> submissions are not refreshed if comments were added after the first scrape.

## Limitations and data safety

> [!WARNING]
> CSV files contain untrusted user-generated text. Spreadsheet applications may
> interpret cells beginning with `=`, `+`, `-`, or `@` as formulas. Import the
> file with all columns treated as text instead of opening it directly.

- Reddit generally exposes only about the 1,000 newest submissions in a
  subreddit listing. Older portions of a requested range may be unavailable.
- Large comment trees can take time and consume many API requests.
- Deleted or unavailable content cannot be recovered.
- Public availability does not remove privacy or platform-policy obligations.
  Review scraped data before sharing it, especially when it may contain
  personal, health, or otherwise sensitive information.

## Troubleshooting

### Missing Reddit credentials

Confirm that `.env` exists beside `reddit_scanner.py` and contains non-placeholder
values for `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`.

### A subreddit is required

Set `REDDIT_SUBREDDIT` in `.env` or pass `--subreddit NAME` when running the
script.

### Reddit cannot be reached or rejects the credentials

Check the client ID, client secret, network connection, and user-agent value.
The credentials must belong to a Reddit application created with the **script**
type.

### The requested start date was not reached

This is normally Reddit's listing limit rather than a scraper failure. The
script reports the oldest submission it could access.

### The scraper pauses during a run

Temporary pauses are expected when Reddit asks the client to slow down. The
scraper waits and retries automatically, while preserving rows already written.

## License

Licensed under the [MIT License](LICENSE). Copyright © 2026 Nicola Mustone.
