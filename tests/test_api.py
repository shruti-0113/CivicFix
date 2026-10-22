"""API test suite for CivicFix.

Runs on the standard library only:

    python -m unittest discover -s tests -v

Every test uses a throw-away SQLite file, so your development database is
never touched. No network access is required — the Maps and AI features fall
back to their offline engines when no API key is configured.
"""

import os
import sys
import sqlite3
import tempfile
import unittest

# Keep the suite hermetic: ignore any .env file and force the offline rule
# engine so the tests never call a real Maps or LLM provider.
os.environ["CIVICFIX_SKIP_DOTENV"] = "1"
for _key in (
    "GOOGLE_MAPS_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "LLM_API_KEY",
):
    os.environ.pop(_key, None)
os.environ["AI_PROVIDER"] = "auto"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module  # noqa: E402


class CivicFixTestCase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="civicfix-test-")
        app_module.DB = os.path.join(self._tmpdir, "test.db")
        app_module.init_db()
        self._clear_tables()
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()
        self._track_connections()

    def _track_connections(self):
        """Record every connection the handlers open so leaks can be asserted."""
        self._connections = []
        self._real_db = app_module.db

        def tracking_db():
            conn = self._real_db()
            self._connections.append(conn)
            return conn

        app_module.db = tracking_db

    def _clear_tables(self):
        """Drop the demo seed rows so every test starts from an empty board."""
        conn = app_module.db()
        for table in ("comments", "issue_plans", "donations", "expenses", "issues"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()

    def tearDown(self):
        app_module.db = self._real_db
        self.assert_no_leaked_connections()
        app_module.DB = "civicfix.db"
        app_module.init_db()

    def assert_no_leaked_connections(self):
        leaked = []
        for conn in self._connections:
            try:
                conn.execute("SELECT 1")
            except sqlite3.ProgrammingError:
                continue  # properly closed
            except Exception:
                leaked.append(conn)
            else:
                leaked.append(conn)
        self.assertEqual(len(leaked), 0, f"{len(leaked)} database connection(s) were left open")


class TestPagesAndConfig(CivicFixTestCase):
    def test_index_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CivicFix", response.data)
        response.close()

    def test_config_exposes_maps_and_ai(self):
        data = self.client.get("/api/config").get_json()
        self.assertIn("google_maps_key", data)
        self.assertIn("maps", data)
        self.assertIn("ai", data)
        self.assertIn("provider", data["ai"])

    def test_maps_and_ai_status(self):
        self.assertEqual(self.client.get("/api/maps/status").status_code, 200)
        self.assertEqual(self.client.get("/api/ai/status").status_code, 200)

    def test_static_assets_are_served(self):
        for path in ("/app.js", "/style.css", "/manifest.json", "/sw.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            response.close()


class TestIssues(CivicFixTestCase):
    def _sample(self, **overrides):
        payload = {
            "title": "Deep pothole near the school gate",
            "category": "Pothole",
            "description": "A large deep pothole on the main road, bikes fall every day.",
            "location": "Salt Lake, Kolkata",
            "state": "West Bengal",
            "severity": 5,
            "impact": 4,
            "urgency": 5,
        }
        payload.update(overrides)
        return payload

    def test_create_and_read_issue(self):
        response = self.client.post("/api/issues", json=self._sample())
        self.assertEqual(response.status_code, 201)
        issue = response.get_json()
        self.assertEqual(issue["category"], "Pothole")
        self.assertEqual(issue["status"], "Reported")
        self.assertGreater(issue["priority"], 0)

        listing = self.client.get("/api/issues").get_json()
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["title"], issue["title"])

    def test_missing_fields_are_rejected(self):
        response = self.client.post("/api/issues", json={"title": "Only a title"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_invalid_category_is_rejected(self):
        response = self.client.post("/api/issues", json=self._sample(category="Volcano"))
        self.assertEqual(response.status_code, 400)

    def test_out_of_range_scores_are_rejected(self):
        response = self.client.post("/api/issues", json=self._sample(severity=9))
        self.assertEqual(response.status_code, 400)

    def test_status_update_and_upvote(self):
        issue_id = self.client.post("/api/issues", json=self._sample()).get_json()["id"]

        updated = self.client.patch(f"/api/issues/{issue_id}", json={"status": "In Progress"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["status"], "In Progress")

        voted = self.client.post(f"/api/issues/{issue_id}/upvote").get_json()
        self.assertEqual(voted["upvotes"], 1)

    def test_filter_by_category_and_status(self):
        self.client.post("/api/issues", json=self._sample())
        self.client.post("/api/issues", json=self._sample(title="Overflowing bin", category="Garbage"))
        self.assertEqual(len(self.client.get("/api/issues?category=Garbage").get_json()), 1)
        self.assertEqual(len(self.client.get("/api/issues?status=Resolved").get_json()), 0)

    def test_search_matches_title(self):
        self.client.post("/api/issues", json=self._sample())
        results = self.client.get("/api/issues?q=pothole").get_json()
        self.assertEqual(len(results), 1)

    def test_delete_issue(self):
        issue_id = self.client.post("/api/issues", json=self._sample()).get_json()["id"]
        self.assertEqual(self.client.delete(f"/api/issues/{issue_id}").status_code, 200)
        self.assertEqual(len(self.client.get("/api/issues").get_json()), 0)

    def test_comments(self):
        issue_id = self.client.post("/api/issues", json=self._sample()).get_json()["id"]
        created = self.client.post(
            f"/api/issues/{issue_id}/comments", json={"author": "Asha", "message": "Please fix soon"}
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(len(self.client.get(f"/api/issues/{issue_id}/comments").get_json()), 1)

    def test_empty_comment_is_rejected(self):
        issue_id = self.client.post("/api/issues", json=self._sample()).get_json()["id"]
        self.assertEqual(
            self.client.post(f"/api/issues/{issue_id}/comments", json={"message": "  "}).status_code, 400
        )


class TestCommunityFund(CivicFixTestCase):
    def test_donation_and_expense_flow(self):
        issue_id = self.client.post("/api/issues", json={
            "title": "Broken streetlight", "category": "Streetlight", "description": "Dark road",
            "location": "New Town", "severity": 3, "impact": 3, "urgency": 3,
        }).get_json()["id"]

        donation = self.client.post("/api/donations", json={"amount": 500, "donor_name": "Ravi"})
        self.assertEqual(donation.status_code, 201)

        expense = self.client.post("/api/expenses", json={
            "title": "LED lamp", "category": "Materials", "amount": 350, "issue_id": issue_id,
        })
        self.assertEqual(expense.status_code, 201)

        funds = self.client.get("/api/funds").get_json()
        self.assertGreaterEqual(funds["total_raised"], 500)
        self.assertGreaterEqual(funds["total_spent"], 350)

    def test_invalid_amount_is_rejected(self):
        self.assertEqual(self.client.post("/api/donations", json={"amount": -5}).status_code, 400)


class TestAiFeatures(CivicFixTestCase):
    def test_triage_scores_a_report(self):
        response = self.client.post("/api/ai/triage", json={
            "title": "Deep pothole on the main road",
            "description": "Huge deep pothole near a school, bikes fall every day",
            "location": "Salt Lake, Kolkata",
        })
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["category"], "Pothole")
        for key in ("severity", "impact", "urgency"):
            self.assertGreaterEqual(data[key], 1)
            self.assertLessEqual(data[key], 5)
        self.assertGreater(data["priority"], 0)

    def test_triage_requires_content(self):
        self.assertEqual(self.client.post("/api/ai/triage", json={}).status_code, 400)

    def test_resolution_plan_is_generated_and_saved(self):
        issue_id = self.client.post("/api/issues", json={
            "title": "Overflowing garbage bin", "category": "Garbage",
            "description": "Garbage not collected for days", "location": "Salt Lake",
            "severity": 3, "impact": 4, "urgency": 4,
        }).get_json()["id"]

        response = self.client.post("/api/ai/resolve", json={"issue_id": issue_id})
        self.assertEqual(response.status_code, 200)
        plan = response.get_json()
        self.assertTrue(plan["steps"])
        self.assertTrue(plan["materials"])
        self.assertTrue(plan["department"])

        conn = app_module.db()
        try:
            saved = conn.execute(
                "SELECT COUNT(*) FROM issue_plans WHERE issue_id=?", (issue_id,)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(saved, 1)

    def test_resolve_rejects_unknown_issue(self):
        self.assertEqual(self.client.post("/api/ai/resolve", json={"issue_id": 9999}).status_code, 404)

    def test_assistant_answers_from_live_data(self):
        self.client.post("/api/issues", json={
            "title": "Large pothole near college gate", "category": "Pothole",
            "description": "Deep pit", "location": "Kolkata", "severity": 5, "impact": 5, "urgency": 5,
        })
        response = self.client.post("/api/ai/assistant", json={"message": "What should we fix first?"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["answer"])
        self.assertIn("source", data)

    def test_assistant_rejects_empty_message(self):
        self.assertEqual(self.client.post("/api/ai/assistant", json={"message": "  "}).status_code, 400)

    def test_insights_are_built_from_analytics(self):
        response = self.client.get("/api/ai/insights")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data["insights"])
        self.assertIn("headline", data)

    def test_describe_rewrites_a_rough_note(self):
        response = self.client.post("/api/ai/describe", json={
            "description": "street light not working near gate", "category": "Streetlight",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["description"])


class TestMapsEndpoints(CivicFixTestCase):
    """These validate the request handling only — no external calls are made."""

    def test_geocode_requires_a_query(self):
        self.assertEqual(self.client.get("/api/maps/geocode").status_code, 400)

    def test_directions_require_both_points(self):
        self.assertEqual(self.client.get("/api/maps/directions?origin=1,2").status_code, 400)

    def test_nearby_requires_coordinates(self):
        self.assertEqual(self.client.get("/api/maps/nearby?lat=abc").status_code, 400)

    def test_latlng_parser(self):
        self.assertEqual(app_module._parse_latlng("22.57,88.36"), (22.57, 88.36))
        self.assertIsNone(app_module._parse_latlng("nonsense"))
        self.assertIsNone(app_module._parse_latlng("1,2,3"))


if __name__ == "__main__":
    unittest.main()
