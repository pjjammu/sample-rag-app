"""
Evaluate the Laws of the Game RAG pipeline against golden_set.jsonl.

Retrieval metrics (cheap: one Voyage request + Pinecone queries, no Claude calls):
    python evaluate.py golden_set.jsonl --namespace laws-1200-150

Add the generation step (calls Claude once per question, ~46 calls):
    python evaluate.py golden_set.jsonl --namespace laws-1200-150 -k 5 --generate --model claude-haiku-4-5-20251001

Needs VOYAGE_API_KEY and PINECONE_API_KEY (and ANTHROPIC_API_KEY with --generate).
Put it next to generate.py. Use --limit 5 for a cheap smoke test.

What is measured
  Retrieval : did a retrieved chunk CONTAIN the key phrase or an alternative phrasing (strict), or come from
              an expected page (loose)? Both are lower bounds: a chunk can answer the question in other words.
              recall@k, MRR, broken down by question type.
  Generation: citations present/valid, cites an expected page, refuses the unanswerable questions,
              false refusals on answerable ones, tokens, latency, rough cost.
  NOT measured: whether the answer's content is factually correct. Read the answers in the results
              JSON (start with the ones flagged below) or add an LLM judge later.
"""
import argparse, collections, json, os, re, sys, time, datetime

import generate as g

K_LIST = (1, 3, 5, 10)
DEPTH = max(K_LIST)
PRICES = {"haiku": (1.0, 5.0), "sonnet": (2.0, 10.0)}   # USD per million tokens (in, out); verify on Anthropic's pricing page


# ------------------------------------------------------------------ pure helpers (unit-tested)
def norm(s):
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", s).strip().lower()


def hit_rank(chunks, phrases):
    """1-based rank of the first chunk containing ANY acceptable key phrase, or None.
    `phrases` may be one string or a list (the same fact is often worded differently in several places)."""
    if isinstance(phrases, str):
        phrases = [phrases]
    phrases = [norm(p) for p in phrases if p]
    for i, c in enumerate(chunks, 1):
        text = norm(c["text"])
        if any(p in text for p in phrases):
            return i
    return None


def page_rank(chunks, pages):
    """1-based rank of the first chunk from any expected page, or None."""
    for i, c in enumerate(chunks, 1):
        if c["page"] in pages:
            return i
    return None


def recall_at(ranks, k):
    return sum(1 for r in ranks if r is not None and r <= k) / len(ranks) if ranks else float("nan")


def mrr(ranks):
    return sum(1 / r for r in ranks if r) / len(ranks) if ranks else float("nan")


def is_refusal(answer):
    return norm(g.NOT_FOUND_MESSAGE) in norm(answer)


def estimate_cost(model, tokens_in, tokens_out):
    for key, (pin, pout) in PRICES.items():
        if key in model.lower():
            return tokens_in / 1e6 * pin + tokens_out / 1e6 * pout
    return None


# ------------------------------------------------------------------ retrieval (batched: 1 Voyage request)
def retrieve_all(questions, namespace, depth=DEPTH):
    import voyageai
    from pinecone import Pinecone
    vo = voyageai.Client(api_key=g._require_env("VOYAGE_API_KEY"))
    pc = Pinecone(api_key=g._require_env("PINECONE_API_KEY"))
    if g.INDEX_NAME not in pc.list_indexes().names():
        sys.exit(f"Pinecone index '{g.INDEX_NAME}' not found (set PINECONE_INDEX).")
    index = pc.Index(g.INDEX_NAME)

    # Embed every question in ONE request: the free tier allows only 3 requests/minute.
    for attempt in range(6):
        try:
            vecs = vo.embed(questions, model=g.EMBED_MODEL, input_type="query").embeddings
            break
        except Exception as e:
            if "rate" in str(e).lower() or "limit" in str(e).lower():
                print(f"  Voyage rate limited, waiting {30 * (attempt + 1)}s ...")
                time.sleep(30 * (attempt + 1))
            else:
                raise
    else:
        sys.exit("Gave up after repeated Voyage rate-limit errors.")

    out = {}
    for q, v in zip(questions, vecs):
        res = index.query(vector=v, top_k=depth, include_metadata=True, namespace=namespace)
        out[q] = [{"id": m.id, "page": int((m.metadata or {}).get("page", -1)),
                   "text": (m.metadata or {}).get("text", ""), "score": m.score} for m in res.matches]
    return out


# ------------------------------------------------------------------ reporting
def pct(x):
    return "  n/a" if x != x else f"{100 * x:5.1f}%"


def print_retrieval(rows):
    ans = [r for r in rows if r["answerable"]]
    print(f"\n=== RETRIEVAL ({len(ans)} answerable questions) ===")
    header = "".join(f"  recall@{k:<2}" for k in K_LIST) + "   MRR"
    print(f"{'':<14}{header}")

    def line(label, subset, key):
        ranks = [r[key] for r in subset]
        print(f"{label:<14}" + "".join(f"  {pct(recall_at(ranks, k)):>8}" for k in K_LIST) + f"  {mrr(ranks):.3f}")

    line("phrase (all)", ans, "rank")
    line("page   (all)", ans, "page_rank")
    print("\nby type (phrase match):")
    for t in sorted({r["type"] for r in ans}):
        sub = [r for r in ans if r["type"] == t]
        line(f"{t} ({len(sub)})", sub, "rank")

    miss = [r for r in ans if r["rank"] is None]
    print(f"\nnever retrieved in top {DEPTH} ({len(miss)}):")
    for r in miss:
        print(f"  {r['id']} [{r['type']}] {r['question']}")

    top_ans = [r["top_score"] for r in ans if r["top_score"] is not None]
    top_un = [r["top_score"] for r in rows if not r["answerable"] and r["top_score"] is not None]
    if top_ans and top_un:
        print(f"\nmean top-1 similarity: answerable {sum(top_ans)/len(top_ans):.3f} vs "
              f"unanswerable {sum(top_un)/len(top_un):.3f} (a gap here would support a score threshold)")


def print_generation(rows, model):
    done = [r for r in rows if "gen" in r and "error" not in r["gen"]]
    errs = [r for r in rows if "gen" in r and "error" in r["gen"]]
    ans = [r for r in done if r["answerable"]]
    un = [r for r in done if not r["answerable"]]
    print(f"\n=== GENERATION ({len(done)} answered, {len(errs)} errors) ===")
    if ans:
        n = len(ans)
        refused = [r for r in ans if r["gen"]["refused"]]
        cited = [r for r in ans if r["gen"]["cited_pages"]]
        right_page = [r for r in ans if set(r["gen"]["cited_pages"]) & set(r["expected_pages"])]
        unsupported = [r for r in ans if r["gen"]["unsupported_citations"]]
        print(f"answerable: false refusals {len(refused)}/{n} | with citations {len(cited)}/{n} | "
              f"cite an expected page {len(right_page)}/{n} | unsupported citations {len(unsupported)}/{n}")
        flag = [r for r in ans if not r["gen"]["refused"] and r not in right_page]
        if flag:
            print("review these answers (no expected page cited):")
            for r in flag:
                print(f"  {r['id']} {r['question']}  -> cited {r['gen']['cited_pages']}")
        if refused:
            print("false refusals (retrieval found text but Claude declined):")
            for r in refused:
                print(f"  {r['id']} {r['question']}  (phrase rank: {r['rank']})")
    if un:
        ok = [r for r in un if r["gen"]["refused"]]
        print(f"unanswerable: correct refusals {len(ok)}/{len(un)}")
        for r in un:
            if not r["gen"]["refused"]:
                print(f"  ANSWERED ANYWAY {r['id']} {r['question']}\n    -> {r['gen']['answer'][:200]!r}")
    tin = sum(r["gen"]["input_tokens"] for r in done)
    tout = sum(r["gen"]["output_tokens"] for r in done)
    lat = [r["gen"]["latency_s"] for r in done if r["gen"]["latency_s"]]
    trunc = sum(1 for r in done if r["gen"]["truncated"])
    cost = estimate_cost(model, tin, tout)
    print(f"tokens {tin} in / {tout} out | truncated {trunc} | mean latency "
          f"{(sum(lat)/len(lat) if lat else 0):.1f}s | est. cost "
          f"{'n/a' if cost is None else f'${cost:.3f}'} (rough)")
    for r in errs:
        print(f"  ERROR {r['id']}: {r['gen']['error']}")


# ------------------------------------------------------------------ main
def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("golden")
    ap.add_argument("--namespace", default="laws-1200-150")
    ap.add_argument("-k", type=int, default=5, help="chunks passed to Claude with --generate")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--model", default=g.DEFAULT_MODEL)
    ap.add_argument("--max-tokens", type=int, default=600)
    ap.add_argument("--limit", type=int, default=0, help="only the first N questions")
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)

    with open(args.golden, encoding="utf-8") as f:
        golden = [json.loads(l) for l in f if l.strip()]
    if args.limit:
        golden = golden[:args.limit]
    print(f"{len(golden)} questions | namespace '{args.namespace}' | embed model {g.EMBED_MODEL}")

    retrieved = retrieve_all([q["question"] for q in golden], args.namespace, depth=max(DEPTH, args.k))

    rows = []
    for q in golden:
        chunks = retrieved[q["question"]]
        rows.append({
            "id": q["id"], "type": q["type"], "answerable": q["answerable"], "question": q["question"],
            "expected_pages": q["expected_pages"], "key_phrase": q["key_phrase"], "alt_phrases": q.get("alt_phrases", []),
            "rank": hit_rank(chunks[:DEPTH], [q["key_phrase"]] + q.get("alt_phrases", [])) if q["answerable"] else None,
            "page_rank": page_rank(chunks[:DEPTH], set(q["expected_pages"])) if q["answerable"] else None,
            "top_score": chunks[0]["score"] if chunks else None,
            "retrieved": [{"id": c["id"], "page": c["page"], "score": c["score"]} for c in chunks[:DEPTH]],
        })
    print_retrieval(rows)

    if args.generate:
        client = g.get_anthropic()
        cache = retrieved
        print(f"\nGenerating with {args.model} (k={args.k}) ...")
        for row in rows:
            try:
                res = g.answer(row["question"], retrieve_fn=lambda qq, kk: cache[qq][:kk], client=client,
                               model=args.model, k=args.k, max_tokens=args.max_tokens)
                row["gen"] = {
                    "answer": res["answer"], "refused": is_refusal(res["answer"]),
                    "cited_pages": sorted(res["cited_pages"]),
                    "unsupported_citations": sorted(res["unsupported_citations"]),
                    "input_tokens": res["input_tokens"], "output_tokens": res["output_tokens"],
                    "latency_s": res["latency_s"], "truncated": res["truncated"],
                }
            except Exception as e:                       # one failure shouldn't kill the whole run
                row["gen"] = {"error": f"{type(e).__name__}: {e}"}
        print_generation(rows, args.model)

    out = args.out or f"eval_{args.namespace}_k{args.k}{'_gen' if args.generate else ''}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"when": datetime.datetime.now().isoformat(timespec="seconds"), "namespace": args.namespace,
                   "embed_model": g.EMBED_MODEL, "k": args.k, "model": args.model if args.generate else None,
                   "rows": rows}, f, indent=2, ensure_ascii=False)
    print(f"\nPer-question details written to {out}")


if __name__ == "__main__":
    main()
