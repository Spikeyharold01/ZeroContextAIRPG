# Deterministic structured-output recovery

The only accepted recovery flow is:

`Pydantic validation always runs → syntax failure and repair enabled → json_repair once → Pydantic validation → accept or reject`.

`json_repair` is a syntax repair tool, not a semantic validator. Valid JSON with a
wrong type, missing or unknown field, invalid enum/range, inconsistent arithmetic,
or invalid domain operation is rejected immediately. No LLM, provider regeneration,
permissive alternative decoder, or repeated repair loop is used. Rejected hidden
state is never returned as persistable state; independently separated visible
narrative may be retained by a future orchestration layer, which remains responsible
for turn advancement.

Setting `enabled = false` disables repair only. Direct Pydantic validation still
runs: valid output is accepted, while syntax, schema, and semantic failures remain
fail-closed.

The runtime dependency is the PyPI package `json-repair` (`>=0.39,<0.60`). It is
MIT-licensed, pure Python, and performs repair entirely offline after installation.
It has no model or network runtime requirement and is therefore expected to suit
Android/Python and constrained devices subject to the configured per-output byte,
depth, key, array, and execution-time safeguards. The installed version must be
recorded by release/CI checks because it is intentionally bounded rather than vendored.
