# Contributing

Thank you for contributing to Reddit CSV Scraper. Contributions should be focused, documented, and safe to review without exposing Reddit credentials or scraped data.

## Before you begin

Search the existing GitHub issues before starting work. For a substantial change, open an issue first so its behavior and scope can be agreed before implementation.

Never commit:

- Reddit client IDs, client secrets, or populated `.env` files
- Generated CSV files from `scrapes/`
- Personal or sensitive information from scraped content

Use `.env.example` when documenting configuration.

## Set up a development environment

1. Fork and clone the repository, or clone it directly if you have write access.
2. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

   On Windows PowerShell:

   ```powershell
   .venv\Scripts\Activate.ps1
   ```

3. Install the dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. For API-dependent testing, copy the example configuration and add your own Reddit script-application credentials:

   ```bash
   cp .env.example .env
   ```

The CLI help and source-compilation checks do not require Reddit credentials.

## Make a change

Create a short-lived branch with a descriptive name:

```bash
git switch -c fix/short-description
```

Keep each change focused on one problem. Follow the style already present in `reddit_scanner.py`:

- Support Python 3.10 or newer.
- Use type hints for function inputs and return values.
- Prefer small functions with clear responsibilities.
- Add docstrings where behavior is not evident from the function name.
- Preserve the read-only Reddit API behavior and resumable CSV output unless the proposed change explicitly alters them.
- Update `README.md` when installation, configuration, CLI options, output fields, or user-visible behavior changes.

Avoid committing generated files, caches, local environments, or editor metadata. The existing `.gitignore` covers the common cases.

## Validate the change

Run the automated tests and local checks before submitting a pull request:

```bash
python -m unittest discover -s tests -v
python -m compileall -q reddit_scanner.py scrape_run.py scrape_output.py
python reddit_scanner.py --help
```

For changes that affect Reddit requests or CSV generation, also run a small manual scrape with your own credentials and inspect the resulting CSV. Do not commit the output.

Before submitting, check the working tree for accidental credentials or generated data:

```bash
git status --short
```

Describe any checks you could not run and why.

## Update the changelog

Add notable user-facing changes under the appropriate heading in the `[Unreleased]` section of `CHANGELOG.md`. Documentation-only corrections and internal refactors generally do not need changelog entries unless they materially affect users.

## Submit a pull request

Open a pull request against `main`. In its description:

- Explain the problem and the chosen solution.
- Link related issues.
- List the validation commands and manual checks you ran.
- Call out behavior changes, limitations, or follow-up work.

Keep unrelated changes in separate pull requests. Address review feedback with additional commits so reviewers can follow the evolution of the change.

## Report a bug

Open a GitHub issue and include:

- The command you ran, with credentials removed
- The expected and actual behavior
- Your operating system and Python version
- The relevant error message or traceback
- Minimal reproduction steps

Redact access tokens, client secrets, usernames, and sensitive scraped content.

## Request a feature

Open a GitHub issue describing the use case, desired behavior, and any alternatives you considered. Explain how the change fits a small, read-only command-line scraper.
