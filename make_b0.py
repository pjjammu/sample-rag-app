"""
Stage 0.4: build baseline/B0.md from the retrieval-only run and the graded answers.

    python make_b0.py baseline/B0_retrieval.json baseline/B0_generation_graded.csv golden_set.jsonl --out baseline/B0.md

Inputs
  B0_retrieval.json            output of: python evaluate.py golden_set.jsonl --namespace laws-1200-150
  B0_generation_graded.csv     your graded review file (answer_review_fixed.csv), copied unchanged
  golden_set.jsonl             the frozen golden set (used for expected pages and the checksum)
Put this file next to evaluate.py (it reuses its metric functions).
"""
import argparse, collections, csv, hashlib, json, os, sys
import evaluate as ev

JUDGED = ("CORRECT", "PARTIAL", "INCORRECT")


def pct(x):
    return "n/a" if x != x else f"{100 * x:.1f}%"


def ints(s):
    return [int(x) for x in str(s).replace(";", ",").split(",") if x.strip().isdigit()]


def retrieval_section(data):
    rows = data["rows"]
    ans = [r for r in rows if r["answerable"]]
    ks = ev.K_LIST
    out = [f"## Retrieval (B0)", "",
           f"Run: {data.get('when', '?')} | namespace `{data.get('namespace')}` | embeddings `{data.get('embed_model')}` | "
           f"retrieval depth {ev.DEPTH} | {len(ans)} answerable questions", ""]
    head = "| | " + " | ".join(f"recall@{k}" for k in ks) + " | MRR |"
    sep = "|---|" + "---|" * (len(ks) + 1)

    def line(label, subset, key):
        ranks = [r[key] for r in subset]
        return f"| {label} | " + " | ".join(pct(ev.recall_at(ranks, k)) for k in ks) + f" | {ev.mrr(ranks):.3f} |"

    out += [head, sep, line("Phrase match (strict)", ans, "rank"), line("Page match (loose)", ans, "page_rank"), "",
            "By question type (strict phrase match):", "", head.replace("| | ", "| Type (n) | ", 1), sep]
    for t in sorted({r["type"] for r in ans}):
        sub = [r for r in ans if r["type"] == t]
        out.append(line(f"{t} ({len(sub)})", sub, "rank"))
    miss = [r for r in ans if r["rank"] is None]
    out += ["", f"Never retrieved in the top {ev.DEPTH}: " + (", ".join(r["id"] for r in miss) if miss else "none")]
    ta = [r["top_score"] for r in ans if r.get("top_score") is not None]
    tu = [r["top_score"] for r in rows if not r["answerable"] and r.get("top_score") is not None]
    if ta and tu:
        out += ["", f"Mean top-1 similarity: answerable {sum(ta)/len(ta):.3f}, unanswerable {sum(tu)/len(tu):.3f}"]
    return out


def generation_section(graded, golden):
    exp = {g["id"]: set(g["expected_pages"]) for g in golden}
    verdict = lambda r: (r["my_verdict"].strip().upper() or r["auto_or_judge_verdict"])
    answerable = [r for r in graded if r["type"] != "unanswerable"]
    counts = collections.Counter(verdict(r) for r in graded)
    by_type = collections.defaultdict(collections.Counter)
    for r in graded:
        by_type[r["type"]][verdict(r)] += 1

    judged = [r for r in graded if r["auto_or_judge_verdict"] in JUDGED and r["my_verdict"].strip()]
    dis = [r for r in judged if r["my_verdict"].strip().upper() != r["auto_or_judge_verdict"]]
    answered = [r for r in answerable if r["auto_or_judge_verdict"] != "FALSE_REFUSAL"]
    cited = [r for r in answered if ints(r["cited_pages"])]
    right_page = [r for r in answered if set(ints(r["cited_pages"])) & exp.get(r["id"], set())]
    refusals = [r for r in graded if r["type"] == "unanswerable"]
    ok_ref = [r for r in refusals if r["auto_or_judge_verdict"] == "CORRECT_REFUSAL"]

    out = ["## Generation (B0)", "",
           "Generator Claude Haiku 4.5, judge Claude Sonnet 5.5, k = 5, namespace `laws-1200-150`. These are the graded outputs of the "
           "generation run in `B0_generation_graded.csv`; they were not regenerated.", "",
           "My verdicts (the judge's automatic verdict is used only for refusals):", "",
           "| Verdict | Count |", "|---|---|"]
    for v in ("CORRECT", "PARTIAL", "INCORRECT", "CORRECT_REFUSAL", "FALSE_REFUSAL", "ANSWERED_UNANSWERABLE"):
        if counts.get(v):
            out.append(f"| {v} | {counts[v]} |")
    out += ["", "By question type:", "", "| Type | n | Correct | Partial | Incorrect | Correct refusal |", "|---|---|---|---|---|---|"]
    for t in sorted(by_type):
        c = by_type[t]
        out.append(f"| {t} | {sum(c.values())} | {c['CORRECT']} | {c['PARTIAL']} | {c['INCORRECT']} | {c['CORRECT_REFUSAL']} |")
    out += ["", "Behaviour metrics:", "",
            f"- Answerable questions answered (not refused): {len(answered)} of {len(answerable)}",
            f"- False refusals: {len(answerable) - len(answered)} of {len(answerable)}",
            f"- Answers with at least one citation: {len(cited)} of {len(answered)}",
            f"- Answers citing an expected page (frozen golden set v2): {len(right_page)} of {len(answered)}",
            f"- Correct refusals on unanswerable questions: {len(ok_ref)} of {len(refusals)}",
            f"- Judge versus my verdict: agree on {len(judged) - len(dis)} of {len(judged)} judged answers"
            + (" (" + ", ".join(f"{r['id']}: judge {r['auto_or_judge_verdict']}, me {r['my_verdict'].strip().upper()}" for r in dis) + ")" if dis else ""),
            "",
            "Recorded from the console output of the generation run (cannot be recomputed from the CSV):", "",
            "- Citations of a page that wasn't retrieved: 0 of 41",
            "- Tokens: 56,265 in / 4,800 out; estimated cost about $0.08; mean latency 1.4 s", "",
            "Caveats: the reference answers for N01, G15, G20, G26 and G29 have known defects (see `GOLDEN_SET_ERRATA.md`). "
            "I could see the judge's verdict while grading some answers, so agreement may be inflated by anchoring."]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("retrieval_json")
    ap.add_argument("graded_csv")
    ap.add_argument("golden")
    ap.add_argument("--out", default="baseline/B0.md")
    a = ap.parse_args(argv)

    with open(a.retrieval_json, encoding="utf-8") as f:
        data = json.load(f)
    with open(a.graded_csv, encoding="utf-8-sig", newline="") as f:
        graded = list(csv.DictReader(f))
    with open(a.golden, encoding="utf-8") as f:
        golden = [json.loads(l) for l in f if l.strip()]

    digest = hashlib.sha256(open(a.golden, "rb").read()).hexdigest()
    sha_file = os.path.join(os.path.dirname(a.golden) or ".", "golden_set.sha256")
    if os.path.exists(sha_file):
        expected = open(sha_file).read().split()[0]
        if expected != digest:
            sys.exit("golden_set.jsonl does not match golden_set.sha256: the golden set has changed. Stop and restore it.")
    # the retrieval run must have been scored against exactly this golden set
    gold = {g["id"]: g for g in golden}
    stale = [r["id"] for r in data["rows"]
             if r["id"] not in gold or any(r.get(k) != gold[r["id"]].get(k) for k in ("key_phrase", "alt_phrases", "expected_pages"))]
    if stale:
        sys.exit(f"{a.retrieval_json} was not produced with this golden_set.jsonl (mismatch on {stale}). "
                 "Rerun evaluate.py on the frozen file before building B0.")
    ids_r, ids_g = {r["id"] for r in data["rows"]}, {r["id"] for r in graded}
    if ids_r != ids_g:
        sys.exit(f"Question ids differ between the retrieval run and the graded CSV: {sorted(ids_r ^ ids_g)}")

    lines = ["# Baseline B0", "",
             f"Golden set v2, SHA-256 `{digest}`. Frozen: see `GOLDEN_SET_CHANGELOG.md` and `GOLDEN_SET_ERRATA.md`.", ""]
    lines += retrieval_section(data) + [""] + generation_section(graded, golden) + [""]
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"Wrote {a.out}")


if __name__ == "__main__":
    main()
