# Evaluation plan: Laws of the Game RAG app

## End state

A repeatable evaluation process for this RAG app:

- a frozen golden set;
- one command that runs retrieval metrics, generation and a judge, and logs one row per run;
- a judge validated against my own grades;
- a regression gate checked against a recorded baseline (B0);
- written documentation.

## Rules (read these first)

1. **The plan is fixed.** Work on the current stage only, and do the stages in order.
2. **New ideas go to the parking lot.** They enter the plan only if I decide so, with a dated entry in the change log at the bottom.
3. **The golden set is frozen** (v2, checksum in `golden_set.sha256`). It is never edited. Corrections go in `GOLDEN_SET_ERRATA.md` and are applied only in a deliberate new version, followed by a full re-grade and a new baseline.
4. **One change per experiment.**
5. **A stage is finished only when every "done when" line is true.**

**Current stage: 1**

---

## Stage 0: Freeze and record the baseline (no new code, about 30 minutes)

- [x] 0.1 Commit `golden_set.jsonl`, `golden_set.sha256`, `GOLDEN_SET_CHANGELOG.md` and `GOLDEN_SET_ERRATA.md`. Tag the commit `golden-v2`.
- [x] 0.2 Save `answer_review_fixed.csv` as `baseline/B0_generation_graded.csv`. These graded answers are the B0 generation outputs. Do not regenerate them.
- [x] 0.3 Run retrieval only on the frozen file: `python evaluate.py golden_set.jsonl --namespace laws-1200-150`. Save the output as `baseline/B0_retrieval.json`.
- [x] 0.4 Write `baseline/B0.md` with two tables:
  - Retrieval: recall@1/3/5/10 and MRR (strict and page-based), from 0.3.
  - Generation: 36 of 41 correct, 4 partial, 1 incorrect, 5 of 5 correct refusals, judge-versus-me agreement 39 of 41, 0 of 41 false refusals, 0 of 41 unsupported citations.

**Done when:** `baseline/B0.md` exists with both tables and the commit is tagged.

## Stage 1: One-command harness (`run_eval.py`, about 2 hours)

Folders: `evals/golden_set/` (frozen golden set and checksum), `evals/baseline/` (B0), `evals/runs/` (one folder per run, plus `results.csv`).

The script:

- checks `golden_set.sha256` and stops if the file has changed;
- runs retrieval metrics, then generation with `--generate`, then the judge with `--judge`;
- saves `runs/<date>_<label>/` containing `config.json` (namespace, chunking description, k, models, golden set hash, git commit), the results JSON and `summary.md`;
- appends one row to `runs/results.csv` (label, date, recall@1/3/5/10, MRR, false refusals, unsupported citations, correct refusals, judge verdict counts, tokens, cost).

**Done when:** `python run_eval.py --label B0-check --generate --judge` runs end to end, and its retrieval numbers equal B0 exactly.

## Stage 2: A judge I can trust (about 2 hours)

- [ ] 2.1 Change the judge so it is given the text of the question's expected pages and told to use only the reference answer and those pages.
- [ ] 2.2 Test it against my 41 human grades of the B0 answers. Record the agreement and list every disagreement.
- [ ] 2.3 Planted-error test: take 10 B0 answers and change one fact in each, then judge both versions. Record how many planted errors it catches and how many correct originals it fails.

**Done when (thresholds are fixed now):**

- agreement with my grades is at least 37 of 41 (90%);
- the judge never grades G32 (my INCORRECT) as correct;
- it catches at least 8 of 10 planted errors;
- it fails at most 1 of the 10 correct originals.

If the thresholds aren't met, the judge prompt may be revised, at most twice. Log each attempt and its result, so the judge isn't quietly tuned to my 41 grades.

## Stage 3: Regression gate (about 1 hour)

- [ ] 3.1 Write `gate.json` with thresholds taken from B0 (using the final judge's B0 counts):
  - recall@5 no more than one question below B0 (2.5 points);
  - MRR no more than 0.03 below B0;
  - false refusals 0, unsupported citations 0, correct refusals 5 of 5;
  - judge-correct no more than 1 below B0.
- [ ] 3.2 `python run_eval.py --gate` exits non-zero and names the failed checks.

**Done when:** the gate passes on B0 and fails on a deliberately broken configuration (for example `-k 1`).

## Stage 4: Document the process (about 1 hour)

- [ ] 4.1 Add an "Evaluation process" section to the README: how to run it, what each metric means, how to read `runs/results.csv`, the golden set freeze rule, and known limits (small set, the errata, the judge's limits).
- [ ] 4.2 Link `EVAL_RUN_1.md` and `EVAL_RUN_2_GENERATION.md` as historical reports and `baseline/B0.md` as the baseline.

**Done when:** from a fresh clone, the README alone is enough to reproduce B0's retrieval numbers.

---

## Stage 5: Use the process (starts only after Stage 4 is done)

Each experiment is one change, one run through the harness, one row in `results.csv`, and one sentence of conclusion. The order is fixed.

1. E1: k = 3 and k = 10 (against 5)
2. E2: Voyage reranker on the top 20
3. E3: chunk size 400, 800 and 2000 (against 1200)
4. E4: structure-aware chunker with a heading prefix
5. E5: formatting-aware extraction (strikethrough and underline in the 2026/27 changes section)
6. E6: Haiku against Sonnet for generation
7. E7: full-context baseline (the whole rulebook in the prompt)

**Done when:** all seven rows are recorded and the README results table is updated.

---

## Parking lot (not part of the plan)

- Near-miss unanswerable questions, as a separate file, never merged into the frozen set
- Golden set v3 with the errata applied and a full re-grade
- Hybrid keyword search
- Chunks that cross page boundaries
- FastAPI endpoint, Docker, GitHub Actions
- StatsBomb data, DuckDB and the agent
- Comparing different judge models

## Change log

- 2026-10-18: plan created. Golden set frozen at v2.
- 2026-10-18: Stage 0 complete (baseline recorded in `evals/baseline`). Stage 1 started: `run_eval.py` written.
