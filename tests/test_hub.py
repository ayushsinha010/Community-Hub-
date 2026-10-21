"""Smoke and scoring tests for AI Hub.

Run from the project root:

    python -m unittest discover -s tests -v

The tests use a throwaway SQLite database in a temporary folder (data/hub.db is
never touched) and the Flask test client, so no server needs to be running.

Note: month-based checks compare the local calendar month (date.today()) with
SQLite's UTC timestamps, exactly like the app does. Do not run the suite within a
few hours of midnight on the last day of a month in a non-UTC timezone.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ["AI_HUB_ADMIN_EMAILS"] = "boss@example.com"
os.environ["AI_HUB_EMAIL_DOMAIN"] = "example.com"

import db  # noqa: E402

_TMP = tempfile.TemporaryDirectory()
db.DATA_DIR = _TMP.name
db.DB_PATH = os.path.join(_TMP.name, "test.db")

import app as hub  # noqa: E402


def tearDownModule():
    _TMP.cleanup()


def _prev_year_month():
    today = date.today()
    return (date(today.year, today.month, 1) - timedelta(days=1)).strftime("%Y-%m")


class HubTestCase(unittest.TestCase):
    def setUp(self):
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)
        db.init_db()

    # helpers ---------------------------------------------------------------
    def register(self, email, name="Test User", department="Engineering", password="secret123"):
        client = hub.app.test_client()
        resp = client.post(
            "/register",
            data={"email": email, "display_name": name, "department": department, "password": password},
        )
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True)[:300])
        return client, db.get_user_by_email(email)["id"]

    def post_prompt(self, client, title="A prompt", body="Prompt text", tool="Tool", category="Cat"):
        resp = client.post(
            "/submit",
            data={"contribution_type": "ai_prompt", "title": title, "body": body, "tool": tool, "category": category},
        )
        self.assertEqual(resp.status_code, 302)
        return int(resp.headers["Location"].rstrip("/").split("/")[-1])

    def rate(self, client, contribution_id, stars):
        return client.post(f"/contribution/{contribution_id}/rate", data={"stars": stars, "comment": ""})

    def events(self, user_id, event_type=None):
        conn = db.get_conn()
        sql = "SELECT * FROM point_events WHERE author_id = ?"
        params = [user_id]
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return rows


class PagesAndAuthTests(HubTestCase):
    def test_empty_database_pages(self):
        anon = hub.app.test_client()
        self.assertEqual(anon.get("/login").status_code, 200)
        self.assertEqual(anon.get("/register").status_code, 200)
        for path in ("/", "/leaderboard", "/prompts", "/tips", "/scoring", "/me"):
            resp = anon.get(path)
            self.assertEqual(resp.status_code, 302, path)
            self.assertIn("/login", resp.headers["Location"])

        client, _ = self.register("first@example.com")
        for path in ("/", "/leaderboard", "/prompts", "/tips", "/scoring", "/me", "/submit"):
            self.assertEqual(client.get(path).status_code, 200, path)

    def test_registration_validation(self):
        client = hub.app.test_client()
        base = {"display_name": "X", "department": "Eng", "password": "secret123"}
        resp = client.post("/register", data={**base, "email": "x@mail.example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("limited to @example.com", resp.get_data(as_text=True))
        resp = client.post("/register", data={**base, "email": "x@example.com", "password": "123"})
        self.assertIn("at least 6 characters", resp.get_data(as_text=True))
        self.register("x@example.com")
        resp = client.post("/register", data={**base, "email": "X@Example.com"})
        self.assertIn("already exists", resp.get_data(as_text=True))

    def test_login_and_logout(self):
        self.register("login@example.com", password="secret123")
        client = hub.app.test_client()
        bad = client.post("/login", data={"email": "login@example.com", "password": "wrong"})
        self.assertEqual(bad.status_code, 200)
        self.assertIn("Invalid email or password", bad.get_data(as_text=True))
        ok = client.post("/login", data={"email": "LOGIN@example.com", "password": "secret123"})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(client.get("/").status_code, 200)
        client.get("/logout")
        self.assertEqual(client.get("/").status_code, 302)

    def test_stale_session_is_cleared(self):
        client, user_id = self.register("stale@example.com")
        conn = db.get_conn()
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        resp = client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.headers["Location"])

    def test_digest_is_admin_only(self):
        boss, _ = self.register("boss@example.com", name="Boss")
        user, _ = self.register("user@example.com", name="User")
        self.assertEqual(user.get("/digest/new").status_code, 403)
        self.assertEqual(boss.get("/digest/new").status_code, 200)
        resp = boss.post("/digest/new", data={"title": "Hello", "body": "World"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("Hello", user.get("/").get_data(as_text=True))


class SecretKeyTests(unittest.TestCase):
    def test_key_comes_from_environment_or_a_generated_file(self):
        from unittest import mock

        with mock.patch.dict(os.environ, {"AI_HUB_SECRET_KEY": "from-env"}):
            self.assertEqual(hub.load_secret_key(), "from-env")
        env = {k: v for k, v in os.environ.items() if k != "AI_HUB_SECRET_KEY"}
        with mock.patch.dict(os.environ, env, clear=True):
            first = hub.load_secret_key()
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(hub.load_secret_key(), first)  # persisted, stable across calls
            self.assertTrue(os.path.exists(os.path.join(db.DATA_DIR, ".secret_key")))


class ScoringTests(HubTestCase):
    def test_submission_first_of_month_and_milestone_bonuses(self):
        client, uid = self.register("author@example.com", name="Author")
        self.post_prompt(client, "one")
        self.assertEqual(db.user_total_points(uid), 2 + 3)  # submission + first post of the month
        self.post_prompt(client, "two")
        self.assertEqual(db.user_total_points(uid), 5 + 2)
        self.post_prompt(client, "three")
        self.assertEqual(db.user_total_points(uid), 7 + 2 + 5)  # submission + every-3rd-post milestone
        self.assertEqual(len(self.events(uid, "first_post_of_month_bonus")), 1)
        self.assertEqual(len(self.events(uid, "milestone_bonus")), 1)

    def test_rating_points_equal_stars_and_rerate_updates_in_place(self):
        author, aid = self.register("author@example.com", name="Author")
        rater, _ = self.register("rater@example.com", name="Rater")
        cid = self.post_prompt(author)
        self.assertEqual(db.user_total_points(aid), 5)
        self.rate(rater, cid, 4)
        self.assertEqual(db.user_total_points(aid), 9)
        self.rate(rater, cid, 2)  # edit the rating
        self.assertEqual(db.user_total_points(aid), 7)
        self.assertEqual(len(self.events(aid, "rating")), 1)
        self.assertEqual(db.contribution_rating_summary(cid)["rating_count"], 1)

    def test_self_rating_and_out_of_range_are_rejected(self):
        author, aid = self.register("author@example.com", name="Author")
        rater, _ = self.register("rater@example.com", name="Rater")
        cid = self.post_prompt(author)
        resp = self.rate(author, cid, 5)
        self.assertIn("rate_error", resp.headers["Location"])
        resp = self.rate(rater, cid, 9)
        self.assertIn("rate_error", resp.headers["Location"])
        self.assertEqual(db.user_total_points(aid), 5)

    def test_consistency_bonus_is_awarded_once(self):
        author, aid = self.register("author@example.com", name="Author")
        raters = [self.register(f"r{i}@example.com", name=f"Rater {i}")[0] for i in range(2)]
        ids = [self.post_prompt(author, f"post {i}") for i in range(3)]
        for cid in ids:
            resp = self.rate(raters[0], cid, 5)
            self.assertEqual(resp.status_code, 302)  # regression: this used to be a 500 on the 3rd rated post
        self.assertEqual(len(self.events(aid, "consistency_bonus")), 1)
        # 3 posts (14) + three 5-star ratings (15) + consistency bonus (10)
        self.assertEqual(db.user_total_points(aid), 14 + 15 + 10)
        self.rate(raters[1], ids[0], 5)
        self.assertEqual(len(self.events(aid, "consistency_bonus")), 1)

    def test_consistency_bonus_needs_four_star_average_on_last_three_rated_posts(self):
        author, aid = self.register("author@example.com", name="Author")
        rater, _ = self.register("rater@example.com", name="Rater")
        ids = [self.post_prompt(author, f"post {i}") for i in range(3)]
        for cid, stars in zip(ids, (5, 5, 3)):
            self.rate(rater, cid, stars)
        self.assertEqual(len(self.events(aid, "consistency_bonus")), 0)
        self.rate(rater, ids[2], 4)  # lifting the weakest post to 4 stars completes the streak
        self.assertEqual(len(self.events(aid, "consistency_bonus")), 1)

    def test_leaderboard_orders_by_monthly_points(self):
        a, aid = self.register("a@example.com", name="Alpha")
        b, bid = self.register("b@example.com", name="Bravo")
        self.post_prompt(a)
        cid = self.post_prompt(b)
        self.rate(a, cid, 5)
        board = db.leaderboard_for_month(date.today().strftime("%Y-%m"))
        self.assertEqual([row["user_id"] for row in board], [bid, aid])
        self.assertEqual([row["total_points"] for row in board], [10, 5])
        self.assertEqual(db.current_user_rank(bid), 1)
        self.assertEqual(db.current_user_rank(aid), 2)
        self.assertEqual(a.get("/leaderboard").status_code, 200)
        self.assertEqual(a.get("/leaderboard?month=2020-01").status_code, 200)

    def test_previous_month_winner_is_snapshotted_once(self):
        _, uid = self.register("winner@example.com", name="Winner")
        _, other = self.register("other@example.com", name="Other")
        prev = _prev_year_month()
        conn = db.get_conn()
        cur = conn.execute(
            "INSERT INTO contributions (author_id, contribution_type, title, body) VALUES (?, 'ai_prompt', 't', 'b')",
            (uid,),
        )
        cid = cur.lastrowid
        for author, points in ((uid, 9), (other, 4)):
            conn.execute(
                "INSERT INTO point_events (contribution_id, author_id, event_type, points, created_at) "
                "VALUES (?, ?, 'rating', ?, ?)",
                (cid, author, points, f"{prev}-15 12:00:00"),
            )
        conn.commit()
        conn.close()
        db.close_previous_month_if_needed()
        db.close_previous_month_if_needed()
        winners = db.get_monthly_winners()
        self.assertEqual(len(winners), 1)
        self.assertEqual((winners[0]["year_month"], winners[0]["user_id"], winners[0]["total_points"]), (prev, uid, 9))


class PollTests(HubTestCase):
    def test_poll_create_and_change_vote(self):
        author, aid = self.register("author@example.com", name="Author")
        voter, _ = self.register("voter@example.com", name="Voter")
        resp = author.post(
            "/submit",
            data={"contribution_type": "poll", "title": "Best tool?", "body": "", "option": ["A", "B", " ", "C"]},
        )
        self.assertEqual(resp.status_code, 302)
        cid = int(resp.headers["Location"].rstrip("/").split("/")[-1])
        self.assertEqual(db.user_total_points(aid), 5)  # polls earn the same post bonuses
        options = db.get_poll_options(cid)
        self.assertEqual([o["option_text"] for o in options], ["A", "B", "C"])
        voter.post(f"/contribution/{cid}/vote", data={"option_id": options[0]["id"]})
        voter.post(f"/contribution/{cid}/vote", data={"option_id": options[1]["id"]})
        counts = {o["option_text"]: o["vote_count"] for o in db.get_poll_options(cid)}
        self.assertEqual(counts, {"A": 0, "B": 1, "C": 0})
        self.assertEqual(voter.get(f"/contribution/{cid}").status_code, 200)

    def test_poll_needs_two_options(self):
        author, _ = self.register("author@example.com", name="Author")
        resp = author.post("/submit", data={"contribution_type": "poll", "title": "Q?", "option": ["only one"]})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("at least 2 options", resp.get_data(as_text=True))

    def test_poll_allows_at_most_six_options(self):
        author, _ = self.register("author@example.com", name="Author")
        resp = author.post(
            "/submit",
            data={"contribution_type": "poll", "title": "Q?", "option": [f"opt {i}" for i in range(7)]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("at most 6 options", resp.get_data(as_text=True))


class ContributionTests(HubTestCase):
    def test_detail_edit_comment_and_profile(self):
        author, aid = self.register("author@example.com", name="Author")
        other, _ = self.register("other@example.com", name="Other")
        cid = self.post_prompt(author, "Original")
        self.assertEqual(other.get(f"/contribution/{cid}").status_code, 200)
        self.assertEqual(other.get(f"/contribution/{cid}/edit").status_code, 403)
        resp = author.post(
            f"/contribution/{cid}/edit",
            data={"title": "Changed", "body": "New body", "tool": "T", "category": "C"},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(db.get_contribution(cid)["title"], "Changed")
        other.post(f"/contribution/{cid}/comment", data={"body": "Nice one"})
        self.assertIn("Nice one", other.get(f"/contribution/{cid}").get_data(as_text=True))
        self.assertEqual(other.get(f"/user/{aid}").status_code, 200)
        self.assertEqual(other.get("/user/9999").status_code, 404)
        self.assertEqual(other.get("/contribution/9999").status_code, 404)

    def test_filters_search_and_sort(self):
        author, _ = self.register("author@example.com", name="Author", department="Design")
        other, _ = self.register("other@example.com", name="Other")
        first = self.post_prompt(author, "Alpha prompt", tool="ToolA", category="Writing")
        self.post_prompt(author, "Beta prompt", tool="ToolB", category="Coding")
        self.rate(other, first, 5)
        page = other.get("/?tool=ToolA").get_data(as_text=True)
        self.assertIn("Alpha prompt", page)
        self.assertNotIn("Beta prompt", page)
        self.assertIn("Beta prompt", other.get("/?q=beta").get_data(as_text=True))
        self.assertIn("Alpha prompt", other.get("/?department=Design&sort=top_rated").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
