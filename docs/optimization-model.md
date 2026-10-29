# Licensing cost allocation and local optimization

## Cost data

The prototype uses the structured records in `backend/cost_data.py`. These are
synthetic demonstration values, not supplier quotes or real licensing terms.
Each record supplies a flat USD cost in cents and a hypothetical integer utility
score from 0 to 100. Dataset names without a configured cost are excluded from
optimization with `COST_UNAVAILABLE`. The API currently supports USD only; it
does not convert currencies or model per-token, per-seat, or recurring fees.

## Eligibility gate

Before optimization, the service evaluates every requested dataset through the
existing `evaluate_license` engine. That evaluation uses the latest valid signed
rights record when present and checks the requested purpose, context, geography,
redistribution, derivative-work permissions, expiry, revocation, and evidence
status. Registry flags and valid signatures are not independent legal
verification. Public registration has no trusted reviewer path, so datasets
without independently verified evidence remain in review. Only `APPROVED`
datasets with known cost data are passed to the optimizer. Selected IDs are
checked against the licensing engine again immediately after the solver returns,
and the result is validated for eligibility and budget compliance before it is
stored or returned.

## Optimization model

For each candidate dataset `i`, `q_i` is its synthetic utility score, `c_i` is
its licensing cost in integer USD cents, and `x_i` is either 0 or 1:

```text
maximize   sum(q_i * x_i)
subject to sum(c_i * x_i) <= B
           x_i in {0, 1}
```

The local baseline uses deterministic exhaustive subset search. It returns an
exact maximum-utility feasible subset for up to 20 priced, eligible candidates.
Ties prefer lower total cost, then lexicographically smaller dataset IDs. Inputs
with more than 20 candidates are rejected instead of silently switching to a
heuristic. The worst-case search examines `2^n` subsets, so this exact method is
intended for the small prototype dataset set, not large production corpora.

## Optional QpiAI Opt platform solver

The API accepts `solver: "qpiai_opt"` to request the official
[QpiAI Opt SDK](https://opt-docs.qpiai.tech/) (version 0.0.9). It models each
already-eligible dataset as a binary variable and sends the same objective and
budget constraint as a MILP:

```text
maximize   sum(q_i * x_i)
subject to sum(c_i * x_i) <= B
           x_i in {0, 1}
```

Only candidates that passed licensing and have configured cost data enter the
model. A request must also set `confirm_remote_job: true`; without that
per-request confirmation, the local exact baseline runs and the response
reports the fallback. Credentials are read from process environment,
`backend/.env`, or project-root `backend.env` using the `QPIAI_OPT_API_KEY`
variable. The installed SDK call is `solve(model, mode="platform",
client=Client(api_key=...))`; its MILP result contains a variable-name-to-value
`solution` mapping, execution mode, status, model status, and job ID. The
adapter checks platform mode and binary assignment shape, then the backend
rechecks licensing and budget feasibility. It records solver status but does
not require an optimal status; a feasible incumbent is not proof of
optimality. The result is not marked optimality-guaranteed.

The SDK client defaults to a 60-second HTTP request timeout, while the
installed MILP solver defaults to a 600-second job wait timeout. The adapter
does not override either setting. Platform mode creates a remote job and may
consume credits. No platform execution is performed in the automated tests or
demo by default.

The current root `backend.env` uses variable names the adapter does not read;
the adapter does not interpret `QPIAI_OPT_ACCESS_TOKEN` as an API key or apply
`QPIAI_OPT_API_URL` as a custom endpoint. Confirm the provider credential type
before configuring a valid `QPIAI_OPT_API_KEY`; no live authentication has
been attempted.

The response is treated as untrusted: only a complete binary assignment from a
platform-mode result is accepted, and the existing licensing engine and budget
validator run again before accepting the result. The SDK job ID and model status
are recorded when returned. Missing credentials and SDK/authentication errors
are reported as explicit local-baseline fallbacks. A remote result that fails
the independent safety checks is rejected and audited. QpiAI results are not
reported as guaranteed optimal.

The project installs `qpiai-opt` without its `[quantum]` extra because that
extra currently pins `qpiai-quantum==0.3.0`; BhashaTrust uses and tests
`qpiai-quantum==0.7.5` for its separate QAOA/QCloud circuit path.

## Limits

An optimal result is only optimal for the supplied synthetic utility scores,
flat demo costs, budget, and current policy evaluation. A valid signature still does not establish legal ownership or signer authority.
Missing redistribution or derivative-work terms result in review when those
uses are requested, and explicit prohibitions reject them. The optimizer does not
replace policy review, validate commercial market pricing, or provide currency
conversion. The API records input IDs, selected IDs, validation outcome, and
result in the local audit log; the local log is not externally anchored.
