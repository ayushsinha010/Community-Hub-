# AI Hub

<p align="center"><img src="static/logo.png" alt="AI Hub logo" width="320"></p>

AI Hub is a small community web app where the people in an organisation share AI prompts and productivity tips, rate each other's contributions, discuss them, run quick polls, and compete on a monthly points leaderboard. It is a server-rendered Flask application with a single SQLite file for storage, so it is easy to run locally and easy to read. It is aimed at teams that want a lightweight internal "prompt library" with a bit of gamification, and at developers who want a compact example of a Flask + SQLite app with sessions, a points ledger and lazy month-end jobs.

## Features

- Email + password accounts. Self-registration is limited to one e-mail domain (configurable). Passwords are stored as hashes (Werkzeug).
- Three kinds of contribution: **AI Prompt**, **Productivity Tip** and **Poll** (2 to 6 options, votes can be changed, results are shown only after you have voted).
- Home feed ("Latest Updates") with an inline composer, filter chips (type, tool, category, department), free-text search (title and body), and sorting by newest or top rated.
- Prompt Library (`/prompts`) and Tip/Suggestion Library (`/tips`): compact lists of one contribution type.
- 1 to 5 star ratings with an optional one-line note (you cannot rate your own contribution; you can change your rating later), plus a separate discussion thread per contribution.
- Authors can edit their own contributions. There is no delete.
- Monthly leaderboard (`/leaderboard`, optional `?month=YYYY-MM`), a "Past Contributors of the Month" list, a "Score Calculation" page generated from the scoring constants in the code, and a personal page (`/me`) with your all-time and this-month points.
- Public profile pages (`/user/<id>`) with computed badges.
- Sidebar widgets on the home page: spotlight contributor, top 5 leaderboard, trending this week, active contributors, popular tags, recent activity.
- Admin-only "Digest" cards shown in a carousel above the feed (auto-advances every 10 seconds).

## Tech stack

- Python 3 (tested with 3.14.7), Flask (tested with 3.1.3, Werkzeug 3.1.9, Jinja2 3.1.6)
- SQLite through the standard-library `sqlite3` module (no ORM)
- waitress 3.0.2 (optional production-style server, listed in `requirements.txt`)
- Plain HTML templates (Jinja), one CSS file and a little vanilla JavaScript; no build step
- The Open Sans font is loaded from Google Fonts by the browser

`requirements.txt` does not pin versions.

## Project structure

```
AI-Hub/
  app.py             Flask routes, login gate, configuration
  db.py              SQLite schema, queries, scoring and leaderboard logic
  requirements.txt
  .env.example       List of the environment variables (not loaded automatically)
  data/              hub.db is created here on first run (.gitkeep keeps the folder)
  static/            style.css, app.js, logo.png
  templates/         Jinja templates (base, home, leaderboard, scoring, ...)
  tests/test_hub.py  unittest suite (Flask test client, temporary database)
```

## Prerequisites

- Python 3 with `pip` and `venv`. Verified on Python 3.14.7 on Windows 11; other 3.x versions and other operating systems were not tried.
- Nothing else: SQLite ships with Python and no external service is needed.

## Setup and run

Windows PowerShell:

```powershell
git clone <your-repository-url> AI-Hub
cd AI-Hub
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

macOS / Linux:

```bash
git clone <your-repository-url> AI-Hub
cd AI-Hub
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Then open <http://127.0.0.1:5050>.

What you see on first run: `data/hub.db` is created automatically and is empty. You are sent to the sign-in page; click "Create an account" and register with an address on the allowed domain (`@example.com` unless you changed `AI_HUB_EMAIL_DOMAIN`). The account registered with the admin address (`admin@example.com` by default, see `AI_HUB_ADMIN_EMAILS`) is an admin and gets a "Post Digest" link in the top bar. The home page starts empty ("Nothing here yet") until someone shares a prompt, tip or poll.

`python app.py` starts the Flask development server with the debugger and auto-reloader on (it is meant for local use only). To run without the debugger, or on another port:

```powershell
$env:AI_HUB_DEBUG = "0"; $env:AI_HUB_PORT = "5200"; python app.py
```

```bash
AI_HUB_DEBUG=0 AI_HUB_PORT=5200 python app.py
```

To use the bundled waitress server instead:

```bash
python -m waitress --listen=127.0.0.1:5050 app:app
```

## Configuration

All settings are environment variables and all are optional. The app reads the real process environment only; it does not load `.env` files (`.env.example` just lists the names).

| Variable | Default | Purpose |
|---|---|---|
| `AI_HUB_SECRET_KEY` | random key generated on first start and stored in `data/.secret_key` | Signs the session cookie. Set your own long random value for any shared or public deployment. |
| `AI_HUB_EMAIL_DOMAIN` | `example.com` | Only addresses on this domain may register. |
| `AI_HUB_ADMIN_EMAILS` | `admin@example.com` | Comma-separated admin accounts. The flag is applied when the account registers and again on every start. |
| `COMPANY_NAME` | `Your Company` | Fallback label shown where a user has no department on record. |
| `AI_HUB_PORT` | `5050` | Port used by `python app.py` (not by waitress). |
| `AI_HUB_DEBUG` | `1` | `1` runs the Flask debug server with auto-reload, `0` turns it off (`python app.py` only). |

## Data and storage

- Everything is stored in one SQLite file, `data/hub.db`. The folder and the tables are created automatically on start; new columns are added by a small migration helper in `db.py`.
- The tables are `users`, `contributions`, `ratings`, `point_events`, `monthly_winners`, `settings`, `comments`, `digests`, `poll_options` and `poll_votes`.
- There is no seeded demo data: a fresh install is empty.
- To reset, stop the app and delete `data/hub.db` (and any `hub.db-journal`). A browser that still holds a session for a deleted user is simply sent back to the sign-in page.
- `data/.secret_key` holds the generated session-signing key when `AI_HUB_SECRET_KEY` is not set. Deleting it logs everyone out. `data/*.db` and `data/.secret_key` are ignored by git.

## How points and the leaderboard work

All points live in one append-only table, `point_events` (one row per award, with a timestamp and an event type). A user's all-time score is the sum of their rows. The numbers below are constants in `db.py`, and the in-app "Score Calculation" page is generated from them.

| Event type | Points | When it is awarded |
|---|---|---|
| `submission_bonus` | +2 | Every time you publish a contribution (prompt, tip or poll). No limit. |
| `first_post_of_month_bonus` | +3 | When you publish and have no other contribution created in the current calendar month. At most once per month. |
| `milestone_bonus` | +5 | When your all-time number of contributions (all types, including polls) becomes a multiple of 3 (3rd, 6th, 9th, ...). |
| `rating` | +1 to +5 | Each rating a contribution receives gives its author as many points as the number of stars. |
| `consistency_bonus` | +10 | One time per user. See below. |

Details:

- **Ratings.** One rating per person per contribution. Authors cannot rate their own contribution, and the star value must be between 1 and 5. If someone changes their rating, the existing rating row and its point row are updated in place, so the author's points change to the new star value, never double-count, and the point row keeps its original timestamp. Editing a rating therefore cannot move points to another month.
- **Consistent High Scorer (+10).** After every rating that is added or changed, the app looks at the author's three most recently created contributions that have at least one rating (contributions with no ratings are skipped). If there are three of them and each one's average star rating is 4.0 or higher, the author receives a single +10 event, recorded against the newest of the three and timestamped at that moment. It is never awarded twice to the same user.
- **Polls.** A poll is stored as a contribution, so publishing one earns the same submission, first-of-month and every-third-post points. Votes themselves earn nothing. The rating form is not shown for polls.
- **Editing** a contribution does not change any points.
- **Monthly leaderboard.** For a month `YYYY-MM`, the app sums `points` over the `point_events` rows whose timestamp falls in that month, grouped by author, highest first. Only users with at least one event in that month appear. Ties are not broken; tied users appear in whatever order SQLite returns them. Because a rating is counted in the month in which it was first given, an old contribution that is rated for the first time in a later month earns its author points in that later month.
- **Rank.** The "#N" pill in the top bar and the "Top of the Month" badge use the position in the current month's leaderboard. A user with no points yet this month has no rank.
- **Past winners.** There is no scheduled job. On every request from a signed-in user, `close_previous_month_if_needed()` checks whether the previous calendar month has been recorded yet (using a flag in the `settings` table). If not, it stores that month's top scorer (one row: user and total points) in `monthly_winners`, or nothing if nobody scored that month, and sets the flag. Only the immediately previous month is ever examined, so if nobody opens the app during a whole calendar month, that month's winner is never recorded. These rows feed "Past Contributors of the Month" on the leaderboard page.
- **Badges** (computed on the fly for the profile page, nothing stored): First Post (1+ contributions), Prolific Contributor (5+ contributions), Top of the Month (rank 1 this month), Community Favorite (any one contribution with 5+ ratings), Highly Rated (the average of the per-contribution average ratings over rated contributions is 4.5 or higher).
- **Other rankings.** "Top rated" sorts by average stars, then number of ratings, then newest. "Trending this week" lists contributions created in the last 7 days that have at least one rating or comment, ordered by ratings plus comments. "Active contributors" shows the four people with the most contributions of all time, excluding the viewer.

Worked example: a new user's first prompt scores 2 + 3 = 5. Their second scores 2. Their third scores 2 + 5 = 7, for a total of 14 before any ratings.

## Tests / quality checks

```bash
python -m unittest discover -s tests -v
```

The suite (18 tests) uses a temporary database and Flask's test client; it never touches `data/hub.db`. It covers the session-key loading, the empty-database pages, registration rules, login and logout, stale sessions, the admin-only digest page, every scoring rule (submission, first post of the month, every third post, rating points, re-rating in place, self-rating and out-of-range rejection, the consistency bonus), leaderboard ordering, the lazy previous-month winner snapshot, polls (creation, the 2 to 6 option limits, changing a vote), editing, comments, profiles, and filtering/search/sort. There is no linter or type-check configuration.

## Notes and limitations

- Registration only checks the e-mail domain; the app never sends mail, so it cannot verify that someone owns an address. Anyone who can reach the app and type an address on the allowed domain can create an account. There is no password reset, no login throttling, no SSO and no CSRF protection on the forms.
- No HTTPS and the session cookie is not marked secure. If you deploy it, put it behind a reverse proxy that terminates TLS, set `AI_HUB_SECRET_KEY`, and set `AI_HUB_DEBUG=0` (or use waitress). Never expose the Flask debug server to a network.
- Month boundaries mix two clocks: timestamps are stored by SQLite in UTC, while "this month" is taken from the server's local date. Close to a month change in a non-UTC timezone, events can land in a different month than expected.
- The `/contribution/<id>/rate` endpoint does not check the contribution type; the polls page hides the rating form, but a hand-crafted request could rate a poll and award points.
- Every database call opens its own SQLite connection; this is fine for small teams but is not designed for heavy concurrent writes.
- No moderation or delete features. `contributions.status` exists but is always `published`.
- Search uses SQL `LIKE` on title and body only.
- The browser loads the Open Sans font from Google Fonts, so an internet connection is needed for the intended typography (the app still works without it).

## License

Released under the MIT License: see [LICENSE](LICENSE). Replace it with another license if you prefer.
