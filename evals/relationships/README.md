# Structured claim relationship contract cases

`structured-claims-v1.json` freezes small synthetic, source-shaped cases for every decisive and
abstaining behavior in `structured-claims-v1`. CI loads the file through the production graph and
relationship builders, then compares exact `(label, predicate, normalized_value)` sets.

Run it with:

```bash
uv run pytest tests/test_relationships.py
```

This is a deterministic contract suite, not a human-reviewed accuracy benchmark and not evidence
about a live incident. It prevents ontology/rule changes from silently changing known behavior.
The next relationship-evaluation milestone will capture real claim pairs, blind them for review,
measure per-label precision/recall plus abstention coverage, and use that baseline to decide
whether a free local NLI model or LLM proposer adds enough value to ship.
