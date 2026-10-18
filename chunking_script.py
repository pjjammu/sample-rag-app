"""
Simple chunking of the IFAB Laws of the Game PDF.

    pip install pymupdf langchain-text-splitters
    python simple_chunk.py "path/to/Laws_of_the_Game_2026_27_single_pages.pdf" laws_chunks.jsonl

Keep the PDF and the output file out of your public repo (IFAB copyright).
"""
import json, re, sys
import pymupdf
from langchain_text_splitters import RecursiveCharacterTextSplitter

CHUNK_SIZE = 1200      # characters (~300 tokens) -- change these to experiment
CHUNK_OVERLAP = 150
START_PAGE = 9         # skip cover, copyright and contents pages


def clean(text: str) -> str:
    text = re.sub(r"•\s*\x07", "• ", text)              # bullet markers
    text = text.replace("\x07", "\n\n")                  # PDF's paragraph-start marker
    text = text.replace("\u2002", " ").replace("\t", " ").replace("\u00a0", " ")
    lines = text.split("\n")
    if lines and re.fullmatch(r"\s*\d{1,3}\s*", lines[0]):   # leading page number
        lines = lines[1:]
    lines = [l for l in lines if "Laws of the Game 2026/27" not in l]  # running header
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def main(pdf_path: str, out_path: str):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    doc = pymupdf.open(pdf_path)
    records = []
    for pn in range(START_PAGE, len(doc) + 1):
        text = clean(doc[pn - 1].get_text("text"))
        if len(text.split()) < 20:                       # blank / divider pages
            continue
        for piece in splitter.split_text(text):
            records.append({"id": f"p{pn:03d}-{len(records):04d}", "page": pn, "text": piece})
    with open(out_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    sizes = sorted(len(r["text"]) for r in records)
    print(f"{len(records)} chunks -> {out_path}")
    print(f"chars: min {sizes[0]}, median {sizes[len(sizes)//2]}, max {sizes[-1]}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "laws_chunks.jsonl")