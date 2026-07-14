import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from app.config import settings

DENSE_DIM = 1024  # BAAI/bge-m3 dense output size


@lru_cache(maxsize=1)
def get_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url)


def ensure_collection():
    client = get_client()
    existing = [c.name for c in client.get_collections().collections]
    if settings.qdrant_collection in existing:
        return

    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config={
            "dense": qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": qm.SparseVectorParams(),
        },
    )
    # Payload index so filtering by group is fast even at large scale
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="groups",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )
    client.create_payload_index(
        collection_name=settings.qdrant_collection,
        field_name="document_id",
        field_schema=qm.PayloadSchemaType.KEYWORD,
    )


def upsert_chunks(document_id: str, filename: str, groups: list[str], chunks: list[str],
                   dense_vecs: list[list[float]], sparse_vecs: list[dict]):
    client = get_client()
    points = []
    for i, (text, dense, sparse) in enumerate(zip(chunks, dense_vecs, sparse_vecs)):
        sparse_indices = [int(k) for k in sparse.keys()]
        sparse_values = [float(v) for v in sparse.values()]
        points.append(
            qm.PointStruct(
                id=str(uuid.uuid4()),
                vector={
                    "dense": dense,
                    "sparse": qm.SparseVector(indices=sparse_indices, values=sparse_values),
                },
                payload={
                    "document_id": document_id,
                    "filename": filename,
                    "chunk_index": i,
                    "text": text,
                    "groups": groups,
                },
            )
        )
    client.upsert(collection_name=settings.qdrant_collection, points=points)


def delete_document(document_id: str):
    client = get_client()
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(
                must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id))]
            )
        ),
    )


def get_document_chunks(document_id: str, allowed_groups: list[str] | None) -> list[dict]:
    """
    Fetch every chunk belonging to one document, ordered by chunk_index.
    Used for "full document" mode instead of similarity search, so queries
    like "list all X" see the whole document rather than just the top-k
    most-similar chunks. allowed_groups=None skips the group filter (admin).
    """
    client = get_client()

    must = [qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id))]
    if allowed_groups is not None:
        must.append(qm.FieldCondition(key="groups", match=qm.MatchAny(any=allowed_groups)))

    points = []
    offset = None
    while True:
        batch, offset = client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=qm.Filter(must=must),
            limit=256,
            offset=offset,
            with_payload=True,
        )
        points.extend(batch)
        if offset is None:
            break

    points.sort(key=lambda p: p.payload["chunk_index"])
    return [
        {
            "document_id": p.payload["document_id"],
            "filename": p.payload["filename"],
            "chunk_index": p.payload["chunk_index"],
            "text": p.payload["text"],
        }
        for p in points
    ]


def search(dense_vec: list[float], sparse_vec: dict, allowed_groups: list[str] | None,
           top_k: int) -> list[dict]:
    """
    Hybrid search (dense + sparse) filtered by the requesting user's allowed
    groups. Pass allowed_groups=None to skip the filter (admin/global search).
    """
    client = get_client()

    query_filter = None
    if allowed_groups is not None:
        query_filter = qm.Filter(
            must=[qm.FieldCondition(key="groups", match=qm.MatchAny(any=allowed_groups))]
        )

    sparse_indices = [int(k) for k in sparse_vec.keys()]
    sparse_values = [float(v) for v in sparse_vec.values()]

    results = client.query_points(
        collection_name=settings.qdrant_collection,
        prefetch=[
            qm.Prefetch(
                query=dense_vec,
                using="dense",
                filter=query_filter,
                limit=top_k,
            ),
            qm.Prefetch(
                query=qm.SparseVector(indices=sparse_indices, values=sparse_values),
                using="sparse",
                filter=query_filter,
                limit=top_k,
            ),
        ],
        query=qm.FusionQuery(fusion=qm.Fusion.RRF),
        limit=top_k,
        with_payload=True,
    )

    return [
        {
            "document_id": p.payload["document_id"],
            "filename": p.payload["filename"],
            "chunk_index": p.payload["chunk_index"],
            "text": p.payload["text"],
            "score": p.score,
        }
        for p in results.points
    ]
