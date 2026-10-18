# Golden set changelog

The golden set (`golden_set.jsonl`) was revised after I saw evaluation results. Each change below was made because I inspected a result and found a flaw in the evaluation, not to improve a score. Tag these versions in git so earlier runs can be reproduced.

## v1: initial set (46 questions)

41 answerable and 5 unanswerable questions. Every key phrase was checked programmatically against the PDF text and against my chunk output.

## v2: changes after runs 1 and 2

| Question | Change | Why |
|---|---|---|
| N01, N02 | Added `alt_phrases` and expected pages | The rule is worded differently in up to five places. One key phrase counted retrieval as a miss when the answer was in the top 10. |
| N04 | Added `alt_phrases` and pages 120 and 180 | The model cited pages that state the rule in full. I had listed only the summary page. |
| G32 | Key phrase changed to "a goal may be scored directly from a corner kick" (page 149 only) | The old phrase also appears in the kick-off and goal-kick Laws, so a wrong chunk counted as a hit. |
| G04 | Key phrase now includes "entitled to an interval at half-time" | "Not exceeding 15 minutes" also describes extra time on page 103. |

## FROZEN at v2

`golden_set.jsonl` is frozen. Its SHA-256 is in `golden_set.sha256`, and the evaluation harness refuses to run if the file doesn't match.

I briefly applied reference-answer corrections as a "v3" after the answers had been graded. That was a mistake, because it made the existing grades inconsistent with the references, so I reverted it. The reference answers in the file are again exactly the text my grades were made against.

The corrections are kept in `GOLDEN_SET_ERRATA.md`. They would only be applied in a deliberate v3 with a full re-grade and a baseline reset.

Note: the retrieval-side fields (key phrases, alternative phrases, expected pages) changed between v1 and v2. Retrieval numbers recorded before v2 are not comparable with v2. The retrieval baseline (B0) must be recorded on the frozen v2 file.
