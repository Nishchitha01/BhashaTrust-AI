# BhashaTrust AI local rights ledger

This directory contains a local-only Hardhat deployment of `RightsLedger`. It
anchors SHA-256 hashes of canonical rights-record representations. The complete
signed payload remains in SQLite; personal data, dataset content, public keys,
signatures, API keys, and private keys are not written to the contract.

## Architecture and data flow

1. A signed rights assertion is stored in the existing `rights_records` table.
2. The user explicitly requests anchoring for a record and a purpose/context.
3. FastAPI rechecks the existing licensing engine. The record must have a valid
   signature, be the current signed record applied to the decision, and be
   `APPROVED` for that request.
4. The backend canonicalizes the database record plus policy version and hashes
   it with SHA-256.
5. The local Hardhat node accepts one append-only write from the contract owner.
   SQLite stores transaction metadata and the separate `rights_chain_anchors`
   status. A node failure never changes a licensing decision.
6. Verification recomputes the current database-record hash and compares it
   with the digest returned from the contract.

The blockchain shows that a particular digest was anchored on this local network
at a block and by a transaction sender. It does **not** prove legal ownership,
signer authority, or the truth of the licensing claim.

## Install and test

Node.js 24 is supported by the local setup; the Hardhat project uses the
JavaScript-config-compatible Hardhat 2 line, ethers, and OpenZeppelin Ownable.
From the project root in PowerShell:

```powershell
Set-Location .\blockchain
npm ci
npm run compile
npm test
```

Tests deploy to Hardhat's in-memory chain. They do not use a public RPC.

## Start the local chain and deploy

In the first PowerShell terminal, keep the local node running. Output is
discarded so Hardhat's built-in development account keys are not copied into
logs:

```powershell
Set-Location .\blockchain
npm run node -- --hostname 127.0.0.1 --port 8545 *> $null
```

In a second terminal, deploy only to the configured local node:

```powershell
Set-Location .\blockchain
npm run deploy:local
```

The deployment command reports the contract address and owner address. These
are public identifiers. Hardhat's built-in accounts are for local development
only and must never be funded or reused on a public chain.

## Backend connection

Set these variables in `backend/.env` (see `backend/.env.example`) or in the
backend process environment:

```text
BLOCKCHAIN_RPC_URL=http://127.0.0.1:8545
BLOCKCHAIN_CHAIN_ID=31337
BLOCKCHAIN_NETWORK_NAME=hardhat-local
BLOCKCHAIN_CONTRACT_ADDRESS=<address printed by deploy:local>
BLOCKCHAIN_SENDER_ADDRESS=<local owner address printed by deploy:local>
BLOCKCHAIN_TX_TIMEOUT=20
```

No private key is required: the backend asks the local Hardhat node to sign a
transaction from its unlocked owner account. The backend refuses non-loopback
RPC hosts, non-31337 chain IDs, and senders that are not the configured
contract owner. It sends no transaction automatically. `POST
/api/rights-records/{id}/anchor` is the explicit local write request.

Start the backend from the project root after installing backend dependencies:

```powershell
Set-Location .
.\backend\.venv-local\Scripts\python.exe -m pip install -r backend\requirements.txt
.\backend\.venv-local\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

## API

- `GET /api/datasets/{dataset_id}/rights-ledger` lists database records and
  their current anchoring status.
- `POST /api/rights-records/{record_id}/anchor` anchors a record after the
  backend independently approves the requested purpose and context.
- `GET /api/rights-records/{record_id}/anchor` returns stored status without
  contacting the node.
- `POST /api/rights-records/{record_id}/verify-anchor` reads the local contract
  and verifies the current canonical hash.

Statuses distinguish `DATABASE_ONLY`, `PENDING`, `ANCHORED`, and `FAILED`.
An anchor status is never an authorization decision; current licensing checks
remain authoritative.

## Security and limitations

- The owner is a single local development account. Whoever controls that local
  node's owner account can append records; the contract provides no multisig,
  decentralization, governance, or key-recovery mechanism.
- Records cannot be removed or amended. A changed database record will fail
  hash verification and needs a new identifier for any later assertion.
- Data is public to anyone with access to the network. Only the digest and
  minimal policy metadata are placed on-chain, but hashes can still reveal
  equality or link records.
- Local Hardhat transactions have no public-chain finality and do not establish
  legal ownership or the truth of the submitted record.
- The backend validates an approved current license before anchoring, but
  verification of a historical hash does not re-evaluate the present license.

## Hackathon demonstration

1. Run the contract tests and deploy to the local Hardhat node.
2. Configure the contract address and local owner address in `backend/.env`.
3. Start FastAPI and the React dashboard.
4. Open Rights Ledger and inspect the database-only status for a signed record.
   The current prototype has no trusted reviewer workflow, so its seeded and
   UI-registered records cannot be independently approved and must not be
   anchored as authorized training data.
5. The in-memory contract tests demonstrate local transaction recording,
   duplicate and unauthorized-write rejection, retrieval, and hash checking.
   An end-to-end local anchor requires an independently approved record, which
   this prototype cannot currently produce through its UI.
6. The test suite also verifies modified hashes without tampering with the live
   demo database. Do not represent an in-memory test transaction as a deployed
   local-chain transaction.
