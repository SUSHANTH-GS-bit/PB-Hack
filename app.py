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


# ============================================================
# PATHS
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Vercel filesystem is read-only except /tmp
DEFAULT_DB = os.environ.get(
    "CHAINCERT_DB",
    "/tmp/chaincert.db"
)

# Frontend is outside Backend:
# PB-Hack/
# ├── Backend/app.py
# └── FrontEnd/index.html
FRONTEND_DIR = os.path.join(BASE_DIR, "../FrontEnd")


# ============================================================
# VALIDATION
# ============================================================

RECORD_ID = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
FIELD_LIMIT = 120


def clean(value, limit=FIELD_LIMIT):
    return str(
        value if value is not None else ""
    ).strip()[:limit]


def build_record(body):
    record_id = clean(
        body.get("record_id"),
        40
    )

    student = clean(
        body.get("student_name")
    )

    course = clean(
        body.get("course")
    )

    if not record_id or not student or not course:
        return None, (
            "record_id, student_name and course are required"
        )

    if not RECORD_ID.match(record_id):
        return None, (
            "record_id may only contain letters, "
            "digits, dot, dash and underscore"
        )

    return {
        "record_id": record_id,
        "student_name": student,
        "course": course,
        "marks": clean(body.get("marks")),
        "issuer": clean(
            body.get("issuer")
        ) or ISSUER,
        "issue_date": clean(
            body.get("issue_date"),
            20
        ) or time.strftime("%Y-%m-%d"),
    }, None


# ============================================================
# APPLICATION FACTORY
# ============================================================

def create_app(
    db_path=None,
    difficulty=DIFFICULTY
):

    # IMPORTANT:
    # index.html is inside ../FrontEnd
    app = Flask(
        __name__,
        template_folder=FRONTEND_DIR
    )

    store = Store(
        db_path or DEFAULT_DB
    )

    lock = threading.RLock()

    # Load existing blockchain
    bc = store.load_chain()

    if bc is None:
        bc = Blockchain(difficulty)

        store.save_chain(bc)

        store.log_event(
            "system",
            "Genesis block created"
        )

    else:
        store.log_event(
            "system",
            f"Chain restored from storage "
            f"({len(bc.chain)} blocks)"
        )

    state = {
        "bc": bc
    }


    # ========================================================
    # ISSUE RECORD
    # ========================================================

    def issue(data):

        start = time.perf_counter()

        block = state["bc"].add_record(data)

        ms = (
            time.perf_counter() - start
        ) * 1000

        store.save_chain(
            state["bc"]
        )

        store.log_event(
            "mined",
            f"Block {block.index} mined for "
            f"{data['record_id']} "
            f"(nonce {block.nonce}, {ms:.0f} ms)"
        )

        return block, ms


    # ========================================================
    # CHECK DUPLICATE RECORD
    # ========================================================

    def has_record(record_id):

        return any(
            b.data.get("record_id") == record_id
            for b in state["bc"].chain[1:]
        )


    # ========================================================
    # MAIN PAGE
    # ========================================================

    @app.route("/")
    def index():

        return render_template(
            "index.html"
        )


    # ========================================================
    # VERIFY PAGE
    # ========================================================

    @app.route("/verify")
    def verify_page():

        return render_template(
            "index.html"
        )


    # ========================================================
    # HEALTH CHECK
    # ========================================================

    @app.route("/api/health")
    def health():

        with lock:

            chain = state["bc"]

            report = chain.validate_report()

            return jsonify(
                status="online",
                service="ChainCert",
                blockchain_valid=report["valid"],
                blocks=len(chain.chain),
                storage="SQLite",
                database=os.path.basename(
                    store.path
                )
            )


    # ========================================================
    # GET BLOCKCHAIN
    # ========================================================

    @app.route("/api/chain")
    def get_chain():

        with lock:

            chain = state["bc"]

            report = chain.validate_report()

            return jsonify(

                chain=chain.to_list(),

                valid=report["valid"],

                problems=report["problems"],

                bad_blocks=report[
                    "summary"
                ]["flagged"],

                blocks=report["blocks"],

                summary=report["summary"],

                difficulty=chain.difficulty,

                storage={
                    "engine": "SQLite",
                    "file": os.path.basename(
                        store.path
                    )
                }
            )


    # ========================================================
    # ADD CERTIFICATE
    # ========================================================

    @app.route(
        "/api/add",
        methods=["POST"]
    )
    def add_record():

        body = request.get_json(
            force=True,
            silent=True
        ) or {}

        data, error = build_record(
            body
        )

        if error:

            return jsonify(
                error=error
            ), 400

        with lock:

            if has_record(
                data["record_id"]
            ):

                return jsonify(
                    error="record_id already exists"
                ), 409

            block, ms = issue(data)

            return jsonify(

                block=block.to_dict(),

                mining={
                    "nonce": block.nonce,
                    "ms": round(ms, 1)
                }
            )


    # ========================================================
    # SIMULATE TAMPERING
    # ========================================================

    @app.route(
        "/api/tamper",
        methods=["POST"]
    )
    def tamper():

        body = request.get_json(
            force=True,
            silent=True
        ) or {}

        try:

            idx = int(
                body.get("index")
            )

        except (
            TypeError,
            ValueError
        ):

            return jsonify(
                error="invalid block index"
            ), 400


        with lock:

            chain = state["bc"]

            if not 1 <= idx < len(
                chain.chain
            ):

                return jsonify(
                    error="invalid block index"
                ), 400


            block = chain.chain[idx]

            field = body.get(
                "field"
            )

            if field not in block.data:

                return jsonify(
                    error="unknown field"
                ), 400


            # Modify stored data
            block.data[field] = clean(
                body.get("value")
            )


            # Advanced attacker:
            # change data AND recompute hash
            rehash = bool(
                body.get("rehash")
            )


            if rehash:

                block.field_hashes = fingerprints(
                    block.data
                )

                block.hash = block.compute_hash()


            store.save_chain(
                chain
            )


            store.log_event(

                "tamper",

                f"Simulated edit on block {idx}, "
                f"field '{field}'"
                +
                (
                    " (block hash recomputed by attacker)"
                    if rehash
                    else
                    " (hash not recomputed)"
                )
            )


            return jsonify(

                ok=True,

                index=idx,

                rehash=rehash
            )


    # ========================================================
    # VERIFY CERTIFICATE
    # ========================================================

    @app.route(
        "/api/verify/<record_id>"
    )
    def verify(record_id):

        with lock:

            result = state[
                "bc"
            ].verify_record(
                record_id
            )


            if result["found"]:

                outcome = (
                    "authentic"
                    if result["authentic"]
                    else "TAMPERED"
                )

                store.log_event(

                    "verify",

                    f"Verified {record_id}: "
                    f"{outcome}"
                )

            else:

                store.log_event(

                    "verify",

                    f"Verified {record_id}: "
                    f"not found"
                )


            return jsonify(
                result
            )


    # ========================================================
    # SAMPLE CERTIFICATES
    # ========================================================

    @app.route("/api/samples")
    def samples():

        return jsonify(

            samples=[
                dict(
                    sample,
                    issuer=ISSUER
                )

                for sample
                in SAMPLE_CERTIFICATES
            ]
        )


    # ========================================================
    # SEED SAMPLE DATA
    # ========================================================

    @app.route(
        "/api/seed",
        methods=["POST"]
    )
    def seed():

        with lock:

            added = 0
            skipped = 0


            for sample in SAMPLE_CERTIFICATES:

                if has_record(
                    sample["record_id"]
                ):

                    skipped += 1

                    continue


                data, _ = build_record(
                    sample
                )

                issue(data)

                added += 1


            store.log_event(

                "samples",

                f"Loaded {added} "
                f"sample certificates "
                f"({skipped} already present)"
            )


            return jsonify(

                added=added,

                skipped=skipped
            )


    # ========================================================
    # ACTIVITY LOG
    # ========================================================

    @app.route("/api/events")
    def events():

        return jsonify(
            events=store.recent_events()
        )


    # ========================================================
    # EXPORT BLOCKCHAIN
    # ========================================================

    @app.route("/api/export")
    def export():

        with lock:

            chain = state["bc"]

            payload = json.dumps(

                {
                    "difficulty":
                        chain.difficulty,

                    "chain":
                        chain.to_list()
                },

                indent=2
            )


            return Response(

                payload,

                mimetype="application/json",

                headers={
                    "Content-Disposition":
                    "attachment; "
                    "filename="
                    "chaincert-chain.json"
                }
            )


    # ========================================================
    # RESET BLOCKCHAIN
    # ========================================================

    @app.route(
        "/api/reset",
        methods=["POST"]
    )
    def reset():

        with lock:

            state["bc"] = Blockchain(
                state["bc"].difficulty
            )

            store.save_chain(
                state["bc"]
            )

            store.log_event(
                "system",
                "Chain reset to a new genesis block"
            )


            return jsonify(
                ok=True
            )


    # ========================================================
    # RETURN APPLICATION
    # ========================================================

    return app


# ============================================================
# TOP-LEVEL FLASK APPLICATION
# ============================================================

app = create_app()


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    if "--seed" in sys.argv:

        with app.test_client() as client:

            print(
                "Seeded:",
                client.post(
                    "/api/seed"
                ).get_json()
            )


    app.run(

        host="0.0.0.0",

        port=int(
            os.environ.get(
                "PORT",
                "5000"
            )
        ),

        debug=False,

        threaded=True
    )
