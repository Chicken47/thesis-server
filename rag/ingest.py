"""
ChromaDB ingestion — loads all knowledge base markdown files,
chunks them, embeds them, and stores in local ChromaDB.

Run once to build the index. Re-run whenever you update knowledge base files.
"""

import os
import chromadb

KNOWLEDGE_BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge_base")
CHROMA_DB_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "chroma_db")

# Each collection maps to a subdirectory in knowledge_base/
COLLECTION_MAP = {
    "sector_context": "sectors",
    "governance_rules": "governance",
    "expert_templates": "templates",
}

# Map sector directory filenames to sector names for metadata
SECTOR_TAG_MAP = {
    "IT.md": "IT",
    "Banking.md": "Banking",
    "FMCG.md": "FMCG",
    "Pharma.md": "Pharma",
    "Auto.md": "Auto",
    "Energy.md": "Energy",
}


def chunk_text(text: str, chunk_size: int = 600, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks by character count."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # Try to break at a newline near the end to avoid mid-sentence cuts
        if end < len(text):
            newline_pos = text.rfind("\n", start, end)
            if newline_pos > start + chunk_size // 2:
                end = newline_pos
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if len(c) > 50]  # discard tiny chunks


def load_markdown_files(subdir: str) -> list[dict]:
    """Load all .md files from a subdirectory and return list of {text, metadata} dicts."""
    dir_path = os.path.join(KNOWLEDGE_BASE_DIR, subdir)
    docs = []
    for filename in os.listdir(dir_path):
        if not filename.endswith(".md"):
            continue
        filepath = os.path.join(dir_path, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            text = f.read()

        # Build metadata
        meta = {"source": filename, "subdir": subdir}
        if subdir == "sectors":
            meta["sector"] = SECTOR_TAG_MAP.get(filename, filename.replace(".md", ""))
        elif subdir == "governance":
            meta["type"] = "governance"
        elif subdir == "templates":
            meta["type"] = "template"

        docs.append({"text": text, "metadata": meta, "filename": filename})
    return docs


def build_index(force_rebuild: bool = False):
    """
    Ingest all knowledge base files into ChromaDB.
    Set force_rebuild=True to drop and recreate all collections.
    """
    print("Initializing ChromaDB client...")
    print("Note: First run downloads ~80MB ONNX model to ~/.cache/chroma/ — one-time only.")
    client = chromadb.PersistentClient(path=CHROMA_DB_DIR)

    for collection_name, subdir in COLLECTION_MAP.items():
        print(f"\n--- Processing collection: {collection_name} (from {subdir}/) ---")

        if force_rebuild:
            try:
                client.delete_collection(collection_name)
                print(f"  Deleted existing collection.")
            except Exception:
                pass

        # Uses ChromaDB's built-in default embedding (all-MiniLM-L6-v2 via ONNX)
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        docs = load_markdown_files(subdir)
        print(f"  Loaded {len(docs)} file(s) from knowledge_base/{subdir}/")

        for doc in docs:
            chunks = chunk_text(doc["text"])
            print(f"  {doc['filename']}: {len(chunks)} chunks")

            ids = [f"{doc['filename']}__chunk_{i}" for i in range(len(chunks))]
            metadatas = [doc["metadata"]] * len(chunks)

            # Skip chunks already in the collection (idempotent)
            existing = collection.get(ids=ids)
            existing_ids = set(existing["ids"])
            new_ids = [id_ for id_ in ids if id_ not in existing_ids]
            new_chunks = [chunks[i] for i, id_ in enumerate(ids) if id_ not in existing_ids]
            new_metas = [metadatas[i] for i, id_ in enumerate(ids) if id_ not in existing_ids]

            if new_ids:
                collection.add(documents=new_chunks, ids=new_ids, metadatas=new_metas)
                print(f"    Added {len(new_ids)} new chunks.")
            else:
                print(f"    All chunks already indexed. Skipping.")

    print("\nIndex build complete.")
    _print_summary(client)


def _print_summary(client: chromadb.PersistentClient):
    print("\n=== ChromaDB Index Summary ===")
    for name in COLLECTION_MAP:
        try:
            col = client.get_collection(name)
            print(f"  {name}: {col.count()} chunks")
        except Exception:
            print(f"  {name}: NOT FOUND")


if __name__ == "__main__":
    import sys
    force = "--force" in sys.argv
    build_index(force_rebuild=force)
