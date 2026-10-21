import sqlite3
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "hub.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id INTEGER NOT NULL REFERENCES users(id),
    contribution_type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    tool TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'published',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_id INTEGER NOT NULL REFERENCES contributions(id),
    rater_id INTEGER NOT NULL REFERENCES users(id),
    stars INTEGER NOT NULL,
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT,
    UNIQUE(contribution_id, rater_id)
);

CREATE TABLE IF NOT EXISTS point_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_id INTEGER NOT NULL REFERENCES contributions(id),
    author_id INTEGER NOT NULL REFERENCES users(id),
    rating_id INTEGER REFERENCES ratings(id),
    event_type TEXT NOT NULL,
    points INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS monthly_winners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year_month TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    total_points INTEGER NOT NULL,
    computed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_id INTEGER NOT NULL REFERENCES contributions(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    author_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS poll_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_id INTEGER NOT NULL REFERENCES contributions(id),
    option_text TEXT NOT NULL,
    position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contribution_id INTEGER NOT NULL REFERENCES contributions(id),
    option_id INTEGER NOT NULL REFERENCES poll_options(id),
    voter_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contribution_id, voter_id)
);
"""

# Comma-separated list in the AI_HUB_ADMIN_EMAILS environment variable. These accounts
# get is_admin = 1 on every start (admins can post digests).
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("AI_HUB_ADMIN_EMAILS", "admin@example.com").split(",")
    if e.strip()
}

SUBMISSION_BONUS_POINTS = 2
# Self-registration is limited to this e-mail domain (set AI_HUB_EMAIL_DOMAIN).
ALLOWED_EMAIL_DOMAIN = os.environ.get("AI_HUB_EMAIL_DOMAIN", "example.com").strip().lower()
CONTRIBUTION_TYPES = {
    "ai_prompt": "AI Prompt",
    "productivity_tip": "Productivity Tip",
    "poll": "Poll",
}


def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_add_column(conn, table, column, col_type):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        conn.commit()


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_add_column(conn, "users", "department", "TEXT")
    _migrate_add_column(conn, "users", "is_admin", "INTEGER NOT NULL DEFAULT 0")
    placeholders = ",".join("?" * len(ADMIN_EMAILS))
    conn.execute(f"UPDATE users SET is_admin = 1 WHERE email IN ({placeholders})", tuple(ADMIN_EMAILS))
    conn.commit()
    conn.close()


# ---- users ----------------------------------------------------------------

def create_user(email, display_name, password_hash, department=None):
    conn = get_conn()
    # Accounts listed in ADMIN_EMAILS are admins from the moment they register
    # (init_db() also re-applies the flag on every start).
    is_admin = 1 if email.strip().lower() in ADMIN_EMAILS else 0
    cur = conn.execute(
        "INSERT INTO users (email, display_name, password_hash, department, is_admin) VALUES (?, ?, ?, ?, ?)",
        (email, display_name, password_hash, department, is_admin),
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return user_id


def get_user_by_email(email):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ---- contributions ----------------------------------------------------------------

FIRST_POST_OF_MONTH_BONUS = 3
MILESTONE_POST_BONUS = 5
MILESTONE_EVERY = 3
CONSISTENCY_BONUS = 10
CONSISTENCY_MIN_STARS = 4
CONSISTENCY_STREAK = 3

SCORING_RULES = [
    {"rule": "Share a new prompt or tip", "points": f"+{SUBMISSION_BONUS_POINTS}", "note": "Every time you post, no limit."},
    {"rule": "First post of the month", "points": f"+{FIRST_POST_OF_MONTH_BONUS} bonus", "note": "Awarded once, on your first post each calendar month."},
    {"rule": f"Every {MILESTONE_EVERY}rd post milestone", "points": f"+{MILESTONE_POST_BONUS} bonus", "note": f"On your {MILESTONE_EVERY}rd, {MILESTONE_EVERY*2}th, {MILESTONE_EVERY*3}th... post, all-time."},
    {"rule": "Each star your post receives", "points": "+1 to +5", "note": "Per rating - a 5-star rating is worth 5 points to you."},
    {"rule": "Consistent High Scorer", "points": f"+{CONSISTENCY_BONUS} bonus", "note": f"One-time, when your last {CONSISTENCY_STREAK} rated posts each average {CONSISTENCY_MIN_STARS}+ stars."},
]


def _award(conn, contribution_id, author_id, event_type, points):
    conn.execute(
        """INSERT INTO point_events (contribution_id, author_id, rating_id, event_type, points)
           VALUES (?, ?, NULL, ?, ?)""",
        (contribution_id, author_id, event_type, points),
    )


def create_contribution(author_id, contribution_type, title, body, tool, category):
    from datetime import date

    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO contributions (author_id, contribution_type, title, body, tool, category)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (author_id, contribution_type, title, body, tool or None, category or None),
    )
    contribution_id = cur.lastrowid
    _award(conn, contribution_id, author_id, "submission_bonus", SUBMISSION_BONUS_POINTS)

    this_month = date.today().strftime("%Y-%m")
    posts_this_month_before = conn.execute(
        "SELECT COUNT(*) AS n FROM contributions WHERE author_id = ? AND strftime('%Y-%m', created_at) = ? AND id != ?",
        (author_id, this_month, contribution_id),
    ).fetchone()["n"]
    if posts_this_month_before == 0:
        _award(conn, contribution_id, author_id, "first_post_of_month_bonus", FIRST_POST_OF_MONTH_BONUS)

    total_posts = conn.execute(
        "SELECT COUNT(*) AS n FROM contributions WHERE author_id = ?", (author_id,)
    ).fetchone()["n"]
    if total_posts % MILESTONE_EVERY == 0:
        _award(conn, contribution_id, author_id, "milestone_bonus", MILESTONE_POST_BONUS)

    conn.commit()
    conn.close()
    return contribution_id


def _maybe_award_consistency_bonus(conn, author_id):
    """One-time bonus: if the author's most recent CONSISTENCY_STREAK rated
    contributions each average >= CONSISTENCY_MIN_STARS, and this bonus hasn't
    already been given, award it. Checked after every rating change since that's
    the only time a contribution's average can newly cross the threshold."""
    already = conn.execute(
        "SELECT 1 FROM point_events WHERE author_id = ? AND event_type = 'consistency_bonus'",
        (author_id,),
    ).fetchone()
    if already:
        return

    recent = conn.execute(
        """SELECT c.id, AVG(r.stars) AS avg_stars, COUNT(r.id) AS n
           FROM contributions c JOIN ratings r ON r.contribution_id = c.id
           WHERE c.author_id = ?
           GROUP BY c.id
           ORDER BY c.created_at DESC
           LIMIT ?""",
        (author_id, CONSISTENCY_STREAK),
    ).fetchall()
    if len(recent) < CONSISTENCY_STREAK:
        return
    if all(row["avg_stars"] >= CONSISTENCY_MIN_STARS for row in recent):
        conn.execute(
            """INSERT INTO point_events (contribution_id, author_id, rating_id, event_type, points)
               VALUES (?, ?, NULL, 'consistency_bonus', ?)""",
            (recent[0]["id"], author_id, CONSISTENCY_BONUS),
        )


def update_contribution(contribution_id, title, body, tool, category):
    conn = get_conn()
    conn.execute(
        """UPDATE contributions SET title = ?, body = ?, tool = ?, category = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (title, body, tool or None, category or None, contribution_id),
    )
    conn.commit()
    conn.close()


def get_contribution(contribution_id):
    conn = get_conn()
    row = conn.execute(
        """SELECT c.*, u.display_name AS author_name, u.email AS author_email, u.department AS author_department,
                  COALESCE((SELECT SUM(points) FROM point_events WHERE author_id = u.id), 0) AS author_total_points
           FROM contributions c JOIN users u ON u.id = c.author_id
           WHERE c.id = ?""",
        (contribution_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_contributions(contribution_type=None, tool=None, category=None, department=None, query=None, sort="newest"):
    conn = get_conn()
    where = ["c.status = 'published'"]
    params = []
    if contribution_type:
        where.append("c.contribution_type = ?")
        params.append(contribution_type)
    if tool:
        where.append("c.tool = ?")
        params.append(tool)
    if category:
        where.append("c.category = ?")
        params.append(category)
    if department:
        where.append("u.department = ?")
        params.append(department)
    if query:
        where.append("(c.title LIKE ? OR c.body LIKE ?)")
        needle = f"%{query}%"
        params.extend([needle, needle])

    order = "c.created_at DESC"
    if sort == "top_rated":
        order = "avg_stars DESC, rating_count DESC, c.created_at DESC"

    sql = f"""
        SELECT c.*, u.display_name AS author_name, u.department AS author_department,
               COALESCE(AVG(r.stars), 0) AS avg_stars,
               COUNT(DISTINCT r.id) AS rating_count,
               COALESCE((SELECT SUM(points) FROM point_events WHERE author_id = u.id), 0) AS author_total_points,
               COALESCE((SELECT COUNT(*) FROM comments WHERE contribution_id = c.id), 0) AS comment_count,
               COALESCE((SELECT COUNT(*) FROM poll_votes WHERE contribution_id = c.id), 0) AS poll_vote_count
        FROM contributions c
        JOIN users u ON u.id = c.author_id
        LEFT JOIN ratings r ON r.contribution_id = c.id
        WHERE {' AND '.join(where)}
        GROUP BY c.id
        ORDER BY {order}
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def distinct_values(column):
    assert column in ("tool", "category")
    conn = get_conn()
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM contributions WHERE {column} IS NOT NULL AND {column} != '' ORDER BY {column}"
    ).fetchall()
    conn.close()
    return [r[column] for r in rows]


def current_user_rank(user_id):
    from datetime import date

    board = leaderboard_for_month(date.today().strftime("%Y-%m"))
    for i, row in enumerate(board):
        if row["user_id"] == user_id:
            return i + 1
    return None


def user_total_points(user_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(SUM(points), 0) AS total FROM point_events WHERE author_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return row["total"]


def distinct_departments():
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT department FROM users WHERE department IS NOT NULL AND department != '' ORDER BY department"
    ).fetchall()
    conn.close()
    return [r["department"] for r in rows]


def contribution_rating_summary(contribution_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT COALESCE(AVG(stars), 0) AS avg_stars, COUNT(*) AS rating_count FROM ratings WHERE contribution_id = ?",
        (contribution_id,),
    ).fetchone()
    conn.close()
    return dict(row)


def list_ratings_for_contribution(contribution_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT r.*, u.display_name AS rater_name
           FROM ratings r JOIN users u ON u.id = r.rater_id
           WHERE r.contribution_id = ?
           ORDER BY r.created_at DESC""",
        (contribution_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_rating(contribution_id, rater_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM ratings WHERE contribution_id = ? AND rater_id = ?",
        (contribution_id, rater_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_rating(contribution_id, rater_id, stars, comment):
    """Insert a new rating (and its point_event), or if this rater already rated
    this contribution, UPDATE the existing rating and its linked point_event's
    points IN PLACE - deliberately keeping the original created_at timestamp so
    editing a rating can't shift which month it counts toward, or double-count
    points by inserting a second event."""
    contribution = get_contribution(contribution_id)
    if not contribution:
        raise ValueError("Contribution not found")
    if contribution["author_id"] == rater_id:
        raise ValueError("You can't rate your own contribution.")
    if not (1 <= int(stars) <= 5):
        raise ValueError("Rating must be between 1 and 5 stars.")

    conn = get_conn()
    existing = conn.execute(
        "SELECT * FROM ratings WHERE contribution_id = ? AND rater_id = ?",
        (contribution_id, rater_id),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE ratings SET stars = ?, comment = ?, updated_at = datetime('now') WHERE id = ?",
            (stars, comment or None, existing["id"]),
        )
        conn.execute(
            "UPDATE point_events SET points = ? WHERE rating_id = ?",
            (stars, existing["id"]),
        )
    else:
        cur = conn.execute(
            "INSERT INTO ratings (contribution_id, rater_id, stars, comment) VALUES (?, ?, ?, ?)",
            (contribution_id, rater_id, stars, comment or None),
        )
        rating_id = cur.lastrowid
        conn.execute(
            """INSERT INTO point_events (contribution_id, author_id, rating_id, event_type, points)
               VALUES (?, ?, ?, 'rating', ?)""",
            (contribution_id, contribution["author_id"], rating_id, stars),
        )
    _maybe_award_consistency_bonus(conn, contribution["author_id"])
    conn.commit()
    conn.close()


# ---- scoring / leaderboard ----------------------------------------------------------------

def leaderboard_for_month(year_month):
    conn = get_conn()
    rows = conn.execute(
        """SELECT u.id AS user_id, u.display_name, SUM(pe.points) AS total_points
           FROM point_events pe JOIN users u ON u.id = pe.author_id
           WHERE strftime('%Y-%m', pe.created_at) = ?
           GROUP BY pe.author_id
           ORDER BY total_points DESC""",
        (year_month,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_monthly_winners():
    conn = get_conn()
    rows = conn.execute(
        """SELECT mw.*, u.display_name FROM monthly_winners mw
           JOIN users u ON u.id = mw.user_id
           ORDER BY mw.year_month DESC"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def close_previous_month_if_needed():
    """Lazily snapshots the previous calendar month's leaderboard winner into
    monthly_winners the first time anyone hits the app after that month ends -
    no cron needed. Idempotent via both a settings flag and the table's own
    UNIQUE(year_month) constraint."""
    from datetime import date

    today = date.today()
    prev_month_last_day = date(today.year, today.month, 1) - __import__("datetime").timedelta(days=1)
    prev_year_month = prev_month_last_day.strftime("%Y-%m")

    conn = get_conn()
    already_closed = _setting(conn, "last_closed_month")
    if already_closed == prev_year_month:
        conn.close()
        return
    existing = conn.execute(
        "SELECT 1 FROM monthly_winners WHERE year_month = ?", (prev_year_month,)
    ).fetchone()
    if not existing:
        board = conn.execute(
            """SELECT pe.author_id AS user_id, SUM(pe.points) AS total_points
               FROM point_events pe
               WHERE strftime('%Y-%m', pe.created_at) = ?
               GROUP BY pe.author_id
               ORDER BY total_points DESC
               LIMIT 1""",
            (prev_year_month,),
        ).fetchone()
        if board:
            conn.execute(
                "INSERT OR IGNORE INTO monthly_winners (year_month, user_id, total_points) VALUES (?, ?, ?)",
                (prev_year_month, board["user_id"], board["total_points"]),
            )
    _set_setting(conn, "last_closed_month", prev_year_month)
    conn.commit()
    conn.close()


def user_stats(user_id):
    from datetime import date

    conn = get_conn()
    this_month = date.today().strftime("%Y-%m")
    all_time = conn.execute(
        "SELECT COALESCE(SUM(points), 0) AS total FROM point_events WHERE author_id = ?", (user_id,)
    ).fetchone()["total"]
    month_total = conn.execute(
        "SELECT COALESCE(SUM(points), 0) AS total FROM point_events WHERE author_id = ? AND strftime('%Y-%m', created_at) = ?",
        (user_id, this_month),
    ).fetchone()["total"]
    contributions = conn.execute(
        """SELECT c.*, COALESCE(AVG(r.stars), 0) AS avg_stars, COUNT(r.id) AS rating_count
           FROM contributions c LEFT JOIN ratings r ON r.contribution_id = c.id
           WHERE c.author_id = ?
           GROUP BY c.id
           ORDER BY c.created_at DESC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return {
        "all_time_points": all_time,
        "month_points": month_total,
        "contributions": [dict(r) for r in contributions],
    }


# ---- comments (separate from the one-line rating comment - real discussion) ------

def add_comment(contribution_id, user_id, body):
    conn = get_conn()
    conn.execute(
        "INSERT INTO comments (contribution_id, user_id, body) VALUES (?, ?, ?)",
        (contribution_id, user_id, body),
    )
    conn.commit()
    conn.close()


def list_comments(contribution_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT cm.*, u.display_name AS commenter_name, u.department AS commenter_department
           FROM comments cm JOIN users u ON u.id = cm.user_id
           WHERE cm.contribution_id = ?
           ORDER BY cm.created_at ASC""",
        (contribution_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---- discovery widgets ----------------------------------------------------------------

def popular_tags(limit=8):
    """Combines tool+category into one 'tags' pool for a simple discovery widget -
    the two fields serve the same purpose (a topic label), just different columns."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT tag, COUNT(*) AS n FROM (
             SELECT tool AS tag FROM contributions WHERE tool IS NOT NULL AND tool != ''
             UNION ALL
             SELECT category AS tag FROM contributions WHERE category IS NOT NULL AND category != ''
           )
           GROUP BY tag
           ORDER BY n DESC, tag
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def trending_this_week(limit=5):
    """Most-engaged contributions from the last 7 days (ratings + comments combined) -
    a 'hot right now' signal distinct from the all-time 'top rated' sort."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT c.id, c.title,
                  COALESCE((SELECT COUNT(*) FROM ratings WHERE contribution_id = c.id), 0) AS rating_count,
                  COALESCE((SELECT COUNT(*) FROM comments WHERE contribution_id = c.id), 0) AS comment_count
           FROM contributions c
           WHERE c.status = 'published' AND c.created_at >= datetime('now', '-7 days')
           ORDER BY (rating_count + comment_count) DESC, c.created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows if (r["rating_count"] + r["comment_count"]) > 0]


def active_contributors(exclude_user_id=None, limit=4):
    """Members with the most contributions all-time, for a 'people to check out'
    sidebar widget - excludes the current viewer so it's always someone else."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT u.id, u.display_name, u.department, COUNT(c.id) AS post_count
           FROM users u JOIN contributions c ON c.author_id = u.id
           WHERE u.id != ?
           GROUP BY u.id
           ORDER BY post_count DESC
           LIMIT ?""",
        (exclude_user_id or -1, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def recent_activity(limit=8):
    """Recent ratings across the whole platform, for a live 'community pulse'
    sidebar widget - who rated what, how many stars."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT r.stars, r.created_at, r.updated_at,
                  rater.display_name AS rater_name,
                  c.id AS contribution_id, c.title AS contribution_title
           FROM ratings r
           JOIN users rater ON rater.id = r.rater_id
           JOIN contributions c ON c.id = r.contribution_id
           ORDER BY COALESCE(r.updated_at, r.created_at) DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---- Digest (admin-only announcement cards) -------------------------------------------

def create_digest(author_id, title, body):
    conn = get_conn()
    conn.execute(
        "INSERT INTO digests (author_id, title, body) VALUES (?, ?, ?)",
        (author_id, title, body),
    )
    conn.commit()
    conn.close()


def list_digests(limit=10):
    conn = get_conn()
    rows = conn.execute(
        """SELECT d.*, u.display_name AS author_name
           FROM digests d JOIN users u ON u.id = d.author_id
           ORDER BY d.created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---- Polls (a contribution_type='poll' contribution + its options/votes) ---------------------

def create_poll(author_id, question, description, options):
    """A poll is just a contribution (title=question, body=optional description)
    so it gets the same submission/milestone point bonuses and shows up in the
    same browse feed as prompts/tips - options and votes are the poll-specific part."""
    contribution_id = create_contribution(author_id, "poll", question, description or "", None, None)
    conn = get_conn()
    for position, option_text in enumerate(options):
        conn.execute(
            "INSERT INTO poll_options (contribution_id, option_text, position) VALUES (?, ?, ?)",
            (contribution_id, option_text, position),
        )
    conn.commit()
    conn.close()
    return contribution_id


def get_poll_options(contribution_id):
    conn = get_conn()
    rows = conn.execute(
        """SELECT po.id, po.option_text, po.position,
                  COALESCE((SELECT COUNT(*) FROM poll_votes WHERE option_id = po.id), 0) AS vote_count
           FROM poll_options po
           WHERE po.contribution_id = ?
           ORDER BY po.position""",
        (contribution_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_poll_vote(contribution_id, voter_id):
    conn = get_conn()
    row = conn.execute(
        "SELECT option_id FROM poll_votes WHERE contribution_id = ? AND voter_id = ?",
        (contribution_id, voter_id),
    ).fetchone()
    conn.close()
    return row["option_id"] if row else None


def cast_poll_vote(contribution_id, option_id, voter_id):
    """Upsert, same idiom as upsert_rating - changing your vote updates the
    existing row in place rather than adding a second one for the same voter."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM poll_votes WHERE contribution_id = ? AND voter_id = ?",
        (contribution_id, voter_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE poll_votes SET option_id = ?, created_at = datetime('now') WHERE id = ?",
            (option_id, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO poll_votes (contribution_id, option_id, voter_id) VALUES (?, ?, ?)",
            (contribution_id, option_id, voter_id),
        )
    conn.commit()
    conn.close()


# ---- public profile + badges ----------------------------------------------------------------

def get_user_profile(user_id):
    from datetime import date

    user = get_user_by_id(user_id)
    if not user:
        return None
    stats = user_stats(user_id)
    this_month = date.today().strftime("%Y-%m")
    board = leaderboard_for_month(this_month)
    rank = next((i + 1 for i, row in enumerate(board) if row["user_id"] == user_id), None)

    contributions = stats["contributions"]
    post_count = len(contributions)
    rated_posts = [c for c in contributions if c["rating_count"] > 0]
    avg_rating = (
        sum(c["avg_stars"] for c in rated_posts) / len(rated_posts) if rated_posts else 0
    )

    badges = []
    if post_count >= 1:
        badges.append({"icon": "🌱", "label": "First Post"})
    if post_count >= 5:
        badges.append({"icon": "📚", "label": "Prolific Contributor"})
    if rank == 1:
        badges.append({"icon": "🏆", "label": "Top of the Month"})
    if any(c["rating_count"] >= 5 for c in contributions):
        badges.append({"icon": "🔥", "label": "Community Favorite"})
    if avg_rating >= 4.5 and rated_posts:
        badges.append({"icon": "⭐", "label": "Highly Rated"})

    return {
        "user": user,
        "all_time_points": stats["all_time_points"],
        "month_points": stats["month_points"],
        "contributions": contributions,
        "post_count": post_count,
        "rank_this_month": rank,
        "badges": badges,
    }
