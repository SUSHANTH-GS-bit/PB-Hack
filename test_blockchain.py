"""Run with:  python -m unittest -v   (runs test_blockchain.py and test_app.py)"""
import unittest
from blockchain import Blockchain


def make_chain():
    bc = Blockchain(difficulty=2)  # low difficulty keeps tests fast
    bc.add_record({"record_id": "A1", "student_name": "Asha", "marks": "90"})
    bc.add_record({"record_id": "A2", "student_name": "Ravi", "marks": "75"})
    bc.add_record({"record_id": "A3", "student_name": "Meena", "marks": "88"})
    return bc


class TestChain(unittest.TestCase):
    def test_fresh_chain_is_valid(self):
        ok, problems = make_chain().validate()
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    def test_links_are_correct(self):
        bc = make_chain()
        for i in range(1, len(bc.chain)):
            self.assertEqual(bc.chain[i].previous_hash, bc.chain[i - 1].hash)

    def test_edit_data_detected(self):
        bc = make_chain()
        bc.chain[2].data["marks"] = "100"
        ok, problems = bc.validate()
        self.assertFalse(ok)
        self.assertEqual(problems[0]["block"], 2)

    def test_plain_edit_also_breaks_next_link(self):
        # Matches slide 4: edited block fails its hash AND the next block's link fails.
        bc = make_chain()
        bc.chain[1].data["marks"] = "100"
        ok, problems = bc.validate()
        self.assertFalse(ok)
        reasons = {(p["block"], p["reason"].split(":")[0]) for p in problems}
        self.assertIn((1, "Data was modified"), reasons)
        self.assertIn((2, "Broken link"), reasons)
        self.assertFalse(any(p["block"] == 3 for p in problems))  # later blocks still link correctly

    def test_edit_and_rehash_breaks_next_link(self):
        # Smarter attacker recomputes the hash of the edited block...
        bc = make_chain()
        b = bc.chain[1]
        b.data["marks"] = "100"
        b.hash = b.compute_hash()
        ok, problems = bc.validate()
        self.assertFalse(ok)
        # ...but the next block still points at the OLD hash, and PoW fails too.
        self.assertTrue(any(p["block"] == 2 and "Broken link" in p["reason"] for p in problems))

    def test_verify_record(self):
        bc = make_chain()
        self.assertTrue(bc.verify_record("A2")["authentic"])
        bc.chain[2].data["marks"] = "1"
        self.assertFalse(bc.verify_record("A2")["authentic"])
        self.assertTrue(bc.verify_record("A1")["authentic"])
        self.assertFalse(bc.verify_record("NOPE")["found"])

    def test_save_load_roundtrip(self):
        import os, tempfile
        bc = make_chain()
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        bc.save(path)
        self.assertTrue(Blockchain.load(path).validate()[0])


class TestDetailedDetection(unittest.TestCase):
    def test_report_shows_exact_hash_mismatch(self):
        bc = make_chain()
        stored = bc.chain[1].hash
        bc.chain[1].data["marks"] = "100"
        report = bc.validate_report()
        h = report["blocks"][1]["checks"]["hash"]
        self.assertFalse(h["ok"])
        self.assertEqual(h["stored"], stored)
        self.assertEqual(h["recomputed"], bc.chain[1].compute_hash())
        self.assertNotEqual(h["stored"], h["recomputed"])

    def test_report_names_the_edited_field(self):
        bc = make_chain()
        bc.chain[2].data["marks"] = "100"
        items = {i["field"]: i for i in bc.validate_report()["blocks"][2]["checks"]["fields"]["items"]}
        self.assertEqual(items["marks"]["state"], "modified")
        self.assertEqual(items["student_name"]["state"], "match")
        self.assertNotEqual(items["marks"]["expected"], items["marks"]["actual"])
        types = [(p["block"], p["type"], p.get("field")) for p in bc.validate_report()["problems"]]
        self.assertIn((2, "field_modified", "marks"), types)

    def test_report_shows_exact_previous_hash_mismatch(self):
        bc = make_chain()
        original_hash = bc.chain[1].hash
        bc.chain[1].data["marks"] = "100"
        link = bc.validate_report()["blocks"][2]["checks"]["link"]
        self.assertFalse(link["ok"])
        self.assertEqual(link["stored_previous"], original_hash)
        self.assertEqual(link["actual_previous"], bc.chain[1].compute_hash())

    def test_block_status_values(self):
        bc = make_chain()
        self.assertTrue(all(b["status"] == "valid" for b in bc.validate_report()["blocks"]))
        bc.chain[1].data["marks"] = "100"
        status = [b["status"] for b in bc.validate_report()["blocks"]]
        self.assertEqual(status, ["valid", "tampered", "broken_link", "valid"])

    def test_summary_counts(self):
        bc = make_chain()
        bc.chain[2].data["marks"] = "1"
        s = bc.validate_report()["summary"]
        self.assertEqual(s["total_blocks"], 4)
        self.assertEqual(s["flagged"], [2, 3])
        self.assertEqual(s["first_invalid"], 2)
        self.assertEqual(s["valid_blocks"], 2)

    def test_rehash_with_fresh_fingerprints_still_caught_by_link_and_pow(self):
        from blockchain import fingerprints
        bc = make_chain()
        b = bc.chain[1]
        b.data["marks"] = "100"
        b.field_hashes = fingerprints(b.data)
        b.hash = b.compute_hash()
        types = {(p["block"], p["type"]) for p in bc.validate_report()["problems"]}
        self.assertIn((2, "link_broken"), types)
        self.assertFalse(any(t == "hash_mismatch" and blk == 1 for blk, t in types))

    def test_added_field_detected(self):
        bc = make_chain()
        bc.chain[1].data["extra"] = "x"
        items = {i["field"]: i for i in bc.validate_report()["blocks"][1]["checks"]["fields"]["items"]}
        self.assertEqual(items["extra"]["state"], "added")


if __name__ == "__main__":
    unittest.main()
