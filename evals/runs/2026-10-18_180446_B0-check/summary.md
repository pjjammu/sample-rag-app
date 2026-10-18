# Run: B0-check

Started 2026-10-18T18:04:46

| Setting | Value |
|---|---|
| namespace | laws-1200-150 |
| chunking | RecursiveCharacterTextSplitter, 1200 characters, 150 overlap, split per page |
| k | 5 |
| embed_model | voyage-4-lite |
| gen_model | claude-haiku-4-5-20251001 |
| judge_model | claude-sonnet-5-5 |
| n_questions | 46 |
| golden set sha256 | `623f69a0baa72f1e...` |
| git commit | 6bc5700 (uncommitted changes) |

## Retrieval

| recall@1 | recall@3 | recall@5 | recall@10 | MRR | page recall@5 | page MRR |
|---|---|---|---|---|---|---|
| 68.3% | 87.8% | 97.6% | 100.0% | 0.785 | 100.0% | 0.829 |

Never retrieved in the top 10: none

Versus B0 retrieval: identical

## Generation

- False refusals: 0
- Answers with citations of an unretrieved page: 0
- Answers citing an expected page: 40
- Correct refusals on unanswerable questions: 5
- Errors: 0
- Tokens: 56265 in / 4944 out
- Estimated cost: $0.081
- Mean latency: 1.4 s

## Judge verdicts

| Verdict | Count |
|---|---|
| CORRECT | 33 |
| PARTIAL | 5 |
| CORRECT_REFUSAL | 5 |
| INCORRECT | 3 |

Read these first:

- G10 [PARTIAL] What is the maximum number of substitutes in an official competition? (The model correctly gives five as the maximum, but it omits that FIFA, the confederation or the national association determines this. It also adds a doubtful claim that other official competitions may use more than five, which conflicts with a five-substitute maximum.)
- G15 [PARTIAL] When is the ball out of play? (It covers both key facts (wholly passed over the line; stopped by the referee), but the added claim that the ball is out of play when it touches an official and stays on the field is wrong, and it wrongly says all cases restart with a dropped ball.)
- G22 [PARTIAL] Do arms count when deciding if someone is offside? (The answer correctly says arms and hands are not considered, but it omits the reference's key detail that the arm's upper boundary is in line with the bottom of the armpit.)
- G29 [INCORRECT] When must a player be sent off for a handball offence? (The model answer adds a claim that contradicts the Laws and the reference: that a non-deliberate handball denying a goal or opportunity is a sending-off offence outside the penalty area, and that a non-deliberate handball in the penalty area leads to a caution, whereas the reference limits sending-off to deliberate handball.)
- G32 [INCORRECT] What is awarded if a corner kick goes directly into the kicker's own goal? (The reference says a corner kick is awarded to the opponents, but the model answer says a goal kick, which contradicts it.)
- N01 [PARTIAL] What is the time limit for a player to leave the field when being substituted? (The model answer correctly states ten seconds but omits the key consequence that the substitute cannot enter until the first stoppage after one minute has elapsed if the limit is exceeded.)
- N02 [PARTIAL] How long must a player who received treatment on the field stay off after play restarts? (The one-minute figure matches the reference, but the added claim that the referee may let the player return while the ball is in play is doubtful, and the answer does not clearly state the actual exceptions.)
- N03 [INCORRECT] Is the penalty taker still automatically cautioned if the goalkeeper and kicker offend at the same time? (The model opens with 'No' and says the requirement was removed, but then states the kicker is still cautioned in regular play and in shoot-outs, which contradicts the reference that the automatic caution was removed.)
