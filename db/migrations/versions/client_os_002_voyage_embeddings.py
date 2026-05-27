"""client_os_002 — recria knowledge_embeddings com vector(1024) para Voyage AI

Revision ID: client_os_002_voyage
Revises: client_os_001
Create Date: 2026-05-26

Esta migration substitui os embeddings hash-based fake (vector(1536)) pelos
embeddings reais via Voyage AI (vector(1024) — default do voyage-4-large).

Como TODOS os embeddings antigos eram fakes (hash sha256 — sem semântica real),
DESCARTAMOS tudo e recriamos a tabela. Documentos em `knowledge_documents` são
preservados; só os embeddings são apagados. Reindexação é feita pelo script:
    python scripts/migrate_embeddings_to_voyage.py
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# ── Identificadores ───────────────────────────────────────────────────────────
revision: str = "client_os_002_voyage"
down_revision: Union[str, None] = "client_os_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Recria knowledge_embeddings com vector(1024).
    Embeddings antigos (hash fake) são descartados — sem perda real de informação.
    """
    # ── 1. Apagar tabela antiga (CASCADE remove dependências) ──
    op.execute("DROP TABLE IF EXISTS knowledge_embeddings CASCADE")

    # ── 2. Garantir extensão pgvector ──
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── 3. Recriar com vector(1024) ──
    op.execute("""
        CREATE TABLE knowledge_embeddings (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            embedding vector(1024),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # ── 4. Índice de similaridade (ivfflat ou hnsw) ──
    # hnsw é mais rápido pra busca, mais lento pra criar. Como vamos ter
    # poucos documentos no início, hnsw funciona bem.
    op.execute("""
        CREATE INDEX idx_knowledge_embeddings_vector
        ON knowledge_embeddings
        USING hnsw (embedding vector_cosine_ops)
    """)

    # ── 5. Índice por document_id (lookup rápido na re-indexação) ──
    op.execute("""
        CREATE INDEX idx_knowledge_embeddings_document_id
        ON knowledge_embeddings (document_id)
    """)


def downgrade() -> None:
    """
    Reverte para vector(1536). Apaga embeddings Voyage; reindexação posterior
    teria que regerar com modelo antigo (mas isso não faz sentido — o hash
    fake era lixo). Downgrade existe só por completude.
    """
    op.execute("DROP TABLE IF EXISTS knowledge_embeddings CASCADE")

    op.execute("""
        CREATE TABLE knowledge_embeddings (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL,
            embedding vector(1536),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX idx_knowledge_embeddings_vector
        ON knowledge_embeddings
        USING hnsw (embedding vector_cosine_ops)
    """)

    op.execute("""
        CREATE INDEX idx_knowledge_embeddings_document_id
        ON knowledge_embeddings (document_id)
    """)
