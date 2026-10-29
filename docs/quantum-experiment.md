# Phase 6: QpiAI Quantum experiment

## SDK and authentication

The project uses the official `qpiai-quantum` SDK, pinned to 0.7.5. The SDK's
QCloud authentication entry point is `QpiAIQuantumAuth.login(api_key=...)`; its
documented environment variable is `API_KEY`. The local experiment uses the
SDK's `Circuit` and `StatevectorSimulator` classes directly. QCloud credentials
are read from `backend/.env`, separately from the Phase 5 Opt token. A live
authentication attempt or remote job is never made by local mode.

## Experiment

`POST /api/quantum-experiments` evaluates every requested dataset through the
existing licensing engine first. Only currently `APPROVED` datasets with
configured cost and utility data become binary decision variables. It checks
eligibility again after sampling and validates selected IDs and the budget
before accepting a result.

For each candidate, `x_i` is its selection bit. QUBO construction uses the
existing Phase 5 knapsack conversion, including binary slack bits for the
licensing budget:

```text
minimize -sum(q_i*x_i) + A*(sum(c_i*x_i) + sum(2^k*s_k) - B)^2
```

Costs and budget are divided by a common integer scale before QUBO construction
to reduce coefficient size without changing feasible selections. The local
experiment builds a one-layer (p=1) QAOA circuit with fixed gamma and beta
angles; it does not search or tune an angle grid. It applies Hadamards, the
cost Hamiltonian's RZ/RZZ rotations, an RX mixer, and measurements on all
qubits, then samples QpiAI's local statevector simulator with the request seed.
Only sampled bitstrings within the budget are considered; the selected sample
is ranked by QUBO energy and then by frequency and utility. The exhaustive
local optimizer is returned as a classical baseline. This small p=1 experiment
does not demonstrate quantum advantage.

## QCloud mode

Set `mode: "qcloud"` only when an approved operator intentionally requests a
remote run. The API also requires `confirm_remote_job: true`; otherwise it logs
that execution did not start and returns without authentication or submission.
The adapter passes a configured API key to
`QpiAIQuantumAuth.login(api_key=...)`, then calls the SDK's
`Circuit.run(..., device_name="QpiAI-QSV-Simulator")` and reads its measurement
counts and job ID. In the installed SDK, this device is a QCloud-hosted
simulator, not hardware; the SDK call defaults to a 500-second execution
timeout. QCloud simulator jobs are remote and may consume credits. No job is
submitted automatically. Missing credentials and authentication or execution
failures are returned as sanitized errors. The current mock-based adapter tests
do not establish remote service availability, billing, or hardware execution.
