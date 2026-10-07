"""
app.py - ChainCert Flask server: REST API + dashboard.

Run:   python app.py            then open http://127.0.0.1:5000
       python app.py --seed     also loads the sample certificates on start

The chain is stored in a SQLite file (chaincert.db next to this file, or the path
in the CHAINCERT_DB environment variable), so records survive restarts.
"""
import json
import os
import re
import sys
import threading
import time

from flask import Flask, Response, jsonify, render_template, request

from blockchain import DIFFICULTY, Blockchain, fingerprints
from samples import ISSUER, SAMPLE_CERTIFICATES
from storage import Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.environ.get("CHAINCERT_DB", os.path.join(BASE_DIR, "chaincert.db"))
RECORD_ID = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
FIELD_LIMIT = 120


def clean(value, limit=FIELD_LIMIT):
    return str(value if value is not None else "").strip()[:limit]


def build_record(body):
    """Validate request data and return (record_dict, error_message)."""
    record_id = clean(body.get("record_id"), 40)
    student = clean(body.get("student_name"))
    course = clean(body.get("course"))
    if not record_id or not student or not course:
        return None, "record_id, student_name and course are required"
    if not RECORD_ID.match(record_id):
        return None, "record_id may only contain letters, digits, dot, dash and underscore"
    return {
        "record_id": record_id,
        "student_name": student,
        "course": course,
        "marks": clean(body.get("marks")),
        "issuer": clean(body.get("issuer")) or ISSUER,
        "issue_date": clean(body.get("issue_date"), 20) or time.strftime("%Y-%m-%d"),
    }, None


def create_app(db_path=None, difficulty=DIFFICULTY):
    app = Flask(__name__)
    store = Store(db_path or DEFAULT_DB)
    lock = threading.RLock()

    bc = store.load_chain()
    if bc is None:
        bc = Blockchain(difficulty)
        store.save_chain(bc)
        store.log_event("system", "Genesis block created")
    else:
        store.log_event("system", f"Chain restored from storage ({len(bc.chain)} blocks)")
    state = {"bc": bc}

    def issue(data):
        start = time.perf_counter()
        block = state["bc"].add_record(data)
        ms = (time.perf_counter() - start) * 1000
        store.save_chain(state["bc"])
        store.log_event("mined", f"Block {block.index} mined for {data['record_id']} (nonce {block.nonce}, {ms:.0f} ms)")
        return block, ms

    def has_record(record_id):
        return any(b.data.get("record_id") == record_id for b in state["bc"].chain[1:])

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/chain")
    def get_chain():
        with lock:
            chain = state["bc"]
            report = chain.validate_report()
            return jsonify(
                chain=chain.to_list(),
                valid=report["valid"],
                problems=report["problems"],
                bad_blocks=report["summary"]["flagged"],
                blocks=report["blocks"],
                summary=report["summary"],
                difficulty=chain.difficulty,
                storage={"engine": "SQLite", "file": os.path.basename(store.path)},
            )

    @app.route("/api/add", methods=["POST"])
    def add_record():
        body = request.get_json(force=True, silent=True) or {}
        data, error = build_record(body)
        if error:
            return jsonify(error=error), 400
        with lock:
            if has_record(data["record_id"]):
                return jsonify(error="record_id already exists"), 409
            block, ms = issue(data)
            return jsonify(block=block.to_dict(), mining={"nonce": block.nonce, "ms": round(ms, 1)})

    @app.route("/api/tamper", methods=["POST"])
    def tamper():
        """DEMO ONLY: edit a block's stored data like an attacker with database access.

        By default the hash is NOT recomputed. With rehash=true the attacker also
        recomputes the block's field fingerprints and hash to hide the edit, which
        is then caught by the broken link in the next block (and failed proof of work).
        """
        body = request.get_json(force=True, silent=True) or {}
        try:
            idx = int(body.get("index"))
        except (TypeError, ValueError):
            return jsonify(error="invalid block index"), 400
        with lock:
            chain = state["bc"]
            if not 1 <= idx < len(chain.chain):
                return jsonify(error="invalid block index"), 400
            block = chain.chain[idx]
            field = body.get("field")
            if field not in block.data:
                return jsonify(error="unknown field"), 400
            block.data[field] = clean(body.get("value"))
            rehash = bool(body.get("rehash"))
            if rehash:
                block.field_hashes = fingerprints(block.data)
                block.hash = block.compute_hash()
            store.save_chain(chain)
            store.log_event("tamper", f"Simulated edit on block {idx}, field '{field}'"
                                      + (" (block hash recomputed by attacker)" if rehash else " (hash not recomputed)"))
            return jsonify(ok=True, index=idx, rehash=rehash)

    @app.route("/api/verify/<record_id>")
    def verify(record_id):
        with lock:
            result = state["bc"].verify_record(record_id)
            if result["found"]:
                outcome = "authentic" if result["authentic"] else "TAMPERED"
                store.log_event("verify", f"Verified {record_id}: {outcome}")
            else:
                store.log_event("verify", f"Verified {record_id}: not found")
            return jsonify(result)

    @app.route("/api/samples")
    def samples():
        return jsonify(samples=[dict(s, issuer=ISSUER) for s in SAMPLE_CERTIFICATES])

    @app.route("/api/seed", methods=["POST"])
    def seed():
        with lock:
            added = skipped = 0
            for sample in SAMPLE_CERTIFICATES:
                if has_record(sample["record_id"]):
                    skipped += 1
                    continue
                data, _ = build_record(sample)
                issue(data)
                added += 1
            store.log_event("samples", f"Loaded {added} sample certificates ({skipped} already present)")
            return jsonify(added=added, skipped=skipped)

    @app.route("/api/events")
    def events():
        return jsonify(events=store.recent_events())

    @app.route("/api/export")
    def export():
        with lock:
            chain = state["bc"]
            payload = json.dumps({"difficulty": chain.difficulty, "chain": chain.to_list()}, indent=2)
        return Response(payload, mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=chaincert-chain.json"})

    @app.route("/api/reset", methods=["POST"])
    def reset():
        with lock:
            state["bc"] = Blockchain(state["bc"].difficulty)
            store.save_chain(state["bc"])
            store.log_event("system", "Chain reset to a new genesis block")
            return jsonify(ok=True)

    return app


if __name__ == "__main__":
    flask_app = create_app()
    if "--seed" in sys.argv:
        with flask_app.test_client() as client:
            print("Seeded:", client.post("/api/seed").get_json())
    flask_app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
