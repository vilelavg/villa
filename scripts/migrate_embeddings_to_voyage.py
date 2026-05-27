"""
Villa — Migração de embeddings para Voyage AI

Reindexa todos os documentos existentes em `knowledge_documents` usando
o Voyage AI como backend de embeddings (substitui o hash fake antigo).

Pré-requisitos:
    1. Migration `client_os_002_voyage_embeddings` aplicada (tabela vector(1024))
    2. VOYAGE_API_KEY configurada no .env
    3. Pacote `voyageai` instalado

Uso (do diretório raiz do repo, com venv ativo):
    python scripts/migrate_embeddings_to_voyage.py

Idempotente: se rodar duas vezes, os embeddings da segunda rodada substituem
os da primeira (não cria duplicatas).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Garantir que o root do projeto está no sys.path quando rodado direto
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from core.config import settings  # noqa: E402
from core.database import get_db_session  # noqa: E402
from core.models import KnowledgeDocument  # noqa: E402
from memory.embeddings import EmbeddingService  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("villa.migrate_voyage")


async def main() -> None:
    """Reindexa todos os documentos usando Voyage AI."""

    # Sanity checks
    if not settings.voyage_api_key:
        logger.error("VOYAGE_API_KEY não configurada no .env. Abortando.")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Villa — Migração de embeddings para Voyage AI")
    logger.info("=" * 60)
    logger.info("Modelo:    %s", settings.voyage_model)
    logger.info("Dimensão:  %d", settings.voyage_dimension)
    logger.info("Banco:     %s", settings.async_database_url.split("@")[-1])
    logger.info("=" * 60)

    total_docs = 0
    total_chunks = 0
    failed: list[tuple[str, str]] = []

    async with get_db_session() as db:
        # Listar todos os documentos
        result = await db.execute(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at))
        documents = result.scalars().all()

        if not documents:
            logger.warning("Nenhum documento em knowledge_documents. Nada a reindexar.")
            return

        logger.info("Encontrados %d documentos. Iniciando reindexação...", len(documents))

        emb_service = EmbeddingService(db)

        for doc in documents:
            try:
                if not doc.content or not doc.content.strip():
                    logger.warning("Documento %s sem conteúdo — pulando", doc.id)
                    continue

                logger.info(
                    "Reindexando: %s | type=%s | size=%d chars",
                    doc.title[:60], doc.doc_type, len(doc.content),
                )
                chunks = await emb_service.index_document(doc.id, doc.content)
                logger.info("  → %d chunks indexados", chunks)

                total_docs += 1
                total_chunks += chunks

            except Exception as e:
                logger.exception("Erro ao reindexar %s: %s", doc.id, e)
                failed.append((str(doc.id), str(e)))

        # Commit final
        await db.commit()

    # Relatório
    logger.info("=" * 60)
    logger.info("Migração concluída")
    logger.info("  Documentos reindexados: %d / %d", total_docs, len(documents))
    logger.info("  Total de chunks: %d", total_chunks)
    if failed:
        logger.warning("  Falhas: %d", len(failed))
        for doc_id, err in failed:
            logger.warning("    %s: %s", doc_id, err[:80])
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
