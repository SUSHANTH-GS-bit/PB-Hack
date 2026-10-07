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
# CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Use environment variable if provided.
# Otherwise use /tmp for serverless environments such as Vercel.
DEFAULT_DB = os.environ.get(
    "CHAINCERT_DB",
    "/tmp/chaincert.db"
)

RECORD_ID = re.compile(
    r"^[A-Za-z0-9._-]{1,40}$"
)

FIELD_LIMIT = 120


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def clean(value, limit=FIELD_LIMIT):
    """
    Convert a value to a clean string
    and limit its length.
    """
    return str(
        value if value is not None else ""
    ).strip()[:limit]


def build_record(body):
    """
    Validate and build an academic
    certificate record.
    """

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

    # Required fields
    if not record_id or not student or not course:
        return None, (
            "record_id, student_name and course are required"
        )

    # Validate certificate ID
    if not RECORD_ID.match(record_id):
        return None, (
            "record_id may only contain letters, "
            "digits, dot, dash and underscore"
        )

    record = {
        "record_id": record_id,

        "student_name": student,

        "course": course,

        "marks": clean(
            body.get("marks")
        ),

        "issuer": (
            clean(body.get("issuer"))
            or ISSUER
        ),

        "issue_date": (
            clean(
                body.get("issue_date"),
                20
            )
            or time.strftime("%Y-%m-%d")
        ),
    }

    return record, None


# ============================================================
# CREATE FLASK APPLICATION
# ============================================================

def create_app(
    db_path=None,
    difficulty=DIFFICULTY
):

    app = Flask(
        __name__,
        template_folder=os.path.join(
            BASE_DIR,
            "../templates"
        ),
        static_folder=os.path.join(
            BASE_DIR,
            "../static"
        )
    )

    # --------------------------------------------------------
    # DATABASE / STORAGE
    # --------------------------------------------------------

    store = Store(
        db_path or DEFAULT_DB
    )

    lock = threading.RLock()

    # Load existing blockchain
    bc = store.load_chain()

    # If no blockchain exists,
    # create genesis block
    if bc is None:

        bc = Blockchain(
            difficulty
        )

        store.save_chain(
            bc
        )

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

    # Store blockchain in application state
    state = {
        "bc": bc
    }

    # ========================================================
    # INTERNAL FUNCTIONS
    # ========================================================

    def issue(data):

        start = time.perf_counter()

        block = state["bc"].add_record(
            data
        )

        elapsed_ms = (
            time.perf_counter() - start
        ) * 1000

        # Save blockchain
        store.save_chain(
            state["bc"]
        )

        # Activity log
        store.log_event(
            "mined",
            (
                f"Block {block.index} mined "
                f"for {data['record_id']} "
                f"(nonce {block.nonce}, "
                f"{elapsed_ms:.0f} ms)"
            )
        )

        return block, elapsed_ms


    def has_record(record_id):

        return any(
            block.data.get("record_id")
            == record_id
            for block
            in state["bc"].chain[1:]
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
    # GET BLOCKCHAIN
    # ========================================================

    @app.route("/api/chain")
    def get_chain():

        with lock:

            chain = state["bc"]

            report = (
                chain.validate_report()
            )

            return jsonify(

                chain=chain.to_list(),

                valid=report["valid"],

                problems=report["problems"],

                bad_blocks=(
                    report["summary"]["flagged"]
                ),

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
    # ADD CERTIFICATE / RECORD
    # ========================================================

    @app.route(
        "/api/add",
        methods=["POST"]
    )
    def add_record():

        body = (
            request.get_json(
                force=True,
                silent=True
            )
            or {}
        )

        data, error = build_record(
            body
        )

        if error:

            return jsonify(
                error=error
            ), 400

        with lock:

            # Prevent duplicate certificate IDs
            if has_record(
                data["record_id"]
            ):

                return jsonify(
                    error="record_id already exists"
                ), 409

            # Add record to blockchain
            block, elapsed_ms = issue(
                data
            )

            return jsonify(

                block=block.to_dict(),

                mining={
                    "nonce": block.nonce,
                    "ms": round(
                        elapsed_ms,
                        1
                    )
                }
            )


    # ========================================================
    # TAMPER BLOCK
    # ========================================================

    @app.route(
        "/api/tamper",
        methods=["POST"]
    )
    def tamper():

        body = (
            request.get_json(
                force=True,
                silent=True
            )
            or {}
        )

        try:

            index = int(
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

            # Genesis block cannot be tampered
            if not 1 <= index < len(
                chain.chain
            ):

                return jsonify(
                    error="invalid block index"
                ), 400

            block = chain.chain[index]

            # Field to modify
            field = body.get(
                "field"
            )

            if field not in block.data:

                return jsonify(
                    error="unknown field"
                ), 400

            # Modify the data
            block.data[field] = clean(
                body.get("value")
            )

            # Optional attacker rehash
            rehash = bool(
                body.get("rehash")
            )

            if rehash:

                block.field_hashes = (
                    fingerprints(
                        block.data
                    )
                )

                block.hash = (
                    block.compute_hash()
                )

            # Save tampered chain
            store.save_chain(
                chain
            )

            # Activity log
            store.log_event(
                "tamper",
                (
                    f"Simulated edit on "
                    f"block {index}, "
                    f"field '{field}'"
                    +
                    (
                        " (block hash recomputed by attacker)"
                        if rehash
                        else
                        " (hash not recomputed)"
                    )
                )
            )

            return jsonify(

                ok=True,

                index=index,

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

            result = (
                state["bc"]
                .verify_record(
                    record_id
                )
            )

            # Activity logging
            if result["found"]:

                outcome = (
                    "authentic"
                    if result["authentic"]
                    else "TAMPERED"
                )

                store.log_event(
                    "verify",
                    (
                        f"Verified "
                        f"{record_id}: "
                        f"{outcome}"
                    )
                )

            else:

                store.log_event(
                    "verify",
                    (
                        f"Verified "
                        f"{record_id}: "
                        f"not found"
                    )
                )

            return jsonify(
                result
            )


    # ========================================================
    # GET SAMPLE CERTIFICATES
    # ========================================================

    @app.route(
        "/api/samples"
    )
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

                data, error = build_record(
                    sample
                )

                if error:

                    continue

                issue(
                    data
                )

                added += 1

            store.log_event(
                "samples",
                (
                    f"Loaded {added} "
                    f"sample certificates "
                    f"({skipped} already present)"
                )
            )

            return jsonify(

                added=added,

                skipped=skipped
            )


    # ========================================================
    # ACTIVITY LOG
    # ========================================================

    @app.route(
        "/api/events"
    )
    def events():

        return jsonify(
            events=store.recent_events()
        )


    # ========================================================
    # EXPORT BLOCKCHAIN
    # ========================================================

    @app.route(
        "/api/export"
    )
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
                    (
                        "attachment; "
                        "filename="
                        "chaincert-chain.json"
                    )
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
    # HEALTH CHECK
    # ========================================================

    @app.route(
        "/api/health"
    )
    def health():

        with lock:

            report = (
                state["bc"].validate_report()
            )

            return jsonify(

                status="online",

                blockchain_valid=(
                    report["valid"]
                ),

                blocks=len(
                    state["bc"].chain
                ),

                difficulty=(
                    state["bc"].difficulty
                )
            )


    # ========================================================
    # RETURN FLASK APPLICATION
    # ========================================================

    return app


# ============================================================
# PRODUCTION ENTRY POINT
# ============================================================

app = create_app()


# ============================================================
# LOCAL DEVELOPMENT
# ============================================================

if __name__ == "__main__":

    if "--seed" in sys.argv:

        with app.test_client() as client:

            response = client.post(
                "/api/seed"
            )

            print(
                "Seeded:",
                response.get_json()
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
