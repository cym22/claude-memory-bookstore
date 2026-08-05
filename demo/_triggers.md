# Trigger table (demo excerpt)

> A hand-maintained inverted index: keyword → source file. This file grows large over time
> (in the real system it can pass 100K+ characters), so the rule is: **grep first, then read by
> line range** — never read the whole file, it will get truncated and you'll silently lose the
> back half.

- 🟢 widget launch · pricing · tiered plan · vendor rate limit → topic_widget_launch.md   [Pricing locked 2026-05-02: tiered (see decision doc). Vendor integration currently rate-limited at 100 req/min, backoff implemented 2026-04-19, holding steady since.]
- 🔴🔑 credentials · API key · token → reference_credentials.md   [Single source. Never paste into outward-facing docs, PRs, or chat exports — this rule exists because of a near-miss where a token almost shipped in a demo screenshot.]
- 🔴 review before merge · merge policy → feedback_review_before_merge.md   [User wants a walkthrough before merge, even for "trivial" changes. Triggered by a rename that silently broke a build — see 2026-03-02 incident.]
