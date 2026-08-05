---
name: topic_widget_launch
description: Pricing model, vendor integration status, and open questions for the Widget launch (demo)
metadata:
  type: project
---

# Widget Launch

**Started 2026-04-10** · status 🟢 in progress

## Pricing
Decided 2026-05-02: **tiered**, not flat-rate. Reasoning: flat-rate under-priced the top 10% of
usage by roughly 3x based on the beta cohort's usage distribution — a flat price would have meant
subsidizing power users with everyone else's margin.

## Vendor integration
Rate-limited at 100 req/min on the vendor's side. Backoff + jitter implemented 2026-04-19 after an
incident where a burst of retries during a vendor outage made the rate-limiting worse, not better
(see `logs/2026-04-18_vendor_api_incident.md`). Holding steady since — no retries triggered in the
last two weeks of monitoring.

## Open questions
1. Whether the tiered pricing should have a free tier at all, or start at tier 1.
2. Whether the vendor contract needs renegotiating before the rate limit becomes a real ceiling —
   current usage is at ~60% of the limit at peak.
