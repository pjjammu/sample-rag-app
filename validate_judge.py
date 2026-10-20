"""
Stage 2: validate the judge (tasks 2.2 and 2.3 of EVAL_PLAN.md).

  2.2  Judge the 41 graded B0 answers and compare with my own verdicts.
  2.3  Planted-error test: change one fact in 10 correct B0 answers and see whether the judge catches it.

    python validate_judge.py --pdf "C:\\path\\to\\Laws of the Game 2026_27_single pages.pdf"
    python validate_judge.py --pdf "..." --limit 5        # cheap smoke test (not logged, no pass/fail)

Files (inside evals/):
    baseline/B0_generation_graded.csv   my graded B0 answers
    golden_set/golden_set.jsonl         frozen golden set (checksum checked)
    judge/planted_errors.jsonl          the 10 planted errors (created once, then frozen)
    judge/attempts.jsonl                one line per validation attempt (written by this script)

Pass thresholds (fixed in the plan): agreement >= 90% of the graded answers, the judge never grades an answer
I marked INCORRECT as CORRECT, it catches >= 8 of 10 planted errors, and it fails <= 1 of the 10 correct originals.
The judge prompt may be revised at most twice, so at most 3 attempts are allowed.
"""
import argparse, collections, csv, datetime, json, math, sys
from pathlib import Path

import judge_v2
import review_answers as ra
import run_eval as rn

ROOT = Path(__file__).resolve().parent
AGREE_MIN_FRAC = 0.90
CAUGHT_MIN = 8
ORIGINAL_FAILS_MAX = 1
ATTEMPT_LIMIT = 3          # first judge + at most two revisions
LABELS = ("CORRECT", "PARTIAL", "INCORRECT")


# ---------------------------------------------------------------- pure helpers (unit-tested)
def human_rows(graded):
    """Graded B0 rows that carry a human CORRECT / PARTIAL / INCORRECT verdict."""
    return [r for r in graded if r["my_verdict"].strip().upper() in LABELS]


def plant(graded_by_id, planted):
    """Apply each planted error to the B0 answer. Returns [(id, corrupted_text, description)]."""
    out = []
    for p in planted:
        row = graded_by_id[p["id"]]
        if row["my_verdict"].strip().upper() != "CORRECT":
            raise ValueError(f"{p['id']}: planted errors must start from an answer I graded CORRECT")
        if p["find"] not in row["model_answer"]:
            raise ValueError(f"{p['id']}: text to replace not found in the B0 answer: {p['find']!r}")
        out.append((p["id"], row["model_answer"].replace(p["find"], p["replace"], 1), p["error"]))
    return out


def confusion(human, judged):
    """human, judged: dicts id -> verdict. Rows are my verdicts, columns the judge's (other = unparseable / error)."""
    m = {h: collections.Counter() for h in LABELS}
    for i, h in human.items():
        j = judged[i]
        m[h][j if j in LABELS else "OTHER"] += 1
    return m


def summarize(human, judged, planted_orig, planted_corrupt):
    """human: id->my verdict, judged: id->judge verdict, planted_orig / planted_corrupt: id->judge verdict."""
    n = len(human)
    agree = sum(1 for i, h in human.items() if judged[i] == h)
    missed_incorrect = [i for i, h in human.items() if h == "INCORRECT" and judged[i] == "CORRECT"]
    caught = [i for i, v in planted_corrupt.items() if v != "CORRECT"]
    orig_fails = [i for i, v in planted_orig.items() if v != "CORRECT"]
    need = math.ceil(AGREE_MIN_FRAC * n)
    checks = {
        f"agreement >= {need} of {n}": agree >= need,
        "judge never grades my INCORRECT answers as CORRECT": not missed_incorrect,
        f"catches >= {CAUGHT_MIN} of {len(planted_corrupt)} planted errors": len(caught) >= CAUGHT_MIN,
        f"fails <= {ORIGINAL_FAILS_MAX} of {len(planted_orig)} correct originals": len(orig_fails) <= ORIGINAL_FAILS_MAX,
    }
    return {"n": n, "agree": agree, "agree_needed": need, "missed_incorrect": missed_incorrect,
            "disagreements": [i for i, h in human.items() if judged[i] != h],
            "planted_n": len(planted_corrupt), "caught": caught, "missed_planted": [i for i in planted_corrupt if i not in caught],
            "orig_fails": orig_fails, "checks": checks, "passed": all(checks.values())}


def attempts_so_far(path):
    p = Path(path)
    if not p.exists():
        return 0
    return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())


# ---------------------------------------------------------------- running
def judge_set(items, golden_by_id, client, model, page_text_fn):
    """items: [(id, answer_text)] -> {id: (verdict, reason)}"""
    out = {}
    for i, answer in items:
        g = golden_by_id[i]
        pages = {p: page_text_fn(p) for p in g["expected_pages"]}
        try:
            out[i] = judge_v2.judge_one(client, model, g["question"], g["reference_answer"], answer, pages)
        except Exception as e:
            out[i] = ("JUDGE_ERROR", f"{type(e).__name__}: {e}")
    return out


def run(graded, golden, planted, client, model, page_text_fn, limit=0):
    golden_by_id = {g["id"]: g for g in golden}
    graded_by_id = {r["id"]: r for r in graded}
    rows = human_rows(graded)
    if limit:
        rows = rows[:limit]
    human = {r["id"]: r["my_verdict"].strip().upper() for r in rows}

    print(f"Judging {len(rows)} graded answers with {model} (judge prompt {judge_v2.JUDGE_PROMPT_VERSION}) ...")
    first = judge_set([(r["id"], r["model_answer"]) for r in rows], golden_by_id, client, model, page_text_fn)

    planted_use = [p for p in planted if p["id"] in human] if limit else planted
    corrupted = plant(graded_by_id, planted_use)
    print(f"Judging {len(corrupted)} planted-error answers ...")
    second = judge_set([(i, text) for i, text, _ in corrupted], golden_by_id, client, model, page_text_fn)

    # the correct originals reuse the verdicts from the first pass (same answer, same pages, same prompt)
    judged = {i: v for i, (v, _) in first.items()}
    planted_orig = {i: judged[i] for i, _, _ in corrupted if i in judged}
    planted_corrupt = {i: v for i, (v, _) in second.items()}
    res = summarize(human, judged, planted_orig, planted_corrupt)
    res["detail"] = {
        "judged": {i: {"human": human[i], "judge": first[i][0], "reason": first[i][1]} for i in human},
        "planted": {i: {"error": e, "corrupted_answer": t, "judge": second[i][0], "reason": second[i][1],
                        "original_judge": planted_orig.get(i)} for i, t, e in corrupted},
    }
    res["confusion"] = {h: dict(c) for h, c in confusion(human, judged).items()}
    return res


def report_markdown(res, attempt, model, partial):
    L = [f"# Judge validation: attempt {attempt}", "",
         f"Judge prompt `{judge_v2.JUDGE_PROMPT_VERSION}` (sha {judge_v2.JUDGE_PROMPT_SHA}), model `{model}`"
         + ("  **(PARTIAL RUN: not logged, thresholds not applied)**" if partial else ""), "",
         f"## Against my verdicts: {res['agree']} of {res['n']} agree", "",
         "| My verdict \\ judge | CORRECT | PARTIAL | INCORRECT | other |", "|---|---|---|---|---|"]
    for h in LABELS:
        c = res["confusion"][h]
        L.append(f"| {h} | {c.get('CORRECT', 0)} | {c.get('PARTIAL', 0)} | {c.get('INCORRECT', 0)} | {c.get('OTHER', 0)} |")
    L += ["", "Disagreements:", ""]
    for i in res["disagreements"] or []:
        d = res["detail"]["judged"][i]
        L.append(f"- {i}: me {d['human']}, judge {d['judge']}: {d['reason']}")
    if not res["disagreements"]:
        L.append("- none")
    L += ["", f"## Planted errors: caught {len(res['caught'])} of {res['planted_n']}; "
              f"{len(res['orig_fails'])} correct original(s) failed", ""]
    for i, d in res["detail"]["planted"].items():
        L.append(f"- {i} ({d['error']}): judge {d['judge']}" + (" (MISSED)" if i in res["missed_planted"] else "")
                 + (f"; original graded {d['original_judge']}" if d["original_judge"] != "CORRECT" else ""))
    L += ["", "## Checks", ""] + [f"- [{'x' if ok else ' '}] {name}" for name, ok in res["checks"].items()]
    L += ["", f"**{'PASSED' if res['passed'] else 'NOT PASSED'}**"]
    return "\n".join(L) + "\n"


def main(argv=None, client=None, page_text_fn=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--evals-dir", default=str(ROOT / "evals"))
    ap.add_argument("--pdf", help="the Laws of the Game PDF (needed unless tests inject page text)")
    ap.add_argument("--judge-model", default=ra.JUDGE_MODEL_DEFAULT)
    ap.add_argument("--graded", default=None)
    ap.add_argument("--planted", default=None)
    ap.add_argument("--limit", type=int, default=0, help="smoke test on the first N graded answers")
    args = ap.parse_args(argv)

    evals = Path(args.evals_dir)
    golden_path = evals / "golden_set" / "golden_set.jsonl"
    rn.check_golden(golden_path, evals / "golden_set" / "golden_set.sha256")
    graded_path = Path(args.graded) if args.graded else evals / "baseline" / "B0_generation_graded.csv"
    planted_path = Path(args.planted) if args.planted else evals / "judge" / "planted_errors.jsonl"
    attempts_path = evals / "judge" / "attempts.jsonl"
    partial = bool(args.limit)

    done = attempts_so_far(attempts_path)
    if not partial and done >= ATTEMPT_LIMIT:
        sys.exit(f"{done} attempts are already logged. The plan allows the first judge plus two revisions, so stop here "
                 "and decide with the plan's rules before trying again.")
    if page_text_fn is None:
        if not args.pdf:
            sys.exit("--pdf is required (the Laws of the Game PDF).")
        page_text_fn = judge_v2.pdf_page_text_fn(args.pdf)
    if client is None:
        import generate as g
        client = g.get_anthropic()

    with open(graded_path, encoding="utf-8-sig", newline="") as f:
        graded = list(csv.DictReader(f))
    with open(golden_path, encoding="utf-8") as f:
        golden = [json.loads(l) for l in f if l.strip()]
    with open(planted_path, encoding="utf-8") as f:
        planted = [json.loads(l) for l in f if l.strip()]

    res = run(graded, golden, planted, client, args.judge_model, page_text_fn, limit=args.limit)
    attempt = done + 1
    md = report_markdown(res, attempt, args.judge_model, partial)
    print("\n" + md)

    out_dir = evals / "judge"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    tag = "smoke" if partial else f"attempt{attempt}"
    (out_dir / f"{tag}_{stamp}.md").write_text(md, encoding="utf-8")
    (out_dir / f"{tag}_{stamp}.json").write_text(json.dumps(res, indent=2, ensure_ascii=False, default=list), encoding="utf-8")
    if not partial:
        with open(attempts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"attempt": attempt, "when": stamp, "judge_prompt_version": judge_v2.JUDGE_PROMPT_VERSION,
                                "judge_prompt_sha256": judge_v2.JUDGE_PROMPT_SHA, "judge_model": args.judge_model,
                                "agreement": f"{res['agree']}/{res['n']}", "missed_incorrect": res["missed_incorrect"],
                                "planted_caught": f"{len(res['caught'])}/{res['planted_n']}",
                                "original_fails": len(res["orig_fails"]), "passed": res["passed"]}) + "\n")
        print(f"Attempt {attempt} logged in {attempts_path}.")


if __name__ == "__main__":
    main()
