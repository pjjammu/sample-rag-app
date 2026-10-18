"""
Embed chunks with Voyage AI and store / search them in Pinecone.

Setup:
    pip install voyageai pinecone
    Set VOYAGE_API_KEY and PINECONE_API_KEY
    (Windows: setx VOYAGE_API_KEY "your-key", then open a NEW terminal)

Usage:
    python rag.py index  laws_chunks.jsonl --namespace laws-1200-150
    python rag.py search "When is a player in an offside position?" --namespace laws-1200-150 -k 5

One namespace per chunking setup lets you compare chunk sizes inside the same index.
"""
import argparse, hashlib, json, os, sys, time
import voyageai
from pinecone import Pinecone, ServerlessSpec

EMBED_MODEL = "voyage-4-lite"   # confirm current model names in Voyage's docs
INDEX_NAME = "laws-rag"
CACHE_FILE = f"embed_cache_{EMBED_MODEL}.json"

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
VOYAGE_API_KEY = os.environ.get("VOYAGE_API_KEY")

# Voyage free trial without a payment method: 3 requests/min and 10K tokens/min.
BATCH_TOKENS = 7000
PAUSE_SECONDS = int(os.environ.get("EMBED_PAUSE", "21"))   # set EMBED_PAUSE=0 on a paid tier
CHARS_PER_TOKEN = 3                                        # conservative estimate

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_chunks(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def sha(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def make_batches(texts, max_tokens=BATCH_TOKENS, max_items=128):
    batches, cur, cur_t = [], [], 0
    for t in texts:
        est = len(t) // CHARS_PER_TOKEN + 1
        if cur and (cur_t + est > max_tokens or len(cur) >= max_items):
            batches.append(cur)
            cur, cur_t = [], 0
        cur.append(t)
        cur_t += est
    if cur:
        batches.append(cur)
    return batches


def embed_texts(vo, texts, input_type):
    """Embed one batch, retrying politely when rate limited."""
    for attempt in range(6):
        try:
            return vo.embed(texts, model=EMBED_MODEL, input_type=input_type).embeddings
        except Exception as e:
            msg = str(e).lower()
            if "rate" in msg or "limit" in msg:
                wait = 30 * (attempt + 1)
                print(f"  rate limited, waiting {wait}s ...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("Gave up after repeated rate-limit errors")


def get_index(pc, dim, create):
    names = pc.list_indexes().names()
    if INDEX_NAME not in names:
        if not create:
            sys.exit(f"Index '{INDEX_NAME}' does not exist yet. Run the 'index' command first.")
        # Starter (free) plan runs on AWS us-east-1 only
        pc.create_index(name=INDEX_NAME, dimension=dim, metric="cosine",
                        spec=ServerlessSpec(cloud="aws", region="us-east-1"))
        while not pc.describe_index(INDEX_NAME).status["ready"]:
            time.sleep(1)
    elif dim and pc.describe_index(INDEX_NAME).dimension != dim:
        sys.exit(f"Index '{INDEX_NAME}' has a different dimension than {EMBED_MODEL} produces. "
                 f"Delete it in the Pinecone console or change INDEX_NAME.")
    return pc.Index(INDEX_NAME)


def cmd_index(args):
    vo = voyageai.Client(api_key=VOYAGE_API_KEY)                       # reads VOYAGE_API_KEY
    chunks = load_chunks(args.chunks)
    cache = {}
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    todo = sorted({c["text"] for c in chunks if sha(c["text"]) not in cache})
    print(f"{len(chunks)} chunks; {len(todo)} texts to embed ({len(chunks) - len(todo)} cached)")

    batches = make_batches(todo)
    for i, batch in enumerate(batches, 1):
        for text, vec in zip(batch, embed_texts(vo, batch, "document")):
            cache[sha(text)] = vec
        with open(CACHE_FILE, "w", encoding="utf-8") as f:   # save progress as we go
            json.dump(cache, f)
        print(f"  embedded batch {i}/{len(batches)}")
        if i < len(batches):
            time.sleep(PAUSE_SECONDS)

    dim = len(next(iter(cache.values())))
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = get_index(pc, dim, create=True)
    records = []
    for c in chunks:
        meta = {k: v for k, v in c.items() if k != "id" and v is not None}   # Pinecone rejects nulls
        records.append({"id": c["id"], "values": cache[sha(c["text"])], "metadata": meta})
    for i in range(0, len(records), 100):
        index.upsert(vectors=records[i:i + 100], namespace=args.namespace)
    print(f"Upserted {len(records)} vectors into '{INDEX_NAME}' (namespace '{args.namespace}', dim {dim}).")
    print("New vectors can take a few seconds to become searchable.")


def cmd_search(args):
    vo = voyageai.Client(api_key=VOYAGE_API_KEY)
    qvec = embed_texts(vo, [args.question], "query")[0]
    pc = Pinecone(api_key=PINECONE_API_KEY)
    index = get_index(pc, dim=None, create=False)
    res = index.query(vector=qvec, top_k=args.k, include_metadata=True, namespace=args.namespace)
    for m in res.matches:
        md = m.metadata or {}
        print(f"\n[{m.score:.3f}] page {md.get('page')}  ({m.id})")
        print((md.get("text") or "")[:500].replace("\n", " "))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("index")
    p1.add_argument("chunks")
    p1.add_argument("--namespace", default="laws-1200-150")
    p1.set_defaults(fn=cmd_index)
    p2 = sub.add_parser("search")
    p2.add_argument("question")
    p2.add_argument("--namespace", default="laws-1200-150")
    p2.add_argument("-k", type=int, default=5)
    p2.set_defaults(fn=cmd_search)
    a = ap.parse_args()
    a.fn(a)