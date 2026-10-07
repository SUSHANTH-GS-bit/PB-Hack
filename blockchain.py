"""
blockchain.py - a small blockchain built from scratch (standard library only).

Each block stores a certificate / marks record and is linked to the previous
block by its SHA-256 hash. If ANY part of ANY block is edited, validation
reports exactly which check failed, which block it was, and the expected and
actual hash values side by side.

Checks run on every block:
  1. hash         stored block hash must equal the hash recomputed from content
  2. fields       every record field must match the fingerprint stored when the
                  block was created (shows WHICH field was edited)
  3. proof of work  the stored hash must start with the required zeros
  4. link         previous_hash must equal the recomputed hash of the previous block
"""
import hashlib
import json
import time

DIFFICULTY = 3  # number of leading zeros required in a hash (proof of work)


def sha256_text(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fingerprint(value):
    """SHA-256 fingerprint of a single record field."""
    return sha256_text(str(value))


def fingerprints(data):
    return {key: fingerprint(value) for key, value in data.items()}


class Block:
    def __init__(self, index, timestamp, data, previous_hash, nonce=0, hash=None, field_hashes=None):
        self.index = index
        self.timestamp = timestamp
        self.data = data                      # dict: the certificate / marks record
        self.previous_hash = previous_hash
        self.nonce = nonce
        # Per-field fingerprints are taken when the block is created and are part of
        # the hashed content, so they cannot be changed without breaking the hash.
        self.field_hashes = field_hashes if field_hashes is not None else fingerprints(data)
        self.hash = hash or self.compute_hash()

    def compute_hash(self):
        # sort_keys makes the JSON deterministic, so the hash is repeatable
        payload = json.dumps(
            {
                "index": self.index,
                "timestamp": self.timestamp,
                "data": self.data,
                "field_hashes": self.field_hashes,
                "previous_hash": self.previous_hash,
                "nonce": self.nonce,
            },
            sort_keys=True,
        )
        return sha256_text(payload)

    def mine(self, difficulty=DIFFICULTY):
        """Proof of work: change nonce until the hash starts with N zeros."""
        target = "0" * difficulty
        self.hash = self.compute_hash()
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash = self.compute_hash()

    def to_dict(self):
        return {
            "index": self.index,
            "timestamp": self.timestamp,
            "data": self.data,
            "field_hashes": self.field_hashes,
            "previous_hash": self.previous_hash,
            "nonce": self.nonce,
            "hash": self.hash,
        }


class Blockchain:
    def __init__(self, difficulty=DIFFICULTY):
        self.difficulty = difficulty
        self.chain = []
        self._create_genesis()

    @classmethod
    def from_blocks(cls, difficulty, blocks):
        """Rebuild a chain from stored block dicts WITHOUT recomputing any hash,
        so tampering that happened before a restart is still visible afterwards."""
        bc = cls.__new__(cls)
        bc.difficulty = difficulty
        bc.chain = [Block(**b) for b in blocks]
        return bc

    def _create_genesis(self):
        genesis = Block(0, time.time(), {"note": "Genesis block"}, "0" * 64)
        genesis.mine(self.difficulty)
        self.chain.append(genesis)

    @property
    def last_block(self):
        return self.chain[-1]

    def add_record(self, data):
        block = Block(
            index=len(self.chain),
            timestamp=time.time(),
            data=data,
            previous_hash=self.last_block.hash,
        )
        block.mine(self.difficulty)
        self.chain.append(block)
        return block

    # ------------------------------------------------------------------ validation
    def validate_report(self):
        """Full, structured validation result.

        Returns a dict with:
          valid     True when every check on every block passes
          blocks    one entry per block with the detail of each check
          problems  flat list of failures, each with type, expected and actual values
          summary   counts and the indices of flagged blocks
        """
        target = "0" * self.difficulty
        blocks, problems = [], []

        for i, block in enumerate(self.chain):
            recomputed = block.compute_hash()
            checks = {}

            # 1. block hash
            hash_ok = block.hash == recomputed
            checks["hash"] = {"ok": hash_ok, "stored": block.hash, "recomputed": recomputed}
            if not hash_ok:
                problems.append({
                    "block": i, "type": "hash_mismatch",
                    "reason": "Data was modified: stored hash does not match recomputed hash",
                    "expected": block.hash, "actual": recomputed,
                    "detail": f"Block {i} stores hash {block.hash[:16]}... but its content now hashes to {recomputed[:16]}...",
                })

            # 2. field fingerprints (which field was edited)
            items = []
            for key in sorted(set(block.data) | set(block.field_hashes)):
                expected = block.field_hashes.get(key)
                actual = fingerprint(block.data[key]) if key in block.data else None
                if expected == actual:
                    state = "match"
                elif expected is None:
                    state = "added"
                elif actual is None:
                    state = "removed"
                else:
                    state = "modified"
                items.append({"field": key, "ok": state == "match", "state": state,
                              "value": block.data.get(key), "expected": expected, "actual": actual})
                if state != "match":
                    problems.append({
                        "block": i, "type": "field_modified", "field": key,
                        "reason": f"Field modified: '{key}' no longer matches its recorded fingerprint",
                        "expected": expected, "actual": actual,
                        "detail": f"Field '{key}' in block {i} was {state}; its fingerprint changed from "
                                  f"{(expected or 'none')[:16]}... to {(actual or 'none')[:16]}...",
                    })
            checks["fields"] = {"ok": all(item["ok"] for item in items), "items": items}

            # 3. proof of work
            pow_ok = block.hash.startswith(target)
            checks["pow"] = {"ok": pow_ok, "required": self.difficulty, "prefix": block.hash[:self.difficulty + 2]}
            if not pow_ok:
                problems.append({
                    "block": i, "type": "pow_failed",
                    "reason": "Hash does not satisfy proof-of-work",
                    "expected": target + "...", "actual": block.hash[:self.difficulty + 2] + "...",
                    "detail": f"Block {i} hash must start with {self.difficulty} zeros but starts with '{block.hash[:self.difficulty]}'",
                })

            # 4. link to previous block (compared with the RECOMPUTED previous hash, so an
            #    edit to block i-1 is exposed as a broken link in block i)
            if i == 0:
                checks["link"] = {"ok": True, "applicable": False, "stored_previous": block.previous_hash, "actual_previous": None}
            else:
                actual_prev = self.chain[i - 1].compute_hash()
                link_ok = block.previous_hash == actual_prev
                checks["link"] = {"ok": link_ok, "applicable": True,
                                  "stored_previous": block.previous_hash, "actual_previous": actual_prev}
                if not link_ok:
                    problems.append({
                        "block": i, "type": "link_broken",
                        "reason": f"Broken link: previous_hash does not match hash of block {i - 1}",
                        "expected": block.previous_hash, "actual": actual_prev,
                        "detail": f"Block {i} points to previous hash {block.previous_hash[:16]}... "
                                  f"but block {i - 1} now hashes to {actual_prev[:16]}...",
                    })

            own_ok = hash_ok and checks["fields"]["ok"] and pow_ok
            status = "valid" if own_ok and checks["link"]["ok"] else ("tampered" if not own_ok else "broken_link")
            blocks.append({"index": i, "status": status, "valid": status == "valid", "checks": checks})

        flagged = [b["index"] for b in blocks if not b["valid"]]
        summary = {
            "total_blocks": len(self.chain),
            "records": max(len(self.chain) - 1, 0),
            "valid_blocks": len(self.chain) - len(flagged),
            "flagged_blocks": len(flagged),
            "flagged": flagged,
            "first_invalid": flagged[0] if flagged else None,
            "problem_count": len(problems),
        }
        return {"valid": not problems, "blocks": blocks, "problems": problems, "summary": summary}

    def validate(self):
        """Returns (is_valid, problems)."""
        report = self.validate_report()
        return report["valid"], report["problems"]

    def verify_record(self, record_id):
        """Find a record by its id and check its block (and the chain around it)."""
        report = self.validate_report()
        for block in self.chain[1:]:
            if block.data.get("record_id") == record_id:
                i = block.index
                own = [p for p in report["problems"] if p["block"] == i and p["type"] != "link_broken"]
                link = [p for p in report["problems"] if p["block"] == i and p["type"] == "link_broken"]
                # If the NEXT block's link to this one is broken, this block was changed
                # (even if the attacker recomputed its hash), so it is not authentic.
                nxt = [p for p in report["problems"] if p["block"] == i + 1 and p["type"] == "link_broken"]
                return {"found": True, "block": block.to_dict(),
                        "authentic": not own and not nxt,
                        "link_broken": bool(link),
                        "chain_valid": report["valid"],
                        "issues": own + nxt + link}
        return {"found": False}

    # ------------------------------------------------------------------ JSON export / import
    def to_list(self):
        return [b.to_dict() for b in self.chain]

    def save(self, path):
        with open(path, "w") as f:
            json.dump({"difficulty": self.difficulty, "chain": self.to_list()}, f, indent=2)

    @classmethod
    def load(cls, path):
        with open(path) as f:
            raw = json.load(f)
        return cls.from_blocks(raw["difficulty"], raw["chain"])
