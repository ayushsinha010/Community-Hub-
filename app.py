import os
import re
import secrets

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db



def load_secret_key():
    """Session-signing key: AI_HUB_SECRET_KEY if set, otherwise a random key that is
    generated on first start and kept in data/.secret_key (git-ignored) so that
    logins survive restarts. No key is hard-coded in the source."""
    key = os.environ.get("AI_HUB_SECRET_KEY")
    if key:
        return key
    path = os.path.join(db.DATA_DIR, ".secret_key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(db.DATA_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(key)
    except OSError:
        pass  # read-only folder: fall back to a per-process key (logins reset on restart)
    return key


app = Flask(__name__)
app.secret_key = load_secret_key()
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Shown only as a fallback label where a user has no department on record.
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Your Company")

db.init_db()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.before_request
def require_login():
    if request.endpoint in ("login", "register", "static") or request.path.startswith("/static/"):
        return None
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))
    if not db.get_user_by_id(user_id):
        # Session points at a user that no longer exists (e.g. the dev database
        # was reset while this browser still had a session cookie) - clear it
        # instead of letting every template that touches current_user crash.
        session.pop("user_id", None)
        return redirect(url_for("login"))
    db.close_previous_month_if_needed()
    return None


@app.context_processor
def inject_current_user():
    user_id = session.get("user_id")
    return {
        "current_user": db.get_user_by_id(user_id) if user_id else None,
        "current_user_score": db.user_total_points(user_id) if user_id else None,
        "current_user_rank": db.current_user_rank(user_id) if user_id else None,
        "contribution_types": db.CONTRIBUTION_TYPES,
        "company_name": COMPANY_NAME,
    }


@app.route("/register", methods=["GET", "POST"])
def register():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        display_name = request.form.get("display_name", "").strip()
        department = request.form.get("department", "").strip()
        password = request.form.get("password", "")
        if not EMAIL_RE.match(email):
            error = "Enter a valid email address."
        elif not email.endswith("@" + db.ALLOWED_EMAIL_DOMAIN):
            error = f"Registration is limited to @{db.ALLOWED_EMAIL_DOMAIN} email addresses."
        elif not display_name:
            error = "Enter your name."
        elif not department:
            error = "Enter your department."
        elif len(password) < 6:
            error = "Password must be at least 6 characters."
        elif db.get_user_by_email(email):
            error = "An account with that email already exists - try signing in instead."
        else:
            user_id = db.create_user(email, display_name, generate_password_hash(password), department)
            session["user_id"] = user_id
            return redirect(url_for("home"))
    return render_template("register.html", error=error, departments=db.distinct_departments())


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.get_user_by_email(email)
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
        error = "Invalid email or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))


@app.route("/")
def home():
    from datetime import date

    contribution_type = request.args.get("type") or None
    tool = request.args.get("tool") or None
    category = request.args.get("category") or None
    department = request.args.get("department") or None
    query = request.args.get("q", "").strip() or None
    sort = request.args.get("sort", "newest")

    contributions = db.list_contributions(
        contribution_type=contribution_type, tool=tool, category=category,
        department=department, query=query, sort=sort,
    )
    board = db.leaderboard_for_month(date.today().strftime("%Y-%m"))
    return render_template(
        "home.html",
        contributions=contributions,
        tools=db.distinct_values("tool"),
        categories=db.distinct_values("category"),
        departments=db.distinct_departments(),
        selected_type=contribution_type,
        selected_tool=tool,
        selected_category=category,
        selected_department=department,
        query=query or "",
        sort=sort,
        sidebar_board=board[:5],
        spotlight=db.get_user_profile(board[0]["user_id"]) if board else None,
        popular_tags=db.popular_tags(),
        recent_activity=db.recent_activity(),
        trending=db.trending_this_week(),
        active_contributors=db.active_contributors(exclude_user_id=session.get("user_id")),
        digests=db.list_digests(limit=8),
    )


@app.route("/digest/new", methods=["GET", "POST"])
def new_digest():
    user = db.get_user_by_id(session["user_id"])
    if not user["is_admin"]:
        abort(403)
    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        if not title:
            error = "Give the digest a title."
        elif not body:
            error = "Write the digest body."
        else:
            db.create_digest(session["user_id"], title, body)
            return redirect(url_for("home"))
    return render_template("digest_form.html", error=error, form=request.form)


@app.route("/prompts")
def prompt_library():
    contributions = db.list_contributions(contribution_type="ai_prompt", sort="newest")
    return render_template("library.html", page_title="Prompt Library", contributions=contributions)


@app.route("/tips")
def tip_library():
    contributions = db.list_contributions(contribution_type="productivity_tip", sort="newest")
    return render_template("library.html", page_title="Tip/Suggestion Library", contributions=contributions)


@app.route("/scoring")
def scoring_info():
    return render_template("scoring.html", rules=db.SCORING_RULES)


@app.route("/submit", methods=["GET", "POST"])
def submit():
    error = None
    if request.method == "POST":
        contribution_type = request.form.get("contribution_type", "")
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        tool = request.form.get("tool", "").strip()
        category = request.form.get("category", "").strip()

        if contribution_type not in db.CONTRIBUTION_TYPES:
            error = "Choose what you're sharing."
        elif not title:
            error = "Give the poll a question." if contribution_type == "poll" else "Give it a short title."
        elif contribution_type == "poll":
            options = [o.strip() for o in request.form.getlist("option") if o.strip()]
            if len(options) < 2:
                error = "A poll needs at least 2 options."
            elif len(options) > 6:
                error = "A poll can have at most 6 options."
            else:
                contribution_id = db.create_poll(session["user_id"], title, body, options)
                return redirect(url_for("contribution_detail", contribution_id=contribution_id))
        elif not body:
            error = "Fill in the prompt text or tip description."
        else:
            contribution_id = db.create_contribution(
                session["user_id"], contribution_type, title, body, tool, category
            )
            return redirect(url_for("contribution_detail", contribution_id=contribution_id))
    return render_template(
        "submit.html",
        error=error,
        tools=db.distinct_values("tool"),
        categories=db.distinct_values("category"),
        form=request.form,
        initial_type=request.args.get("type", ""),
    )


@app.route("/contribution/<int:contribution_id>")
def contribution_detail(contribution_id):
    contribution = db.get_contribution(contribution_id)
    if not contribution:
        abort(404)
    summary = db.contribution_rating_summary(contribution_id)
    ratings = db.list_ratings_for_contribution(contribution_id)
    my_rating = db.get_rating(contribution_id, session["user_id"])
    comments = db.list_comments(contribution_id)

    poll_options = None
    my_vote = None
    poll_total_votes = 0
    if contribution["contribution_type"] == "poll":
        poll_options = db.get_poll_options(contribution_id)
        my_vote = db.get_poll_vote(contribution_id, session["user_id"])
        poll_total_votes = sum(o["vote_count"] for o in poll_options)

    return render_template(
        "contribution_detail.html",
        contribution=contribution,
        summary=summary,
        ratings=ratings,
        my_rating=my_rating,
        comments=comments,
        is_author=contribution["author_id"] == session["user_id"],
        rate_error=request.args.get("rate_error"),
        poll_options=poll_options,
        my_vote=my_vote,
        poll_total_votes=poll_total_votes,
    )


@app.route("/contribution/<int:contribution_id>/vote", methods=["POST"])
def vote_poll(contribution_id):
    contribution = db.get_contribution(contribution_id)
    if not contribution or contribution["contribution_type"] != "poll":
        abort(404)
    option_id = request.form.get("option_id", type=int)
    if option_id:
        db.cast_poll_vote(contribution_id, option_id, session["user_id"])
    return redirect(url_for("contribution_detail", contribution_id=contribution_id))


@app.route("/contribution/<int:contribution_id>/rate", methods=["POST"])
def rate_contribution(contribution_id):
    stars = request.form.get("stars", type=int)
    comment = request.form.get("comment", "").strip()
    try:
        db.upsert_rating(contribution_id, session["user_id"], stars, comment)
    except ValueError as e:
        return redirect(url_for("contribution_detail", contribution_id=contribution_id, rate_error=str(e)))
    return redirect(url_for("contribution_detail", contribution_id=contribution_id))


@app.route("/contribution/<int:contribution_id>/comment", methods=["POST"])
def comment_on_contribution(contribution_id):
    contribution = db.get_contribution(contribution_id)
    if not contribution:
        abort(404)
    body = request.form.get("body", "").strip()
    if body:
        db.add_comment(contribution_id, session["user_id"], body)
    return redirect(url_for("contribution_detail", contribution_id=contribution_id) + "#comments")


@app.route("/user/<int:user_id>")
def user_profile(user_id):
    profile = db.get_user_profile(user_id)
    if not profile:
        abort(404)
    return render_template("user_profile.html", profile=profile)


@app.route("/contribution/<int:contribution_id>/edit", methods=["GET", "POST"])
def edit_contribution(contribution_id):
    contribution = db.get_contribution(contribution_id)
    if not contribution:
        abort(404)
    if contribution["author_id"] != session["user_id"]:
        abort(403)

    error = None
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        body = request.form.get("body", "").strip()
        tool = request.form.get("tool", "").strip()
        category = request.form.get("category", "").strip()
        if not title or not body:
            error = "Title and body can't be empty."
        else:
            db.update_contribution(contribution_id, title, body, tool, category)
            return redirect(url_for("contribution_detail", contribution_id=contribution_id))
    return render_template(
        "edit_contribution.html",
        contribution=contribution,
        error=error,
        tools=db.distinct_values("tool"),
        categories=db.distinct_values("category"),
    )


@app.route("/leaderboard")
def leaderboard():
    from datetime import date

    year_month = request.args.get("month") or date.today().strftime("%Y-%m")
    board = db.leaderboard_for_month(year_month)
    return render_template(
        "leaderboard.html",
        board=board,
        year_month=year_month,
        current_month=date.today().strftime("%Y-%m"),
        past_winners=db.get_monthly_winners(),
    )


@app.route("/me")
def me():
    stats = db.user_stats(session["user_id"])
    return render_template("me.html", stats=stats)


@app.errorhandler(404)
def not_found(_error):
    return render_template("error.html", title="Page not found", message="That page doesn't exist."), 404


@app.errorhandler(403)
def forbidden(_error):
    return render_template("error.html", title="Not allowed", message="You don't have access to that."), 403


if __name__ == "__main__":
    app.run(
        debug=os.environ.get("AI_HUB_DEBUG", "1") == "1",
        port=int(os.environ.get("AI_HUB_PORT", "5050")),
    )
