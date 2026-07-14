from anthropic import Anthropic

from app.config import settings
from app.embeddings import embed_query, rerank
from app.vectorstore import get_document_chunks, search
from app.schemas import ChatTurn, SourceChunk

_client = Anthropic(api_key=settings.anthropic_api_key)

# Safety cap so an unexpectedly huge document can't blow past the model's
# context window in full-document mode. Comfortably above anything BGE-M3
# chunking at CHUNK_SIZE_CHARS produces for the doc sizes this system targets.
MAX_FULL_DOC_CHARS = 700_000

SYSTEM_PROMPT = """You are a knowledge assistant answering questions using ONLY the \
provided document excerpts. Follow these rules strictly:

1. Answer using only information found in the excerpts below. Do not use outside knowledge.
2. If the excerpts don't contain enough information to answer, say so clearly instead of guessing.
3. When you state a fact, mention which source it came from using the [filename] notation.
4. Be concise and direct. Match the language the user asked the question in \
(respond in Traditional Chinese if the question was asked in Chinese, English if asked in English).
"""


def _call_claude(query: str, history: list[ChatTurn], chunks: list[dict], max_tokens: int) -> str:
    context_block = "\n\n---\n\n".join(
        f"[{c['filename']} — chunk {c['chunk_index']}]\n{c['text']}"
        for c in chunks
    )

    messages = [{"role": turn.role, "content": turn.content} for turn in history]
    messages.append({
        "role": "user",
        "content": f"Document excerpts:\n\n{context_block}\n\n---\n\nQuestion: {query}",
    })

    response = _client.messages.create(
        model=settings.answer_model,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return "".join(block.text for block in response.content if block.type == "text")


def _answer_full_document(query: str, history: list[ChatTurn], document_id: str,
                           allowed_groups: list[str] | None) -> dict:
    chunks = get_document_chunks(document_id, allowed_groups)
    if not chunks:
        return {
            "answer": "That document doesn't exist, has no processed content, or you don't "
                       "have access to it.",
            "sources": [],
        }

    # Truncate defensively rather than ever silently exceeding the model's context window.
    total_chars = 0
    kept = []
    for c in chunks:
        total_chars += len(c["text"])
        if total_chars > MAX_FULL_DOC_CHARS:
            break
        kept.append(c)

    answer_text = _call_claude(query, history, kept, max_tokens=4096)
    if len(kept) < len(chunks):
        answer_text += (
            "\n\n(Note: this document was too large to include in full — the answer is based "
            f"on the first {len(kept)} of {len(chunks)} chunks.)"
        )

    sources = [
        SourceChunk(
            document_id=c["document_id"],
            filename=c["filename"],
            chunk_index=c["chunk_index"],
            text=c["text"][:300],
            score=1.0,
        )
        for c in kept
    ]
    return {"answer": answer_text, "sources": sources}


def answer_query(query: str, history: list[ChatTurn], allowed_groups: list[str] | None,
                  document_id: str | None = None) -> dict:
    # Full-document mode: skip similarity search entirely and feed the whole
    # document to Claude. Used for "list all X" / exhaustive-enumeration
    # questions, where top-k retrieval structurally can't return everything.
    if document_id:
        return _answer_full_document(query, history, document_id, allowed_groups)

    # 1. Embed the query (dense + sparse) with the same model used at ingestion time
    embedded = embed_query(query)
    dense_vec = embedded["dense"][0]
    sparse_vec = embedded["sparse"][0]

    # 2. Hybrid retrieval, filtered to the groups this user can see
    candidates = search(
        dense_vec=dense_vec,
        sparse_vec=sparse_vec,
        allowed_groups=allowed_groups,
        top_k=settings.top_k_retrieve,
    )

    if not candidates:
        return {
            "answer": "I couldn't find anything relevant to that question in the documents "
                       "you have access to.",
            "sources": [],
        }

    # 3. Rerank down to the best few chunks before spending context tokens
    passages = [c["text"] for c in candidates]
    scores = rerank(query, passages)
    for c, s in zip(candidates, scores):
        c["rerank_score"] = s
    candidates.sort(key=lambda c: c["rerank_score"], reverse=True)
    top_chunks = candidates[: settings.top_k_reranked]

    answer_text = _call_claude(query, history, top_chunks, max_tokens=1500)

    sources = [
        SourceChunk(
            document_id=c["document_id"],
            filename=c["filename"],
            chunk_index=c["chunk_index"],
            text=c["text"][:300],
            score=float(c["rerank_score"]),
        )
        for c in top_chunks
    ]

    return {"answer": answer_text, "sources": sources}
