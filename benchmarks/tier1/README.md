# Tier 1 synthetic fixtures

`generate.py` creates the deterministic 24-task repair set and six controls in
`tasks.jsonl`. The records intentionally share a compact repository description;
the task identity and category are the experimental factors. The fixture is
self-contained and does not download code or dependencies. External benchmark
repositories belong under `.bench/external` and are described separately.

The manifest is JSONL so a run can append results without rewriting prior
records. Keep oracle patches and any generated checkout under `.bench` during
execution.
