# Memory Index (demo)

> This is a **fictional example** showing the format only — no real project data.
> Every entry: one line, "file exists + one-line hook". Details always live in the source-of-truth file, never here.
> Conflict resolution: your explicit instruction > topic source file > trigger-file projection > this index > log snapshots. Same-tier conflicts: newer date wins; if it can't be resolved, ask the user — never silently split the difference.

## 🧭 Architecture / Strategy
- 🟢⭐⭐⭐ [Widget Launch Plan](topic_widget_launch.md) — pricing decided 2026-05-02: tiered, not flat-rate; two open questions left, see source
- 🔴⭐⭐ [Vendor API rate-limit incident](logs/2026-04-18_vendor_api_incident.md) — root cause = missing backoff, not a capacity issue; fix shipped, monitoring for a week before closing

## 💾 Infrastructure
- 🔴🔑⭐⭐⭐ [Credentials — single source of truth](reference_credentials.md) — passwords/tokens live only here; never copied into outward-facing material
- 🟢⭐⭐ [Backup pipeline health check](feedback_backup_pipeline_notes.md) — weekly check confirmed 3 independent copies; don't skip the offsite one

## ⚙️ Workflow preferences
- 🔴⭐⭐ [Review before merge](feedback_review_before_merge.md) — user wants a walkthrough of the diff before any merge, even trivial ones; burned once by a "trivial" rename that broke a build
