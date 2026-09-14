# Agent trajectory evaluation artifacts

This directory pins the content-addressed release policy used to assess observable offline agent
trajectories. It contains no generated answers, model credentials, approvals, or production release
evidence.

`release-policy-v1.json` requires three chronological trajectory captures, at least 30 total cases,
independently adjudicated grounded-answer results, a passing relationship benchmark, and a complete
stable drift chain. Observable policy, evidence inspection, claim trace, and strict trajectory pass
rates must all be 100%. Passing the policy makes a candidate eligible only for separate human
review; execution remains hard-disabled.

Reproduce the checked-in policy identity:

```bash
uv run atlas-pulse-evaluate-agent-trajectories write-policy \
  --output /tmp/atlas-release-policy.json
diff -u evals/agent-trajectories/release-policy-v1.json /tmp/atlas-release-policy.json
```

The full capture, scoring, drift, and assessment workflow is documented in
[`docs/agent-trajectory-release.md`](../../docs/agent-trajectory-release.md).
