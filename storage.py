"""
storage.py - persistent storage for the chain, backed by SQLite (standard library).

Every change to the chain is written to the database, including simulated
tampering, so the demo state survives a Flask restart exactly as it was.
Stored hashes are loaded as-is and never recomputed on load.
"""
import json
import os
import sqlite3
import time
from contextlib import contextmanager

from blockchain import Blockchain

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    idx           INTEGER PRIMARY KEY,
    timestamp     REAL    NOT NULL,
    data          TEXT    NOT NULL,
    previous_hash TEXT    NOT NULL,
    nonce         INTEGER NOT NULL,
    hash          TEXT    NOT NULL,
    field_hashes  TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._tx() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- chain ----
    def save_chain(self, bc):
        """Replace the stored chain with the current one in a single transaction."""
        with self._tx() as conn:
            conn.execute("DELETE FROM blocks")
            conn.executemany(
                "INSERT INTO blocks (idx, timestamp, data, previous_hash, nonce, hash, field_hashes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(b.index, b.timestamp, json.dumps(b.data, sort_keys=True), b.previous_hash,
                  b.nonce, b.hash, json.dumps(b.field_hashes, sort_keys=True)) for b in bc.chain],
            )
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('difficulty', ?)", (str(bc.difficulty),))

    def load_chain(self):
        """Return the stored Blockchain, or None when the database is empty."""
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM blocks ORDER BY idx").fetchall()
            meta = conn.execute("SELECT value FROM meta WHERE key = 'difficulty'").fetchone()
        if not rows:
            return None
        blocks = [{
            "index": r["idx"], "timestamp": r["timestamp"], "data": json.loads(r["data"]),
            "previous_hash": r["previous_hash"], "nonce": r["nonce"], "hash": r["hash"],
            "field_hashes": json.loads(r["field_hashes"]),
        } for r in rows]
        difficulty = int(meta["value"]) if meta else 3
        return Blockchain.from_blocks(difficulty, blocks)

    # ---- activity log ----
    def log_event(self, kind, message):
        with self._tx() as conn:
            conn.execute("INSERT INTO events (ts, kind, message) VALUES (?, ?, ?)", (time.time(), kind, message))

    def recent_events(self, limit=60):
        with self._tx() as conn:
            rows = conn.execute("SELECT ts, kind, message FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"ts": r["ts"], "kind": r["kind"], "message": r["message"]} for r in rows]
