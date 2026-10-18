# Evaluation run 1: baseline retrieval

This is the first measurement of my RAG pipeline over the IFAB *Laws of the Game 2026/27*. I ran it before changing anything, so it is my baseline for every experiment that follows. When I inspected the two questions that were never retrieved, I found that my evaluation was wrong, not the retrieval. That finding is below.

> **Update after run 2:** while reviewing the generation results I found that one golden-set key phrase (G32) also appears in two other Laws, so it credited the wrong chunk and made recall@5 look one question better than it was. I tightened that phrase and one other (G04). The numbers below are as first measured. See [EVAL_RUN_2_GENERATION.md](EVAL_RUN_2_GENERATION.md).

## What I measured

I wanted to know one thing first: **when someone asks a question, does the right passage reach the model?** Generation quality comes later. If retrieval is poor, nothing downstream can fix it, so I evaluated retrieval on its own.

## Setup

| Component | Choice |
|---|---|
| Source document | IFAB Laws of the Game 2026/27 (260-page PDF) |
| Chunking | `RecursiveCharacterTextSplitter`, 1,200 characters, 150 overlap, split per page (297 chunks) |
| Embeddings | Voyage AI `voyage-4-lite` (documents and queries embedded with their respective input types) |
| Vector store | Pinecone (free Starter plan), cosine similarity, namespace `laws-1200-150` |
| Retrieval depth | Top 10 chunks per question |

The PDF and the derived chunks are not in this repository because the booklet is IFAB's copyrighted material. Bring your own copy.

## The golden set

I built 46 questions: 41 that the Laws can answer and 5 that they can't (for example, who won the 2018 World Cup, or how much referees are paid). The answerable ones are split by type:

| Type | Count | Purpose |
|---|---|---|
| Lookup | 22 | Direct facts: durations, distances, counts |
| Exception | 7 | Rules with conditions, like offside and handball |
| Paraphrase | 5 | Casual wording that avoids the rulebook's phrasing |
| New in 2026/27 | 5 | Questions about this year's rule changes |
| Multi-rule | 2 | Answers that combine more than one rule |

Each answerable question has a **key phrase** copied from the passage that answers it. I drafted the questions with Claude's help, and every key phrase was checked programmatically against the PDF text and against my chunk output.

**How a hit is scored:** a retrieved chunk counts if it contains the key phrase (strict). I also report a looser page-based version, where the chunk only has to come from an expected page.

## Results (as first measured)

41 answerable questions, one key phrase per question:

| | recall@1 | recall@3 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|---|
| Phrase match (strict) | 61.0% | 80.5% | 92.7% | 95.1% | 0.717 |
| Page match (loose) | 65.9% | 85.4% | 92.7% | 95.1% | 0.759 |

By question type (strict phrase match):

| Type | n | recall@1 | recall@3 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|---|---|
| Exception | 7 | 71.4% | 71.4% | 100% | 100% | 0.779 |
| Lookup | 22 | 63.6% | 86.4% | 100% | 100% | 0.757 |
| Multi-rule | 2 | 100% | 100% | 100% | 100% | 1.000 |
| Paraphrase | 5 | 80.0% | 100% | 100% | 100% | 0.900 |
| New in 2026/27 | 5 | 0.0% | 40.0% | 40.0% | 60.0% | 0.158 |

Mean top-1 similarity score: **0.617** for answerable questions, **0.430** for unanswerable ones.

Two questions (N01 and N02, both about the 2026/27 changes) were never retrieved in the top 10. That looked like my weakest result, so I read the retrieved chunks.

## What I found when I inspected the two misses

**Both were false misses. The answer was in the top 10, worded differently from my key phrase.**

- **N01** (the time limit for a substituted player to leave the field): my key phrase came from the one-line summary on page 167. The top result was a chunk from page 171 stating the rule directly (leave the field "within ten seconds"), and the next ones came from page 172, the notes protocol on page 22, and Law 3's own text on pages 61 and 63.
- **N02** (how long a treated player stays off the field): again my phrase came from page 167. The top result was the explanation on page 175 ("must leave and remain off the field of play for one minute (running clock) after play has restarted"), followed by the protocol on page 24 and Law 5 on page 75.

The same rule is stated in up to five places in this booklet: the summary of changes, a longer explanation, the Law's own text, and the notes. A single key phrase only credits retrieval for finding one of them. My evaluation was too strict, and the golden set now accepts alternative phrasings (`alt_phrases`) and the extra expected pages.

Re-scoring only these two questions from the stored results (both now hit at rank 1), the counts become recall@1 27/41 (65.9%), @3 35/41 (85.4%), @5 40/41 (97.6%), @10 41/41 (100%), and the 2026/27 type goes from 3/5 to 5/5 within the top 10. **I still need to rerun the full evaluation with the corrected golden set to get clean numbers, including MRR.**

Two things I noticed in the chunk text itself:

1. **A fragment ranked first.** The page 171 chunk is a single sentence fragment that starts mid-sentence ("being substituted must leave…"). It lost its context because I split each page separately, so sentences that cross a page boundary are cut in two. It still answered the question, but this is a chunking weakness I want to test.
2. **One chunk covers many topics.** The page 167 summary chunk bundles changes for several Laws together, so its embedding is a blend of topics. Detailed explanations of one topic tend to score higher against a specific question.

## What I take from this

- **Retrieval works well overall.** For at least 38 of 41 questions (92.7%), the right passage is in the top 5, and top 5 is how many chunks I send to the model.
- **k=5 looks right.** Dropping to 3 chunks would cost about 12 points of recall (80.5% vs. 92.7%), while the two extra chunks add very few tokens.
- **Ranking is the weaker part.** The right chunk is first only about 61–66% of the time, even though it's in the top 5 almost always. A reranker could address this.
- **Paraphrases did fine.** Rewording the question did not hurt retrieval, so the embedding model seems to handle vocabulary mismatch for this document.
- **My metric is a lower bound.** I found two questions whose retrieval was scored wrongly. Others ranked below 1 may be understated too.

### On unanswerable questions

The similarity gap (0.617 vs. 0.430) suggests a minimum-score cutoff could help the system decline before calling the model. With only five unanswerable questions I won't pick a threshold yet. I'll check whether the two score ranges overlap before trusting it.

## Limitations

- The golden set is small, so each question moves a percentage by about 2.4 points. Types with 2 or 5 questions are anecdotes, not trends.
- Strict phrase matching under-counts when a fact is worded in several places. I audited the two never-retrieved questions but not the others.
- A phrase hit shows a chunk contains a key passage, not that it contains the full answer.
- This run measures retrieval only. It says nothing about whether generated answers are correct.

## Next steps

1. Rerun the evaluation with the corrected golden set and record the new baseline.
2. Audit the other questions where the right chunk isn't first, and add alternative phrasings where the answer is legitimately elsewhere.
3. Run the generation evaluation: citations, refusals on unanswerable questions, tokens, and cost.
4. Compare chunk sizes and overlaps, each in its own Pinecone namespace.
5. Test chunking that crosses page boundaries and a section-heading prefix on each chunk.
6. Try hybrid keyword search and Voyage's reranker.
7. Compare against a full-context baseline, with the whole rulebook in the prompt.

## Reproduce

```
python evaluate.py golden_set.jsonl --namespace laws-1200-150
```

This writes `eval_laws-1200-150_k5.json` with per-question results, including every retrieved chunk and its score.
