"""
Stage 1: one-command evaluation harness for the Laws of the Game RAG app.

Layout (all paths relative to the folder that contains this file; override with --evals-dir):
    evals/golden_set/golden_set.jsonl      frozen golden set
    evals/golden_set/golden_set.sha256     its checksum (the run stops if they differ)
    evals/baseline/B0_retrieval.json       baseline retrieval run (used by --vs-b0)
    evals/runs/<date>_<label>/             one folder per run
    evals/runs/results.csv                 one row per full run

Usage:
    python run_eval.py --label E1-k10 -k 10                       # retrieval metrics only (free)
    python run_eval.py --label B0-check --generate --judge --pdf "C:\\path\\to\\Laws of the Game 2026_27_single pages.pdf" --vs-b0
    python run_eval.py --label smoke --limit 5 --generate          # partial run: saved, but NOT added to results.csv

Needs VOYAGE_API_KEY and PINECONE_API_KEY (plus ANTHROPIC_API_KEY with --generate / --judge).
Keep this file next to evaluate.py, generate.py, review_answers.py and judge_v2.py (it reuses them).
--judge uses judge v2 (validated in Stage 2), which reads the relevant Laws pages, so it needs --pdf.
"""
import argparse, collections, csv, datetime, hashlib, json, re, subprocess, sys
from pathlib import Path

import evaluate as ev
import generate as g
import judge_v2
import review_answers as ra

ROOT = Path(__file__).resolve().parent
FIELDS = ["label", "date", "run_dir", "golden_sha", "namespace", "chunking", "k", "gen_model", "judge_model",
          "git_commit", "n_answerable", "recall@1", "recall@3", "recall@5", "recall@10", "mrr", "page_recall@5",
          "page_mrr", "false_refusals", "unsupported_citations", "cites_expected_page", "correct_refusals",
          "judge_correct", "judge_partial", "judge_incorrect", "judge_other", "tokens_in", "tokens_out",
          "cost_usd", "mean_latency_s"]
DEFAULT_CHUNKING = "RecursiveCharacterTextSplitter, 1200 characters, 150 overlap, split per page"


# ---------------------------------------------------------------- small helpers
def slug(label):
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-")
    if not s:
        sys.exit("--label must contain at least one letter or digit.")
    return s


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_golden(golden_path, sha_path):
    """Return the golden set's SHA-256, or stop the run if it doesn't match the frozen checksum."""
    if not Path(golden_path).exists():
        sys.exit(f"Golden set not found: {golden_path}")
    if not Path(sha_path).exists():
        sys.exit(f"Checksum file not found: {sha_path}. The golden set must be frozen before running evaluations.")
    digest = sha256_file(golden_path)
    expected = Path(sha_path).read_text().split()[0]
    if digest != expected:
        sys.exit("STOP: golden_set.jsonl does not match golden_set.sha256. The golden set has changed since it was frozen. "
                 "Restore the frozen file (the golden set is never edited; see GOLDEN_SET_ERRATA.md).")
    return digest


def git_info(root):
    try:
        run = lambda *a: subprocess.run(a, cwd=root, capture_output=True, text=True, timeout=5)
        head = run("git", "rev-parse", "--short", "HEAD")
        if head.returncode != 0:
            return {"commit": None, "dirty": None}
        return {"commit": head.stdout.strip(), "dirty": run("git", "status", "--porcelain").stdout.strip() != ""}
    except Exception:
        return {"commit": None, "dirty": None}


def rnd(x, n=4):
    return "" if x is None or x != x else round(x, n)


# ---------------------------------------------------------------- retrieval
def build_rows(golden, retrieved):
    rows = []
    for q in golden:
        chunks = retrieved[q["question"]]
        phrases = [q["key_phrase"]] + q.get("alt_phrases", [])
        rows.append({
            "id": q["id"], "type": q["type"], "answerable": q["answerable"], "question": q["question"],
            "expected_pages": q["expected_pages"], "key_phrase": q["key_phrase"], "alt_phrases": q.get("alt_phrases", []),
            "rank": ev.hit_rank(chunks[:ev.DEPTH], phrases) if q["answerable"] else None,
            "page_rank": ev.page_rank(chunks[:ev.DEPTH], set(q["expected_pages"])) if q["answerable"] else None,
            "top_score": chunks[0]["score"] if chunks else None,
            "retrieved": [{"id": c["id"], "page": c["page"], "score": c["score"]} for c in chunks[:ev.DEPTH]],
        })
    return rows


def retrieval_metrics(rows):
    ans = [r for r in rows if r["answerable"]]
    ranks, pranks = [r["rank"] for r in ans], [r["page_rank"] for r in ans]
    m = {"n_answerable": len(ans), "mrr": ev.mrr(ranks), "page_recall@5": ev.recall_at(pranks, 5), "page_mrr": ev.mrr(pranks)}
    for k in ev.K_LIST:
        m[f"recall@{k}"] = ev.recall_at(ranks, k)
    return m


def compare_to_b0(rows, b0_path):
    """Per-question differences in (rank, page_rank) against the baseline retrieval run."""
    with open(b0_path, encoding="utf-8") as f:
        base = {r["id"]: (r["rank"], r["page_rank"]) for r in json.load(f)["rows"]}
    return [(r["id"], base.get(r["id"]), (r["rank"], r["page_rank"])) for r in rows
            if base.get(r["id"]) != (r["rank"], r["page_rank"])]


# ---------------------------------------------------------------- generation and judge
def run_generation(rows, retrieved, client, model, k, max_tokens):
    for row in rows:
        try:
            res = g.answer(row["question"], retrieve_fn=lambda qq, kk: retrieved[qq][:kk], client=client,
                           model=model, k=k, max_tokens=max_tokens)
            row["gen"] = {"answer": res["answer"], "refused": ev.is_refusal(res["answer"]),
                          "cited_pages": sorted(res["cited_pages"]),
                          "unsupported_citations": sorted(res["unsupported_citations"]),
                          "input_tokens": res["input_tokens"], "output_tokens": res["output_tokens"],
                          "latency_s": res["latency_s"], "truncated": res["truncated"]}
        except Exception as e:                      # one failure shouldn't kill the whole run
            row["gen"] = {"error": f"{type(e).__name__}: {e}"}


def generation_metrics(rows, model):
    done = [r for r in rows if "gen" in r and "error" not in r["gen"]]
    errs = [r for r in rows if "gen" in r and "error" in r["gen"]]
    ans = [r for r in done if r["answerable"]]
    un = [r for r in done if not r["answerable"]]
    tin, tout = sum(r["gen"]["input_tokens"] for r in done), sum(r["gen"]["output_tokens"] for r in done)
    lat = [r["gen"]["latency_s"] for r in done if r["gen"]["latency_s"]]
    return {
        "false_refusals": sum(1 for r in ans if r["gen"]["refused"]),
        "unsupported_citations": sum(1 for r in ans if r["gen"]["unsupported_citations"]),
        "cites_expected_page": sum(1 for r in ans if not r["gen"]["refused"]
                                   and set(r["gen"]["cited_pages"]) & set(r["expected_pages"])),
        "correct_refusals": sum(1 for r in un if r["gen"]["refused"]),
        "errors": len(errs), "tokens_in": tin, "tokens_out": tout,
        "cost_usd": ev.estimate_cost(model, tin, tout),
        "mean_latency_s": sum(lat) / len(lat) if lat else None,
    }


def judge_rows(rows, golden_by_id, client, judge_model, page_text_fn):
    """Automatic verdicts for refusals; judge v2 grades everything else. Returns verdict counts."""
    todo = []
    for r in rows:
        gen = r.get("gen")
        if not gen or "error" in gen:
            continue
        r["verdict"], r["verdict_reason"] = ra.auto_verdict(r["answerable"], gen["refused"]), ""
        if r["verdict"] is None:
            todo.append(r)
    print(f"\nJudging {len(todo)} answers with {judge_model} ...")
    for r in todo:
        try:
            pages = {p: page_text_fn(p) for p in r["expected_pages"]}
            r["verdict"], r["verdict_reason"] = judge_v2.judge_one(
                client, judge_model, r["question"], golden_by_id[r["id"]].get("reference_answer", ""), r["gen"]["answer"], pages)
        except Exception as e:
            r["verdict"], r["verdict_reason"] = "JUDGE_ERROR", f"{type(e).__name__}: {e}"
    return collections.Counter(r["verdict"] for r in rows if r.get("verdict"))


def review_rows(rows, golden_by_id):
    out = []
    for r in rows:
        gen = r.get("gen")
        if not gen or "error" in gen:
            continue
        out.append({"id": r["id"], "type": r["type"], "question": r["question"],
                    "reference_answer": golden_by_id[r["id"]].get("reference_answer", ""),
                    "model_answer": gen["answer"], "cited_pages": gen["cited_pages"],
                    "expected_pages": r["expected_pages"], "verdict": r.get("verdict") or "",
                    "reason": r.get("verdict_reason", ""), "my_verdict": ""})
    return out


# ---------------------------------------------------------------- outputs
def results_row(cfg, run_dir, rm, gm, jc):
    row = {"label": cfg["label"], "date": cfg["started_at"], "run_dir": run_dir.name, "golden_sha": cfg["golden_sha256"][:12],
           "namespace": cfg["namespace"], "chunking": cfg["chunking"], "k": cfg["k"], "gen_model": cfg["gen_model"] or "",
           "judge_model": cfg["judge_model"] or "", "git_commit": cfg["git"]["commit"] or "", "n_answerable": rm["n_answerable"]}
    for key in ("recall@1", "recall@3", "recall@5", "recall@10", "mrr", "page_recall@5", "page_mrr"):
        row[key] = rnd(rm[key])
    if gm:
        for key in ("false_refusals", "unsupported_citations", "cites_expected_page", "correct_refusals", "tokens_in", "tokens_out"):
            row[key] = gm[key]
        row["cost_usd"], row["mean_latency_s"] = rnd(gm["cost_usd"], 3), rnd(gm["mean_latency_s"], 2)
    if jc is not None:
        row.update(judge_correct=jc.get("CORRECT", 0), judge_partial=jc.get("PARTIAL", 0),
                   judge_incorrect=jc.get("INCORRECT", 0), judge_other=jc.get("UNPARSEABLE", 0) + jc.get("JUDGE_ERROR", 0))
    return row


def append_results(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    if exists:
        with open(path, encoding="utf-8", newline="") as f:
            header = next(csv.reader(f))
        if header != FIELDS:
            sys.exit(f"{path} has a different set of columns than this version of run_eval.py writes. "
                     "Move it aside (keep it!) and start a new results file.")
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def write_summary(path, cfg, rm, rows, gm, jc, b0_diffs):
    pct = lambda x: "n/a" if x != x else f"{100 * x:.1f}%"
    L = [f"# Run: {cfg['label']}", "", f"Started {cfg['started_at']}"
         + ("  **(PARTIAL RUN: not added to results.csv)**" if cfg["partial"] else ""), "",
         "| Setting | Value |", "|---|---|"]
    for k in ("namespace", "chunking", "k", "embed_model", "gen_model", "judge_model", "judge_prompt_version", "n_questions"):
        L.append(f"| {k} | {cfg[k]} |")
    L += [f"| golden set sha256 | `{cfg['golden_sha256'][:16]}...` |",
          f"| git commit | {cfg['git']['commit']}{' (uncommitted changes)' if cfg['git']['dirty'] else ''} |", "",
          "## Retrieval", "", "| recall@1 | recall@3 | recall@5 | recall@10 | MRR | page recall@5 | page MRR |", "|---|---|---|---|---|---|---|",
          "| " + " | ".join(pct(rm[k]) for k in ("recall@1", "recall@3", "recall@5", "recall@10")) + f" | {rm['mrr']:.3f} | "
          + pct(rm["page_recall@5"]) + f" | {rm['page_mrr']:.3f} |", ""]
    miss = [r["id"] for r in rows if r["answerable"] and r["rank"] is None]
    L.append(f"Never retrieved in the top {ev.DEPTH}: " + (", ".join(miss) if miss else "none"))
    if b0_diffs is not None:
        L += ["", "Versus B0 retrieval: " + ("identical" if not b0_diffs else
              f"{len(b0_diffs)} question(s) differ: " + ", ".join(d[0] for d in b0_diffs))]
    if gm:
        L += ["", "## Generation", "",
              f"- False refusals: {gm['false_refusals']}", f"- Answers with citations of an unretrieved page: {gm['unsupported_citations']}",
              f"- Answers citing an expected page: {gm['cites_expected_page']}",
              f"- Correct refusals on unanswerable questions: {gm['correct_refusals']}",
              f"- Errors: {gm['errors']}", f"- Tokens: {gm['tokens_in']} in / {gm['tokens_out']} out",
              f"- Estimated cost: {'n/a' if gm['cost_usd'] is None else '$%.3f' % gm['cost_usd']}",
              f"- Mean latency: {'n/a' if gm['mean_latency_s'] is None else '%.1f s' % gm['mean_latency_s']}"]
    if jc is not None:
        L += ["", "## Judge verdicts", "", "| Verdict | Count |", "|---|---|"] + [f"| {v} | {n} |" for v, n in jc.most_common()]
        bad = [r for r in rows if r.get("verdict") not in (None, "CORRECT", "CORRECT_REFUSAL")]
        if bad:
            L += ["", "Read these first:", ""] + [f"- {r['id']} [{r['verdict']}] {r['question']} ({r.get('verdict_reason', '')})" for r in bad]
    Path(path).write_text("\n".join(L) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- main
def main(argv=None, page_text_fn=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Run the RAG evaluation.")
    ap.add_argument("--label", required=True, help="short name for this run, e.g. B0-check or E1-k10")
    ap.add_argument("--namespace", default="laws-1200-150")
    ap.add_argument("--chunking", default=DEFAULT_CHUNKING, help="free-text description recorded in config.json")
    ap.add_argument("-k", type=int, default=5, help="chunks sent to Claude")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--judge", action="store_true", help="grade answers with the judge (needs --generate)")
    ap.add_argument("--vs-b0", action="store_true", help="compare retrieval with evals/baseline/B0_retrieval.json")
    ap.add_argument("--model", default=g.DEFAULT_MODEL)
    ap.add_argument("--judge-model", default=ra.JUDGE_MODEL_DEFAULT)
    ap.add_argument("--pdf", help="the Laws of the Game PDF (needed with --judge: the judge reads the relevant pages)")
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions (partial run)")
    ap.add_argument("--evals-dir", default=str(ROOT / "evals"))
    args = ap.parse_args(argv)
    if args.judge and not args.generate:
        ap.error("--judge needs --generate")
    if args.judge and page_text_fn is None:
        if not args.pdf:
            ap.error("--judge needs --pdf (the judge reads the relevant Laws pages)")
        page_text_fn = judge_v2.pdf_page_text_fn(args.pdf)

    evals = Path(args.evals_dir)
    golden_path, sha_path = evals / "golden_set" / "golden_set.jsonl", evals / "golden_set" / "golden_set.sha256"
    digest = check_golden(golden_path, sha_path)
    with open(golden_path, encoding="utf-8") as f:
        golden = [json.loads(l) for l in f if l.strip()]
    if args.limit:
        golden = golden[:args.limit]
    partial = bool(args.limit)

    started = datetime.datetime.now()
    run_dir = evals / "runs" / f"{started:%Y-%m-%d_%H%M%S}_{slug(args.label)}"
    if run_dir.exists():
        sys.exit(f"{run_dir} already exists.")
    cfg = {"label": args.label, "started_at": started.isoformat(timespec="seconds"), "namespace": args.namespace,
           "chunking": args.chunking, "k": args.k, "retrieval_depth": max(ev.DEPTH, args.k), "embed_model": g.EMBED_MODEL,
           "index": g.INDEX_NAME, "gen_model": args.model if args.generate else None,
           "judge_model": args.judge_model if args.judge else None,
           "judge_prompt_version": judge_v2.JUDGE_PROMPT_VERSION if args.judge else None,
           "judge_prompt_sha256": judge_v2.JUDGE_PROMPT_SHA if args.judge else None, "max_tokens": args.max_tokens,
           "system_prompt_sha256": hashlib.sha256(g.SYSTEM_PROMPT.encode()).hexdigest()[:16],
           "golden_path": str(golden_path), "golden_sha256": digest, "n_questions": len(golden), "partial": partial,
           "git": git_info(ROOT), "generate": args.generate, "judge": args.judge}
    print(f"Run '{args.label}': {len(golden)} questions | namespace '{args.namespace}' | golden set {digest[:12]} OK")

    # ---- retrieval
    retrieved = ev.retrieve_all([q["question"] for q in golden], args.namespace, depth=cfg["retrieval_depth"])
    rows = build_rows(golden, retrieved)
    ev.print_retrieval(rows)
    rm = retrieval_metrics(rows)

    b0_diffs = None
    if args.vs_b0:
        b0_path = evals / "baseline" / "B0_retrieval.json"
        if not b0_path.exists():
            sys.exit(f"{b0_path} not found.")
        b0_diffs = compare_to_b0(rows, b0_path)
        print("\nVersus B0 retrieval: " + ("IDENTICAL" if not b0_diffs else f"{len(b0_diffs)} question(s) DIFFER"))
        for qid, was, now in b0_diffs:
            print(f"  {qid}: B0 (rank, page_rank) = {was}, now {now}")

    # ---- generation and judge
    gm = jc = None
    golden_by_id = {q["id"]: q for q in golden}
    if args.generate:
        client = g.get_anthropic()
        print(f"\nGenerating with {args.model} (k={args.k}) ...")
        run_generation(rows, retrieved, client, args.model, args.k, args.max_tokens)
        ev.print_generation(rows, args.model)
        gm = generation_metrics(rows, args.model)
        if args.judge:
            jc = judge_rows(rows, golden_by_id, client, args.judge_model, page_text_fn)
            print("Judge verdicts:", dict(jc))

    # ---- save
    run_dir.mkdir(parents=True)
    (run_dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    (run_dir / "results.json").write_text(json.dumps({"config": cfg, "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    write_summary(run_dir / "summary.md", cfg, rm, rows, gm, jc, b0_diffs)
    if args.generate:
        ra.write_csv(run_dir / "answer_review.csv", review_rows(rows, golden_by_id))
    if partial:
        print(f"\nPartial run (--limit): saved to {run_dir}, NOT added to results.csv.")
    else:
        append_results(evals / "runs" / "results.csv", results_row(cfg, run_dir, rm, gm, jc))
        print(f"\nSaved to {run_dir} and added a row to {evals / 'runs' / 'results.csv'}.")


if __name__ == "__main__":
    main()
