"""API and persistence tests. Run with:  python -m unittest -v"""
import os
import tempfile
import unittest

from app import create_app
from samples import SAMPLE_CERTIFICATES


class TestApp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "test.db")
        self.app = create_app(self.db, difficulty=2)
        self.c = self.app.test_client()

    def add(self, client, rid, name="Asha", course="Python", marks="90"):
        return client.post("/api/add", json={"record_id": rid, "student_name": name, "course": course, "marks": marks})

    def test_sample_set_is_large_and_unique(self):
        ids = [s["record_id"] for s in SAMPLE_CERTIFICATES]
        self.assertGreaterEqual(len(ids), 10)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(SAMPLE_CERTIFICATES[0]["student_name"], "Rahul Verma")
        self.assertEqual(SAMPLE_CERTIFICATES[0]["marks"], "85")

    def test_seed_loads_all_samples_and_chain_is_valid(self):
        r = self.c.post("/api/seed").get_json()
        self.assertEqual(r["added"], len(SAMPLE_CERTIFICATES))
        d = self.c.get("/api/chain").get_json()
        self.assertTrue(d["valid"])
        self.assertEqual(d["summary"]["records"], len(SAMPLE_CERTIFICATES))
        again = self.c.post("/api/seed").get_json()  # second call adds nothing
        self.assertEqual((again["added"], again["skipped"]), (0, len(SAMPLE_CERTIFICATES)))

    def test_records_survive_restart(self):
        self.add(self.c, "P1")
        self.add(self.c, "P2", name="Ravi")
        before = self.c.get("/api/chain").get_json()["chain"]
        restarted = create_app(self.db, difficulty=2).test_client()   # new process, same database
        after = restarted.get("/api/chain").get_json()
        self.assertEqual([b["hash"] for b in after["chain"]], [b["hash"] for b in before])
        self.assertTrue(after["valid"])
        self.assertEqual(after["summary"]["records"], 2)

    def test_tampering_survives_restart_and_is_still_detected(self):
        self.c.post("/api/seed")
        r = self.c.post("/api/tamper", json={"index": 1, "field": "marks", "value": "95"})
        self.assertEqual(r.status_code, 200)
        restarted = create_app(self.db, difficulty=2).test_client()
        d = restarted.get("/api/chain").get_json()
        self.assertFalse(d["valid"])
        self.assertEqual(d["blocks"][1]["status"], "tampered")
        self.assertEqual(d["blocks"][2]["status"], "broken_link")
        types = {(p["block"], p["type"]) for p in d["problems"]}
        self.assertIn((1, "hash_mismatch"), types)
        self.assertIn((1, "field_modified"), types)
        self.assertIn((2, "link_broken"), types)

    def test_problem_reports_expected_and_actual_values(self):
        self.add(self.c, "P1")
        self.c.post("/api/tamper", json={"index": 1, "field": "marks", "value": "100"})
        problems = self.c.get("/api/chain").get_json()["problems"]
        h = next(p for p in problems if p["type"] == "hash_mismatch")
        self.assertEqual(len(h["expected"]), 64)
        self.assertEqual(len(h["actual"]), 64)
        self.assertNotEqual(h["expected"], h["actual"])
        f = next(p for p in problems if p["type"] == "field_modified")
        self.assertEqual(f["field"], "marks")

    def test_rehash_mode_is_caught_by_next_block_link(self):
        self.c.post("/api/seed")
        self.c.post("/api/tamper", json={"index": 3, "field": "marks", "value": "99", "rehash": True})
        d = self.c.get("/api/chain").get_json()
        types = {(p["block"], p["type"]) for p in d["problems"]}
        self.assertIn((4, "link_broken"), types)
        self.assertNotIn((3, "hash_mismatch"), types)

    def test_verify_endpoint(self):
        self.c.post("/api/seed")
        self.assertTrue(self.c.get("/api/verify/CERT003").get_json()["authentic"])
        self.c.post("/api/tamper", json={"index": 3, "field": "marks", "value": "10"})
        bad = self.c.get("/api/verify/CERT003").get_json()
        self.assertFalse(bad["authentic"])
        self.assertTrue(bad["issues"])
        self.assertTrue(self.c.get("/api/verify/CERT001").get_json()["authentic"])
        self.assertFalse(self.c.get("/api/verify/NOPE").get_json()["found"])

    def test_validation_and_errors(self):
        self.assertEqual(self.add(self.c, "P1").status_code, 200)
        self.assertEqual(self.add(self.c, "P1").status_code, 409)
        self.assertEqual(self.c.post("/api/add", json={"record_id": "X"}).status_code, 400)
        self.assertEqual(self.add(self.c, "bad id!").status_code, 400)
        self.assertEqual(self.c.post("/api/tamper", json={"index": 0, "field": "note", "value": "x"}).status_code, 400)
        self.assertEqual(self.c.post("/api/tamper", json={"index": 1, "field": "nope", "value": "x"}).status_code, 400)
        self.assertEqual(self.c.post("/api/tamper", json={"index": "abc"}).status_code, 400)

    def test_reset_is_persistent(self):
        self.add(self.c, "P1")
        self.c.post("/api/reset")
        restarted = create_app(self.db, difficulty=2).test_client()
        self.assertEqual(restarted.get("/api/chain").get_json()["summary"]["records"], 0)

    def test_activity_log_and_export(self):
        self.add(self.c, "P1")
        self.c.post("/api/tamper", json={"index": 1, "field": "marks", "value": "1"})
        kinds = [e["kind"] for e in self.c.get("/api/events").get_json()["events"]]
        self.assertIn("mined", kinds)
        self.assertIn("tamper", kinds)
        exp = self.c.get("/api/export")
        self.assertEqual(exp.status_code, 200)
        self.assertIn("attachment", exp.headers["Content-Disposition"])

    def test_dashboard_page_has_no_emoji(self):
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn("Block Explorer", html)
        for ch in html:
            self.assertFalse(0x1F300 <= ord(ch) <= 0x1FAFF or 0x2600 <= ord(ch) <= 0x27BF, "emoji or dingbat: %r" % ch)


if __name__ == "__main__":
    unittest.main()
