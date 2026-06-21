"""
RAG (Retrieval-Augmented Generation) layer for SignalEdge.

ChromaDB stores all fetched headlines as vector embeddings. Given any text
(a market question or a chatbot query), find_relevant() returns the most
semantically similar articles from the store.

Why ChromaDB over plain SQL LIKE queries?
  SQL matches exact keywords. ChromaDB matches MEANING.
  "Fed interest rate decision" will surface "FOMC monetary policy stance"
  even though those strings share no words.

Persistence: the ChromaDB collection lives at db/chroma/ and survives
between pipeline runs — articles accumulate over time.
Tests inject chromadb.EphemeralClient() so no disk I/O occurs in CI.
"""

import os
import chromadb

CHROMA_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "chroma")
COLLECTION_NAME = "headlines"


def _get_collection(client=None) -> chromadb.Collection:
    """Return (or create) the headlines collection.
    Pass an EphemeralClient in tests to avoid touching the filesystem."""
    if client is None:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
    # default_ef uses sentence-transformers/all-MiniLM-L6-v2 (~90 MB, auto-downloaded once)
    return client.get_or_create_collection(name=COLLECTION_NAME)


def index_articles(articles: list[dict], client=None) -> int:
    """
    Add new articles to the ChromaDB vector store.
    Skips articles already present (idempotent — safe to call every pipeline run).
    Returns the count of newly indexed articles.

    Document text = title + description (richer context than title alone).
    Metadata stores id, url, source, published_at so we can return full article
    objects from find_relevant() without an extra DB round-trip.
    """
    if not articles:
        return 0

    collection = _get_collection(client)

    # Pre-check which IDs already exist to avoid ChromaDB DuplicateIDError
    existing_ids = set(collection.get(include=[])["ids"])
    new = [a for a in articles if a["id"] not in existing_ids]

    if not new:
        return 0

    collection.add(
        ids=[a["id"] for a in new],
        # Concatenate title + description — embedding richer text improves retrieval
        documents=[
            f"{a['title']}. {a.get('description', '')}".strip()
            for a in new
        ],
        metadatas=[
            {
                "id": a["id"],
                "title": a["title"],
                "url": a.get("url", ""),
                "source": a.get("source", ""),
                "published_at": a.get("published_at", ""),
                "description": a.get("description", ""),
            }
            for a in new
        ],
    )

    return len(new)


def find_relevant(question: str, top_k: int = 10, client=None) -> list[dict]:
    """
    Semantic search: return the top_k articles most relevant to `question`.

    ChromaDB embeds the question and the stored documents using the same model,
    then returns the nearest neighbours by cosine distance. The returned list
    is already ordered from most to least relevant.

    Returns an empty list if the store is empty (e.g. first run before indexing).
    """
    collection = _get_collection(client)

    total = collection.count()
    if total == 0:
        return []

    # Request at most as many results as exist in the collection
    n_results = min(top_k, total)

    results = collection.query(
        query_texts=[question],
        n_results=n_results,
        include=["metadatas"],
    )

    # results["metadatas"] is a list-of-lists (one per query). We only send one query.
    return results["metadatas"][0]
