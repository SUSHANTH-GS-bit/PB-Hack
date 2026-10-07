# ChainCert - Blockchain-Based Tamper-Proof Record Verification

A blockchain built from scratch in Python that stores certificate and marks records as
hash-linked blocks, with a Flask dashboard where judges edit a block and see exactly which
hash no longer matches. Records are kept in a SQLite database, so they survive a restart.

## 1. Run it (2 minutes)

```bash
pip install -r requirements.txt
python app.py              # then open http://127.0.0.1:5000
python app.py --seed       # optional: also loads the 12 sample certificates on start
```

Run the tests (25 tests): `python -m unittest -v`

Environment variables (optional): `CHAINCERT_DB` (database file path), `PORT` (default 5000).

## 2. What is new in this version

| # | Feature | Where |
|---|---|---|
| 1 | **Exact tamper detection.** Every failed check shows the expected and actual values side by side, with the differing characters highlighted. The report names the block, the check, and the edited field. | `blockchain.py` `validate_report()`, inspector and report in the dashboard |
| 2 | **Professional dashboard.** Sidebar navigation, statistics row, block explorer, block inspector, validation report, activity log. Text labels and vector icons only, no emojis. | `templates/index.html` |
| 3 | **12 sample certificates** for the live demo. The first is Rahul Verma, 85 marks (CERT001), matching the slides. | `samples.py` |
| 4 | **Persistent storage** in SQLite. Blocks, tampering and the activity log all survive a Flask restart. | `storage.py` |
| 5 | **`requirements.txt`** | `requirements.txt` |

## 3. Files

| File | Purpose |
|---|---|
| `blockchain.py` | `Block` and `Blockchain`: SHA-256 hashing, proof of work, linking, detailed validation report |
| `storage.py` | SQLite persistence (blocks, difficulty, activity log) using only the standard library |
| `samples.py` | Fictional sample certificates for the demo |
| `app.py` | Flask REST API and server (`create_app()` factory) |
| `templates/index.html` | Dashboard (single page, no external assets, works offline) |
| `test_blockchain.py` | Core logic and detailed detection tests |
| `test_app.py` | API, sample data and restart persistence tests |
| `chaincert.db` | Created automatically on first run (not shipped) |

## 4. How tamper detection works

Each block stores: index, timestamp, data (the record), field fingerprints, previous hash, nonce, hash.
The block hash is SHA-256 over all of those fields except the hash itself.
`validate_report()` runs four checks on every block:

| Check | Passes when | Catches |
|---|---|---|
| Block hash | stored hash equals the hash recomputed from the content | any edit to the block |
| Field fingerprints | each field matches the SHA-256 fingerprint recorded at creation | **which field** was edited (also added or removed fields) |
| Proof of work | stored hash starts with the required zeros | a hash that was typed in or recomputed without mining |
| Link | the block's `previous_hash` equals the recomputed hash of the previous block | an edit to an earlier block, even if its hash was recomputed |

Block status in the explorer:
- **VALID**: all four checks pass.
- **TAMPERED**: the block's own hash, fields or proof of work fail.
- **BROKEN LINK**: the block itself is fine, but it points to an earlier block that changed.

### What the dashboard shows for a plain edit (judge changes 85 to 95 in block 1)
- Block 1: **TAMPERED**. Check 1 shows the stored hash and the recomputed hash with every differing character highlighted ("63 of 64 characters differ"). Check 2 names the field `marks` and shows its recorded and current fingerprints.
- Block 2: **BROKEN LINK**. Check 4 shows the previous hash stored in block 2 against the actual hash of block 1.
- The Validation Report lists all three failures: Block hash mismatch, Field fingerprint mismatch, Previous hash mismatch.

### The advanced attacker option
Tick "Advanced attacker" in the Simulate tampering panel. The tool then also recomputes the edited block's fingerprints and hash. The block's own hash looks consistent again, but Block 2's link still fails and the new hash does not satisfy proof of work. To hide the edit completely the attacker would have to re-mine every later block, which is the honest limit of a single-node prototype: a real network of many nodes would reject the forged chain.

## 5. API

| Method and path | Purpose |
|---|---|
| `GET /api/chain` | Chain, `valid`, per-block `blocks` checks, flat `problems` list, `summary` |
| `POST /api/add` | Issue a record (mines a block). Body: `record_id`, `student_name`, `course`, `marks`, `issuer`, `issue_date` |
| `POST /api/tamper` | Demo only. Body: `index`, `field`, `value`, optional `rehash` |
| `GET /api/verify/<record_id>` | Authentic or tampered, with the exact issues |
| `POST /api/seed` | Load the 12 sample certificates (skips ones already present) |
| `GET /api/samples` | The sample list |
| `GET /api/events` | Activity log |
| `GET /api/export` | Download the chain as JSON |
| `POST /api/reset` | New chain from a fresh genesis block |

## 6. Persistence

All changes are written to `chaincert.db` (SQLite) inside one transaction, including simulated
tampering. On start the stored blocks are loaded exactly as saved and no hash is recomputed, so a
tampered chain is still flagged after a restart. The activity log notes "Chain restored from storage".
To start clean, press **Reset chain** in the dashboard or delete `chaincert.db`.

## 7. Live demo script (3 to 4 minutes)

1. Start with `python app.py`. Open the dashboard. Click **Load sample certificates**. Show 13 blocks, "Chain integrity verified" and 100%.
2. Click a block in the explorer. In the inspector point out the four green checks and that the previous hash matches the block before.
3. In **Verify record**, type `CERT001` and verify. It shows AUTHENTIC.
4. Hand the keyboard to a judge. In **Simulate tampering** choose Block #1, field `marks`, value `95`, click **Apply edit**.
5. The status turns red, Block 1 shows TAMPERED, Block 2 shows BROKEN LINK, and the report lists the three failed checks. In the inspector show the highlighted stored and recomputed hashes.
6. Verify `CERT001` again: TAMPERED. Verify `CERT005`: still AUTHENTIC, so only the attacked record is flagged.
7. Optional restart proof: stop the server, start it again. The tampered state is still there.
8. Click **Reset chain** and **Load sample certificates** to start over.

## 8. Likely viva questions

- **What is a hash?** A fixed-length fingerprint of data. Same input gives the same output, any change gives a totally different output, and you can't reverse it.
- **Why SHA-256?** Standard, collision-resistant, in Python's `hashlib`, used by Bitcoin.
- **What is the nonce?** A counter changed during mining until the hash meets the difficulty target.
- **What are the field fingerprints for?** They show which field was edited, not just that something changed.
- **What if someone recomputes the hash after editing?** The next block's `previous_hash` no longer matches, so the break moves down the chain. They would have to re-mine every later block.
- **Is this a real blockchain?** It is a single-node chain. A production one adds a peer-to-peer network and consensus.
- **Why SQLite?** It ships with Python, needs no setup, and writes each change in one transaction.
- **Why not just a database?** A DB admin can silently edit a row. Here an edit is mathematically detectable.
- **Privacy?** In production store only the hash of the certificate on the chain and keep personal data off-chain.

## 9. Notes

- Sample names and the issuer "Sample University" are fictional, for demonstration only.
- The hash now also covers the per-field fingerprints, which are derived from the record data, so the idea on the slides (the hash covers the record, previous hash and nonce) still holds.
- Ideas if you have spare time: QR code per record, hashing an uploaded PDF certificate, digital signatures, several nodes with a longest-valid-chain rule.
