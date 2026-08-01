# CES pilot — first implementation pass on 9 wheat rust papers

## What's here

- `ctr_schema.py` — the domain-agnostic Comparative Trial Record (CTR) object
  model: Study, Unit, Condition, Outcome, Context, TierRecord.
- `wheat_rust_extraction.py` — populates that schema from the 9 papers'
  **search-snippet abstracts** (no full text fetched yet), plus 4 tier
  records judging comparability between metric pairs.
- `wheat_rust_ces_pilot.json` — the resulting dataset: 9 studies, 48 CTRs,
  4 tier records.
- `query_simulation.py` — simulates a researcher query against the dataset
  to see what actually falls out.

## What this pass proves

1. **The schema holds.** All 9 papers, despite differing designs (field
   hotspot screens, seedling Ug99 panels, multi-location cultivar trials,
   an integrated variety×fungicide trial), fit into the same Study/Unit/
   Condition/Outcome/Context shape without forcing.
2. **The tier distinctions are real, not hypothetical.** Concretely:
   - CI and FRS/TRS are **Tier 2 (convertible)** — CI is a documented,
     explicit transform of FRS/TRS (CI = severity × reaction constant),
     confirmed in S8's abstract.
   - CI and AUDPC are **Tier 3 (related, non-mergeable)** — same construct,
     different timing structure (point vs. trajectory), no conversion
     formula found anywhere in the sample.
   - Seedling infection type and field CI/AUDPC are **Tier 3** — different
     growth stage, different scale type, different biological question.
   - Rust severity and grain yield are **Tier 4 / different construct
     entirely** — and only 1 of 9 papers links them at all.

## The most important finding wasn't a tier — it was a coverage gap

Running the query simulation surfaced something neither of us predicted
going in: **in this 9-paper sample, no single variety or genotype is
evaluated in more than one study.** Every candidate is currently
`n_studies=1`. That means Tier-1 pooling — the "easy" case — currently has
*zero* records where it can actually be exercised, not because the metrics
aren't compatible, but because the varieties tested just don't overlap
across these particular 9 papers.

This is a good outcome for the pilot, not a bad one: it's exactly the kind
of finding a synthesis tool needs to be able to say out loud ("insufficient
cross-study evidence to pool — here is what exists, organized, with no
false confidence") rather than paper over. It also tells you something
concrete about scaling: whether this coverage gap is a property of this
specific 9-paper sample (likely — 9 papers is small and I didn't
deliberately search for the same varieties across studies) or a structural
property of the literature is now a testable question for the next batch
of papers, and it directly determines how valuable the pooling machinery
vs. the "organize and be honest about gaps" machinery will be in practice.

## What's explicitly NOT done yet (needs full text, not abstracts)

- Per-genotype numeric values for S1, S4, S6, S8 (abstracts only give
  cohort-level summaries or named subsets, not full tables).
- Entity resolution stress test — no aliasing/spelling conflicts showed up
  here because there was no cross-study overlap to resolve, so this
  hasn't actually been tested yet.
- A real search for cross-study variety overlap (e.g., deliberately
  searching for "Kakaba stem rust" as its own query to see if it appears
  in other independent trials beyond S3).
