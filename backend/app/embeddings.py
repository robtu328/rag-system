"""
Wraps BAAI/bge-m3 (dense + sparse embeddings in one model) and
BAAI/bge-reranker-v2-m3 (cross-encoder reranker).

Both models are lazy-loaded singletons so the FastAPI process only pays the
load cost once, on first use, not on every request. First call will be slow
while weights download into the model_cache volume; subsequent restarts are
fast since the cache persists.
"""
from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def get_embedding_model():
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=settings.use_gpu,
        device="cuda" if settings.use_gpu else "cpu",
    )


@lru_cache(maxsize=1)
def get_reranker_model():
    from FlagEmbedding import FlagReranker

    return FlagReranker(
        settings.reranker_model,
        use_fp16=settings.use_gpu,
        device="cuda" if settings.use_gpu else "cpu",
    )


def embed_texts(texts: list[str]) -> dict:
    """
    Returns dict with 'dense' (list[list[float]]) and 'sparse'
    (list[dict[int,float]]) representations for a batch of texts.
    """
    model = get_embedding_model()
    output = model.encode(
        texts,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = [v.tolist() for v in output["dense_vecs"]]
    # bge-m3 sparse weights come back as {token_id: weight} dicts already
    sparse = output["lexical_weights"]
    return {"dense": dense, "sparse": sparse}


def embed_query(text: str) -> dict:
    return embed_texts([text])


def rerank(query: str, passages: list[str]) -> list[float]:
    """Returns a relevance score per passage, same order as input."""
    model = get_reranker_model()
    pairs = [[query, p] for p in passages]
    scores = model.compute_score(pairs, normalize=True)
    if isinstance(scores, float):
        scores = [scores]
    return scores
