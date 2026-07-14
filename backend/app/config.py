from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Postgres
    database_url: str = "postgresql+psycopg://rag:changeme@postgres:5432/rag_knowledge"

    # Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "documents"

    # Anthropic
    anthropic_api_key: str = ""
    answer_model: str = "claude-sonnet-4-6"

    # Auth
    jwt_secret: str = "change-this-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480

    # Embeddings / reranking
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    use_gpu: bool = True

    # Retrieval tuning
    top_k_retrieve: int = 40
    top_k_reranked: int = 8
    chunk_size_chars: int = 1500
    chunk_overlap_chars: int = 200

    class Config:
        env_file = ".env"


settings = Settings()
