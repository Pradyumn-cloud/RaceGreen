# Tier 1 synthetic benchmark

`generate.py` creates the deterministic 24-task repair set and six controls in
`tasks.jsonl`. The records intentionally share a compact repository description;
the task identity and category are the experimental factors. The fixture is
self-contained and does not download code or dependencies. External benchmark
repositories belong under `.bench/external` and are described separately.

The checked-in manifests and patches are the benchmark inputs. Generated
checkouts and results belong under `.bench`, which is ignored by Git and must
not be committed.
