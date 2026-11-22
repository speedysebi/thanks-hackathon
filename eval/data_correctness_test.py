"""Regression checks for follow-ups, partial edits and concurrent retries.

Run: python -m eval.data_correctness_test (temporary databases, no paid AI calls).
"""
import copy
import gc
import os
import sqlite3
import tempfile
import threading
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, db, extract, llm
from app.main import app


class DataCorrectnessTests(unittest.TestCase):
    def setUp(self):
        # Existing DB helpers let GC close SQLite connections. Keep their warnings
        # out of this regression suite and collect before Windows removes the files.
        warnings.filterwarnings("ignore", category=ResourceWarning)
        self.tmp = tempfile.TemporaryDirectory()
        self.old_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "regression.db"
        self.env = patch.dict(os.environ, {
            "ADMIN_PASSWORD": "regression-admin",
            "ADMIN_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
            "SESSION_SECRET": "data-correctness-tests-only",
        })
        self.env.start()
        self.no_ai = patch.object(llm, "_client", None)
        self.no_ai.start()
        self.client = TestClient(app)
        self.client.cookies.set(auth.COOKIE, auth.make_session(0, 1, mfa_ok=True))

    def tearDown(self):
        self.client.close()
        self.no_ai.stop()
        self.env.stop()
        db._ready.discard(str(db.DB_PATH))
        db.DB_PATH = self.old_path
        gc.collect()
        self.tmp.cleanup()

    def entry(self, **changes):
        fields = extract._clean(extract.keyword_extract("fed 10 people including 4 kids"))
        return db.insert({**fields, "worker": "Tester", "channel": "web", "raw_text": "fed 10 people",
                          "people_served": 10, "youth_served": 4, **changes})

    def test_followups_survive_recent_limit_and_date_filters(self):
        old = (date.today() - timedelta(days=200)).isoformat()
        pending = [self.entry(activity_date=old, outcome="needs_follow_up", follow_up=f"Call back {i}")
                   for i in range(10)]
        for _ in range(301):
            self.entry(outcome="resolved")
        recent = self.client.get("/api/entries?days=365&limit=300").json()
        self.assertTrue({e["id"] for e in pending}.isdisjoint(e["id"] for e in recent))
        followups = self.client.get("/api/followups")
        self.assertEqual(followups.status_code, 200)
        self.assertEqual({e["id"] for e in followups.json()}, {e["id"] for e in pending})
        self.assertTrue(all(e["activity_date"] == old and "links" in e for e in followups.json()))
        self.client.patch(f"/api/entries/{pending[0]['id']}", json={"outcome": "resolved"}).raise_for_status()
        self.assertEqual(len(self.client.get("/api/followups").json()), 9)

    def test_followup_worker_filter_and_sensitive_calendar(self):
        self.entry(worker="José", outcome="needs_follow_up", sensitive=True,
                   follow_up="Private case details", activity_date="2026-01-01")
        self.entry(worker="Other", outcome="needs_follow_up")
        events = self.client.get("/api/followups", params={"worker": "JOSÉ"}).json()
        self.assertEqual(len(events), 1)
        self.assertNotIn("Private case details", events[0]["title"] + events[0]["details"])

    def test_edits_scrub_all_writable_text_fields(self):
        entry = self.entry()
        pii = "Ms. Johnson at 2411 N 35th St, 414-555-0199, helper@example.com"
        response = self.client.patch(f"/api/entries/{entry['id']}", json={
            "summary": pii, "follow_up": pii, "neighborhood": "2411 N 35th St",
        })
        self.assertEqual(response.status_code, 200)
        stored = db.get(entry["id"])
        for field in ("summary", "follow_up", "neighborhood"):
            self.assertNotIn("2411", stored[field])
        self.assertNotIn("Johnson", stored["summary"])
        self.assertNotIn("555-0199", stored["follow_up"])
        self.assertNotIn("example.com", stored["summary"])
        self.assertIn("[phone]", response.json()["summary"])

    def test_partial_edits_cannot_break_counts_or_partially_save(self):
        entry = self.entry()
        url = f"/api/entries/{entry['id']}"
        for change in ({"youth_served": 11}, {"people_served": 3},
                       {"people_served": 2, "youth_served": 3, "summary": "Should not be saved"}):
            response = self.client.patch(url, json=change)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(db.get(entry["id"]), entry)
        response = self.client.patch(url, json={"people_served": 3, "youth_served": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["people_served"], response.json()["youth_served"]), (3, 3))

    def test_concurrent_partial_edits_validate_committed_counts(self):
        entry = self.entry()
        barrier = threading.Barrier(2)

        def edit(change):
            barrier.wait(timeout=10)
            try:
                db.update(entry["id"], change)
                return True
            except db.InvalidEntryCounts:
                return False

        # Each patch is valid against (10 people, 4 youth), but together (5, 8) is invalid.
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, [{"people_served": 5}, {"youth_served": 8}]))
        self.assertEqual(sum(results), 1)
        saved = db.get(entry["id"])
        self.assertLessEqual(saved["youth_served"], saved["people_served"])

    def test_unauthorized_edits_still_rejected(self):
        entry = self.entry()
        with TestClient(app) as anon:
            self.assertEqual(anon.patch(f"/api/entries/{entry['id']}", json={"summary": "Changed"}).status_code, 403)
        self.assertEqual(db.get(entry["id"]), entry)

    def test_simultaneous_retries_save_one_row_and_return_same_entry(self):
        db.connect().close()  # both requests must reach extraction before either inserts
        barrier = threading.Barrier(4)
        fields = extract._clean(extract.keyword_extract("fed 12 kids at sherman park"))

        def slow_extract(_):
            barrier.wait(timeout=10)
            return copy.deepcopy(fields)

        def send(_):
            with TestClient(app) as client:
                return client.post("/api/log", json={"text": "fed 12 kids at sherman park",
                                                     "worker": "Retry", "client_id": "race-note-12345"})

        with patch.object(extract, "extract", side_effect=slow_extract), ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(send, range(4)))
        self.assertEqual([r.status_code for r in responses], [200] * 4)
        bodies = [r.json() for r in responses]
        self.assertEqual(len({b["entry"]["id"] for b in bodies}), 1)
        self.assertEqual(len(db.list_entries()), 1)
        self.assertEqual(sum(b["entry"].get("duplicate", False) for b in bodies), 3)
        self.assertEqual(sum(e["people_served"] for e in db.list_entries()), 12)
        self.assertEqual(len({b["undo_token"] for b in bodies}), 1)

    def test_retry_from_another_account_does_not_expose_entry(self):
        entry = self.entry(client_id="private-client-123", user_id=123)
        with TestClient(app) as anon:
            response = anon.post("/api/log", json={"text": "unrelated words", "client_id": "private-client-123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["entry"]["id"], None)
        self.assertEqual(response.json()["undo_token"], None)
        self.assertNotIn(entry["summary"], response.text)

    def test_concurrent_id_conflict_does_not_expose_another_accounts_entry(self):
        users = [db.create_user(name, "unused-test-hash") for name in ("Dee", "Vaun")]
        barrier = threading.Barrier(2)
        fields = extract._clean(extract.keyword_extract("fed 12 kids at sherman park"))

        def slow_extract(_):
            barrier.wait(timeout=10)
            return copy.deepcopy(fields)

        def send(user):
            with TestClient(app) as client:
                client.cookies.set(auth.COOKIE, auth.make_session(user["id"], 1))
                response = client.post("/api/log", json={"text": "fed 12 kids at sherman park",
                                                        "client_id": "shared-id-conflict-123"})
                return user["id"], response

        with patch.object(extract, "extract", side_effect=slow_extract), ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(send, users))
        saved = db.list_entries()
        self.assertEqual(len(saved), 1)
        for uid, response in responses:
            self.assertEqual(response.status_code, 200)
            body = response.json()
            if uid == saved[0]["user_id"]:
                self.assertEqual(body["entry"]["id"], saved[0]["id"])
                self.assertTrue(body["undo_token"])
            else:
                self.assertIsNone(body["entry"]["id"])
                self.assertIsNone(body["undo_token"])
                self.assertNotIn("raw_text", body["entry"])

    def test_null_client_ids_allow_distinct_entries(self):
        self.assertNotEqual(self.entry()["id"], self.entry()["id"])
        self.assertEqual(len(db.list_entries()), 2)

    def test_schema_initializes_safely_for_concurrent_first_connections(self):
        barrier = threading.Barrier(4)

        def initialize(_):
            barrier.wait(timeout=10)
            with db.connect() as conn:
                return conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(initialize, range(4))), [0] * 4)

    def test_migration_upgrades_old_index_and_handles_retries(self):
        entry = self.entry(client_id="legacy-client-123")
        with db.connect() as conn:
            conn.execute("DROP INDEX uq_entries_client")
            conn.execute("CREATE INDEX idx_entries_client ON entries(client_id)")
        db._ready.discard(str(db.DB_PATH))
        with db.connect() as conn:
            indexes = {r["name"]: r["unique"] for r in conn.execute("PRAGMA index_list(entries)")}
        self.assertEqual(indexes["uq_entries_client"], 1)
        self.assertNotIn("idx_entries_client", indexes)
        retry = self.entry(client_id="legacy-client-123")
        self.assertTrue(retry["duplicate"])
        self.assertEqual(retry["id"], entry["id"])

    def test_migration_preserves_legacy_duplicates_for_review(self):
        self.entry(client_id="legacy-duplicate-123")
        with db.connect() as conn:
            conn.execute("DROP INDEX uq_entries_client")
            columns = ", ".join(r["name"] for r in conn.execute("PRAGMA table_info(entries)") if r["name"] != "id")
            conn.execute(f"INSERT INTO entries ({columns}) SELECT {columns} FROM entries")
        db._ready.discard(str(db.DB_PATH))
        with self.assertRaisesRegex(RuntimeError, "docs/DATA_CORRECTNESS.md"):
            db.connect()
        with sqlite3.connect(db.DB_PATH) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
