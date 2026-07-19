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

BASE_SYSTEM_PROMPT = """You are a knowledge assistant answering questions using ONLY the \
provided document excerpts. Follow these rules strictly:

1. Answer using only information found in the excerpts below. Do not use outside knowledge.
2. If the excerpts don't contain enough information to answer, say so clearly instead of guessing.
3. When you state a fact, mention which source it came from using the [filename] notation.
4. Be concise and direct."""

AUTO_LANGUAGE_RULE = " Match the language the user asked the question in (respond in " \
    "Traditional Chinese if the question was asked in Chinese, English if asked in English)."


def _build_system_prompt(answer_language: str | None) -> str:
    if answer_language:
        # Pinned explicitly, so it overrides whatever language the question or
        # prior conversation history happens to be in — auto-detection was
        # unreliable once a chat had mixed-language history.
        language_rule = f" Answer in {answer_language}, regardless of what language the " \
            "question or earlier conversation turns used."
    else:
        language_rule = AUTO_LANGUAGE_RULE
    return BASE_SYSTEM_PROMPT + language_rule + "\n"

SUMMARY_SYSTEM_PROMPT = """You produce a comprehensive, structured extract of one part of a \
larger document for later use as retrieval context, so it must be exhaustive:

1. Do not omit any distinct item, requirement, clause, section heading, or fact from this part \
— err on the side of completeness over brevity.
2. Preserve the document's own numbering/section structure where present, so items can still \
be traced back to their source location.
3. Use plain text with clear headings and numbered/bulleted lists, not prose summarization.
4. This is only one part of the document — extract only what's in the text given to you, don't \
speculate about other parts.
"""

# Chars per section sent to a single extraction call. A single-pass call over
# a large document risks getting cut off by max_tokens before reaching later
# content (observed: a 178-chunk/~267K-char doc's extract was truncated
# mid-section-1, never reaching the actual requirements in chapters 4-12).
# Sectioning gives every part of the document its own output budget.
SUMMARY_SECTION_CHARS = 50_000


def generate_document_summary(filename: str, full_text: str) -> str:
    """
    One-time, ingestion-time extraction of a structured, exhaustive extract of
    the document. This is later used as cheap context for full-document-mode
    questions instead of resending every raw chunk on every query.
    """
    text = full_text[:MAX_FULL_DOC_CHARS]
    sections = [text[i:i + SUMMARY_SECTION_CHARS] for i in range(0, len(text), SUMMARY_SECTION_CHARS)]
    if not sections:
        sections = [""]

    parts = []
    for i, section in enumerate(sections):
        response = _client.messages.create(
            model=settings.answer_model,
            max_tokens=8192,
            system=SUMMARY_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": (
                    f"Document: {filename} — part {i + 1} of {len(sections)}\n\n{section}"
                    "\n\n---\n\nProduce the structured extract for this part now."
                ),
            }],
        )
        parts.append("".join(block.text for block in response.content if block.type == "text"))

    return "\n\n".join(parts)


def _call_claude(query: str, history: list[ChatTurn], chunks: list[dict], max_tokens: int,
                  answer_language: str | None = None) -> str:
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
        system=_build_system_prompt(answer_language),
        messages=messages,
    )
    return "".join(block.text for block in response.content if block.type == "text")


def _answer_full_document(query: str, history: list[ChatTurn], document_id: str,
                           allowed_groups: list[str] | None,
                           document_summary: str | None,
                           answer_language: str | None) -> dict:
    # get_document_chunks is the ACL gate: it applies the same group filter as
    # normal search, so access is enforced here regardless of whether we end
    # up using the pre-computed summary or the raw chunks below.
    chunks = get_document_chunks(document_id, allowed_groups)
    if not chunks:
        return {
            "answer": "That document doesn't exist, has no processed content, or you don't "
                       "have access to it.",
            "sources": [],
        }

    if document_summary:
        # Cheap path: the ingestion-time extract already covers the whole
        # document, so there's no need to resend every raw chunk per query.
        context = [{
            "filename": chunks[0]["filename"],
            "chunk_index": -1,
            "text": document_summary,
        }]
        answer_text = _call_claude(query, history, context, max_tokens=8192,
                                    answer_language=answer_language)
        sources = [
            SourceChunk(
                document_id=document_id,
                filename=chunks[0]["filename"],
                chunk_index=-1,
                text=document_summary[:300],
                score=1.0,
            )
        ]
        return {"answer": answer_text, "sources": sources}

    # Fallback for documents ingested before summary generation existed, or
    # where generation failed: concatenate raw chunks like before.
    total_chars = 0
    kept = []
    for c in chunks:
        total_chars += len(c["text"])
        if total_chars > MAX_FULL_DOC_CHARS:
            break
        kept.append(c)

    answer_text = _call_claude(query, history, kept, max_tokens=8192,
                                answer_language=answer_language)
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
                  document_id: str | None = None, document_summary: str | None = None,
                  answer_language: str | None = None) -> dict:
    # Full-document mode: skip similarity search entirely and feed the whole
    # document to Claude. Used for "list all X" / exhaustive-enumeration
    # questions, where top-k retrieval structurally can't return everything.
    if document_id:
        return _answer_full_document(query, history, document_id, allowed_groups,
                                      document_summary, answer_language)

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

    answer_text = _call_claude(query, history, top_chunks, max_tokens=1500,
                                answer_language=answer_language)

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
