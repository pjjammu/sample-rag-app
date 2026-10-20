# Evaluation of the Laws of the Game RAG app

This folder holds everything I use to measure the RAG app: the frozen golden set, the baseline, every run, and the validated LLM judge. This file records what I learned from the first runs and how the judge was built and validated. The plan I follow is in `EVAL_PLAN.md`.

## What is in this folder

| Folder | Contents |
|---|---|
| `golden_set/` | The frozen golden set (`golden_set.jsonl`), its checksum (`golden_set.sha256`), `GOLDEN_SET_CHANGELOG.md` and `GOLDEN_SET_ERRATA.md` |
| `baseline/` | B0: `B0.md`, `B0_retrieval.json` and `B0_generation_graded.csv` (my hand-graded answers) |
| `runs/` | One folder per run (config, results, summary, answer review file) and `results.csv` with one row per full run |
| `judge/` | `planted_errors.jsonl`, `attempts.jsonl` (every judge validation attempt) and the validation reports |

## Rules

1. The golden set is frozen. `run_eval.py` stops if the file doesn't match its checksum. Known defects go in the errata file and are applied only in a deliberate new version, followed by a full re-grade and a new baseline.
2. One change per experiment, and every full run is logged in `runs/results.csv`.
3. Any change to the judge prompt or model must be re-validated with `validate_judge.py` and logged.

## Commands

```
python run_eval.py --label E1-k10 -k 10                                      # retrieval metrics only (free)
python run_eval.py --label B0-check --generate --judge --pdf "<path to the Laws PDF>" --vs-b0
python validate_judge.py --pdf "<path to the Laws PDF>"                      # judge validation (counts as an attempt)
```

## What I learned from the first runs

| # | Lesson | Evidence | What I changed |
|---|---|---|---|
| 1 | **Check misses before trusting them.** Both questions that were never retrieved were false misses. | N01 and N02: the answer was in the top 10, worded differently from my key phrase. | Added alternative phrasings and expected pages. |
| 2 | **Check suspicious hits too.** A key phrase that appears in several Laws credits the wrong chunk. | G32: the phrase appeared in the kick-off, goal-kick and corner-kick Laws, so a kick-off chunk at rank 4 counted. G04: "not exceeding 15 minutes" also describes extra time. | Tightened both key phrases to text unique to the intended passage. |
| 3 | **Ranking is weaker than recall.** The right chunk is usually in the top 5, but not first. | First run (golden v1): recall@5 92.7%, recall@1 61.0%. | Reranking and chunking experiments are queued in the plan. |
| 4 | **Near-duplicate text across Laws causes confident wrong answers.** | G32: the exact corner-kick chunk ranked 6th and never reached the model. It saw the kick-off and goal-kick versions and a free-kick rule, and answered "goal kick" instead of "corner kick". | None yet. G32 stays in the set as a known failure. |
| 5 | **Page-boundary splitting damages chunks.** | A chunk that starts mid-list with no heading ranked first for G32. A 194-character fragment ended in the stray page title "The Corner Kick". | Cross-page chunking and heading prefixes are queued. |
| 6 | **A confident wrong answer is worse than a refusal.** | G32 again. | The prompt already tells the model to refuse when the excerpts don't contain the answer. |
| 7 | **"No unsupported citations" doesn't mean the citations are right.** | It only checks that a cited page was among the retrieved ones. G32 cited a page that didn't support the claim. | I describe it as "no fabricated page numbers". |
| 8 | **Text extraction can lose meaning.** The 2026/27 changes section marks deleted text with strikethrough and added text with underlines, both drawn as vector lines. Extraction drops them, so deleted rules look current. | N03: the model quoted a deleted sentence ("the kicker is cautioned...") next to the amendment that removed it. I confirmed this by rendering page 182. I haven't checked the other pages. | A formatting-aware extraction experiment is queued. The judge prompt tells it to trust the reference answer over such pages. |
| 9 | **Reference answers are part of the evaluation and can be wrong.** | N01 was over-specific, G29 omitted a sending-off condition, and G26, G15 and G20 were incomplete or vague. | Listed in the errata. Not edited, so my grades stay valid. |
| 10 | **Don't edit the golden set after grading.** | I rewrote five references after grading, which made my grades inconsistent with them, then reverted it. | Froze the set with a checksum. The harness enforces it. |
| 11 | **A judge that only sees the reference answer uses its own memory.** | G30: it said the offender is cautioned, but the 2026/27 Laws say there is no sanction. G15: it objected to a sentence that appears verbatim on page 101. | Built judge v2, which reads the relevant pages. |
| 12 | **Judge output can fail silently.** | The first judge version had a 200-token limit. Ten of the 41 replies were cut off and unparseable. | Raised the limit, added a retry, and the raw reply is kept when parsing fails. |
| 13 | **Model output varies between runs.** | G32 cited pages 131 and 150 in one run and only 131 in the next. | B0 generation is a set of graded answers that I do not regenerate. |
| 14 | **Small samples limit what I can say.** | One question is about 2.2 to 2.4 percentage points. Types with 2 to 7 questions are anecdotes. | The write-ups say so. |
| 15 | **Seeing the judge's verdict while grading creates anchoring.** | I saw the first judge's verdict for some answers while grading them. | The agreement figures carry that caveat. |

## The LLM judge

### What it does

The judge is a first-pass grader of answer correctness. Refusals are graded by code, not by the judge: a refusal on an unanswerable question is `CORRECT_REFUSAL`, an answer to one is `ANSWERED_UNANSWERABLE`, and a refusal on an answerable question is `FALSE_REFUSAL`. Every other answer gets one of three verdicts:

- **CORRECT:** states all key facts in the reference answer, and no claim contradicts the reference or the pages. A claim the pages neither support nor contradict is not an error.
- **PARTIAL:** gets some key facts right but omits an important one, or includes a claim the pages or reference contradict while the main point is right.
- **INCORRECT:** contradicts the reference on the main point, or misses it.

### Versions

| Version | Inputs | Problems found |
|---|---|---|
| v1 | Question, reference answer, model answer | Graded against its own outdated knowledge (G30), over-literal on G15, and its 200-token limit left 10 of 41 replies unparseable (fixed to 1,000 tokens with one retry) |
| **v2-grounded (current)** | v1 inputs plus the text of the question's expected pages | See the limitations below |

Current judge: `judge_v2.py`, prompt version `v2-grounded`, prompt hash `95e6ece39e1353f1`, model `claude-sonnet-5-5` (the generator is Claude Haiku 4.5). `run_eval.py` records the prompt version and hash in each run's `config.json`, so a change shows up in the record.

How v2 works:

- It uses only the reference answer and the page text as its source of truth, and is told not to use its own knowledge of the Laws.
- It must reply with only a JSON object (`verdict` and one-sentence `reason`).
- Retried once with a larger token budget if the reply can't be parsed.
- The pages and the model answer are wrapped as data, and the prompt tells it to ignore instructions inside them.
- It is told to trust the reference over page text when the reference says something was removed, because deleted text appears unmarked in the pages.

### Validation (Stage 2, attempt 1 of 3, no revisions used)

I fixed the thresholds before running it:

| Check | Threshold | Result |
|---|---|---|
| Agreement with my verdicts on the 41 graded B0 answers | at least 37 of 41 (90%) | **40 of 41** |
| An answer I graded INCORRECT graded CORRECT | never | none (G32 was graded INCORRECT) |
| Planted errors caught (verdict other than CORRECT) | at least 8 of 10 | **10 of 10** |
| Correct originals failed | at most 1 of 10 | **0 of 10** |

How the planted-error test works: I took 10 answers I had graded CORRECT and changed one fact in each (for example eleven players to nine, a 15-minute half-time interval to 20, a corner kick to a goal kick). The judge graded both versions. The substitutions are in `judge/planted_errors.jsonl` and are fixed.

Agreement against my grades:

| My verdict \ judge | CORRECT | PARTIAL | INCORRECT |
|---|---|---|---|
| CORRECT (36) | 35 | 1 | 0 |
| PARTIAL (4) | 0 | 4 | 0 |
| INCORRECT (1) | 0 | 0 | 1 |

- **The one disagreement is G33.** I graded it CORRECT and the judge graded it PARTIAL: the core answer matches the reference, but a closing note about throw-ins and goal kicks is garbled and cites pages the judge wasn't given.
- **One planted error was graded PARTIAL, not INCORRECT.** G31: the judge saw the main point (a goal can be scored from a goal kick) as right and the own-goal restart as wrong. It still counted as caught.
- **B0 under judge v2:** 35 correct, 5 partial, 1 incorrect across the 41 answers. These are the counts the regression gate will use.

### Rules for changing the judge

The plan allows the first judge plus two revisions, so at most three validation attempts. `validate_judge.py` logs each attempt, with the prompt hash, in `judge/attempts.jsonl` and refuses a fourth. A revision must be made for a general reason. Tuning the prompt to fix individual questions would overfit it to my 41 grades.

### Known limitations

- **The planted errors are clear, single-fact swaps.** Passing doesn't show that the judge notices subtle omissions or garbled extra claims.
- **It only sees the expected pages.** Claims that rely on other pages can be neither verified nor refuted, which is what happened with G33.
- **It can't see strikethrough or underline formatting.** It can't catch an N03-style error from the page text, so it relies on the reference answer there.
- **It inherits reference defects.** The errata list five known ones (N01, G15, G20, G26, G29).
- **One rater.** The validation compares the judge with my grades only, and I could see the first judge's verdict for some of them.
- **Same vendor family.** The judge (Sonnet 5.5) and the generator (Haiku 4.5) are both Claude models, and I haven't tested whether they share biases.
- **Repeatability is untested.** I haven't measured how often it returns a different verdict for the same input.

### How I use it

Treat every `PARTIAL` and `INCORRECT` as "read this", and spot-check a few `CORRECT` answers each time. Don't use the judge for anything the validation didn't cover.
