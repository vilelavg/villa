"""
Villa — Serviço de Embeddings (Voyage AI)
Vetorização de documentos para busca semântica (RAG).
Usa pgvector no PostgreSQL para armazenamento e busca por similaridade.

ATUALIZAÇÃO (2026-05-26): Substituído backend hash-based fake por Voyage AI.
- Modelo: voyage-4-large (multilíngue, melhor para português)
- Dimensão: 1024 (default Voyage, schema vector(1024))
- Input types: 'document' na indexação, 'query' na busca
- Free tier: 200M tokens grátis nos primeiros 3 meses

A interface pública é a mesma da versão anterior — módulos consumidores
(knowledge_base, feedback_loop) não precisam mudar.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import uuid4

import voyageai
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.models import KnowledgeDocument, KnowledgeEmbedding

logger = logging.getLogger(__name__)

# ── Configurações de chunking ──
CHUNK_SIZE = 500  # Caracteres por chunk
CHUNK_OVERLAP = 75  # Sobreposição entre chunks
EMBEDDING_DIMENSION = settings.voyage_dimension  # 1024 (Voyage default)


# ══════════════════════════════════════════════════════════════════════════════
# Cliente Voyage (singleton)
# ══════════════════════════════════════════════════════════════════════════════
class _VoyageClientHolder:
    """Wrapper que cria o client Voyage só quando precisamos (lazy init)."""

    _client: voyageai.Client | None = None

    @classmethod
    def get(cls) -> voyageai.Client:
        if cls._client is None:
            if not settings.voyage_api_key:
                raise RuntimeError(
                    "VOYAGE_API_KEY não configurada no .env. "
                    "Pegue a key em https://www.voyageai.com/ e adicione ao .env."
                )
            cls._client = voyageai.Client(
                api_key=settings.voyage_api_key,
                max_retries=settings.voyage_max_retries,
            )
            logger.info(
                "Voyage client inicializado | modelo=%s | dim=%d",
                settings.voyage_model,
                settings.voyage_dimension,
            )
        return cls._client


# ══════════════════════════════════════════════════════════════════════════════
# Serviço principal
# ══════════════════════════════════════════════════════════════════════════════
class EmbeddingService:
    """
    Gerencia embeddings vetoriais para busca semântica.

    Pipeline:
        1. Documento é dividido em chunks
        2. Cada chunk é vetorizado via Voyage AI (input_type='document')
        3. Embeddings são armazenados no pgvector
        4. Busca: query é vetorizada (input_type='query') → busca por similaridade coseno

    Uso:
        emb = EmbeddingService(db)
        await emb.index_document(doc_id, "Conteúdo do documento...")
        results = await emb.search("como tratar implante que quebrou?", limit=5)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    # ═══════════════════════════════════════════════════
    # INDEXAÇÃO
    # ═══════════════════════════════════════════════════

    async def index_document(
        self,
        document_id: str,
        content: str,
        chunk_size: int = CHUNK_SIZE,
        chunk_overlap: int = CHUNK_OVERLAP,
    ) -> int:
        """
        Divide um documento em chunks e gera embeddings via Voyage AI.

        Args:
            document_id: ID do KnowledgeDocument
            content: Texto completo do documento
            chunk_size: Tamanho de cada chunk em caracteres
            chunk_overlap: Sobreposição entre chunks

        Returns:
            Número de chunks indexados
        """
        # Limpar embeddings antigos deste documento (re-indexação)
        await self.db.execute(
            delete(KnowledgeEmbedding).where(KnowledgeEmbedding.document_id == document_id)
        )

        # Dividir em chunks
        chunks = self._split_into_chunks(content, chunk_size, chunk_overlap)
        if not chunks:
            return 0

        # Atualizar chunks no documento (campo JSON)
        doc_result = await self.db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc:
            doc.chunks = [{"text": c, "index": i} for i, c in enumerate(chunks)]

        # Gerar embeddings via Voyage (em batches)
        embeddings = await self._embed_texts(chunks, input_type="document")

        # Salvar no banco
        for i, (chunk_text, embedding_vector) in enumerate(zip(chunks, embeddings)):
            entry = KnowledgeEmbedding(
                id=str(uuid4()),
                document_id=document_id,
                chunk_index=i,
                chunk_text=chunk_text,
            )
            self.db.add(entry)
            await self.db.flush()

            # Setar o vetor via SQL raw (pgvector aceita texto no formato '[v1,v2,...]')
            vec_str = "[" + ",".join(str(v) for v in embedding_vector) + "]"
            await self.db.execute(
                text(
                    "UPDATE knowledge_embeddings "
                    "SET embedding = CAST(:vec AS vector) "
                    "WHERE id = CAST(:id AS UUID)"
                ).bindparams(vec=vec_str, id=entry.id)
            )

        await self.db.flush()
        logger.info(
            "Indexado document_id=%s | chunks=%d | dim=%d",
            document_id,
            len(chunks),
            EMBEDDING_DIMENSION,
        )
        return len(chunks)

    async def index_text(
        self,
        title: str,
        content: str,
        doc_type: str,
        client_slug: str | None = None,
        source: str | None = None,
        source_url: str | None = None,
    ) -> dict:
        """
        Cria um documento E indexa em uma operação.
        Atalho para uso rápido.

        Returns:
            Dict com document_id e chunks_count
        """
        from core.models import Client

        client_id = None
        if client_slug:
            result = await self.db.execute(select(Client.id).where(Client.slug == client_slug))
            client_id = result.scalar_one_or_none()

        doc = KnowledgeDocument(
            id=str(uuid4()),
            client_id=client_id,
            title=title,
            doc_type=doc_type,
            source=source,
            source_url=source_url,
            content=content,
        )
        self.db.add(doc)
        await self.db.flush()

        chunks_count = await self.index_document(doc.id, content)

        return {
            "document_id": doc.id,
            "chunks_count": chunks_count,
            "title": title,
        }

    # ═══════════════════════════════════════════════════
    # BUSCA SEMÂNTICA
    # ═══════════════════════════════════════════════════

    async def search(
        self,
        query: str,
        limit: int = 5,
        client_id: str | None = None,
        doc_type: str | None = None,
        similarity_threshold: float = 0.3,
    ) -> list[dict]:
        """
        Busca semântica por similaridade de coseno.

        Args:
            query: Texto de busca em linguagem natural
            limit: Máximo de resultados
            client_id: Filtrar por cliente
            doc_type: Filtrar por tipo de documento
            similarity_threshold: Mínimo de similaridade (0-1)

        Returns:
            Lista de chunks relevantes com score de similaridade
        """
        # Vetoriza query (input_type='query' → Voyage usa prompt otimizado pra busca)
        query_embeddings = await self._embed_texts([query], input_type="query")
        if not query_embeddings:
            return []
        query_vector = query_embeddings[0]
        query_vec_str = "[" + ",".join(str(v) for v in query_vector) + "]"

        # Busca via pgvector (operador <=> = distância coseno; 0=idêntico, 2=oposto)
        # Similaridade = 1 - distância
        sql = """
            SELECT
                ke.id,
                ke.document_id,
                ke.chunk_index,
                ke.chunk_text,
                kd.title,
                kd.doc_type,
                kd.client_id,
                kd.source,
                1 - (ke.embedding <=> CAST(:query_vec AS vector)) AS similarity
            FROM knowledge_embeddings ke
            JOIN knowledge_documents kd ON ke.document_id = kd.id
            WHERE 1 - (ke.embedding <=> CAST(:query_vec AS vector)) > :threshold
        """

        params: dict = {
            "query_vec": query_vec_str,
            "threshold": similarity_threshold,
        }

        if client_id:
            sql += " AND (kd.client_id = :client_id OR kd.client_id IS NULL)"
            params["client_id"] = client_id

        if doc_type:
            sql += " AND kd.doc_type = :doc_type"
            params["doc_type"] = doc_type

        sql += " ORDER BY similarity DESC LIMIT :limit"
        params["limit"] = limit

        result = await self.db.execute(text(sql).bindparams(**params))
        rows = result.fetchall()

        return [
            {
                "id": row[0],
                "document_id": row[1],
                "chunk_index": row[2],
                "text": row[3],
                "title": row[4],
                "doc_type": row[5],
                "client_id": row[6],
                "source": row[7],
                "score": round(float(row[8]), 4),
            }
            for row in rows
        ]

    # ═══════════════════════════════════════════════════
    # CHUNKING
    # ═══════════════════════════════════════════════════

    def _split_into_chunks(
        self,
        full_text: str,
        chunk_size: int,
        overlap: int,
    ) -> list[str]:
        """
        Divide texto em chunks com sobreposição.

        Tenta quebrar em fronteiras naturais (parágrafos, frases)
        ao invés de cortar no meio de palavras.
        """
        if len(full_text) <= chunk_size:
            return [full_text.strip()] if full_text.strip() else []

        chunks: list[str] = []
        paragraphs = full_text.split("\n\n")

        current_chunk = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current_chunk) + len(para) + 2 <= chunk_size:
                current_chunk = f"{current_chunk}\n\n{para}".strip()
            else:
                if current_chunk:
                    chunks.append(current_chunk)

                if len(para) > chunk_size:
                    sentence_chunks = self._split_by_sentences(para, chunk_size, overlap)
                    chunks.extend(sentence_chunks)
                    current_chunk = ""
                else:
                    if chunks and overlap > 0:
                        last_chunk = chunks[-1]
                        overlap_text = (
                            last_chunk[-overlap:] if len(last_chunk) > overlap else last_chunk
                        )
                        space_idx = overlap_text.find(" ")
                        if space_idx > 0:
                            overlap_text = overlap_text[space_idx + 1 :]
                        current_chunk = f"{overlap_text}\n\n{para}".strip()
                    else:
                        current_chunk = para

        if current_chunk:
            chunks.append(current_chunk)

        return [c for c in chunks if c.strip()]

    def _split_by_sentences(
        self,
        full_text: str,
        chunk_size: int,
        overlap: int,
    ) -> list[str]:
        """Divide texto grande por frases quando parágrafos são maiores que chunk_size."""
        import re

        sentences = re.split(r"(?<=[.!?])\s+", full_text)

        chunks: list[str] = []
        current = ""

        for sent in sentences:
            if len(current) + len(sent) + 1 <= chunk_size:
                current = f"{current} {sent}".strip()
            else:
                if current:
                    chunks.append(current)
                current = sent

        if current:
            chunks.append(current)

        return chunks

    # ═══════════════════════════════════════════════════
    # GERAÇÃO DE EMBEDDINGS VIA VOYAGE AI
    # ═══════════════════════════════════════════════════

    async def _embed_texts(
        self,
        texts: Sequence[str],
        input_type: str = "document",
    ) -> list[list[float]]:
        """
        Gera embeddings para uma lista de textos via Voyage AI.

        Args:
            texts: Lista de textos a vetorizar
            input_type: 'document' ao indexar, 'query' ao buscar.
                        Voyage usa prompts internos diferentes pra otimizar cada caso.

        Returns:
            Lista de vetores (cada vetor é lista de floats com dim=voyage_dimension)
        """
        if not texts:
            return []

        client = _VoyageClientHolder.get()
        batch_size = settings.voyage_batch_size
        all_embeddings: list[list[float]] = []

        # Processa em batches (Voyage aceita até 1000 itens/request)
        import asyncio

        loop = asyncio.get_event_loop()

        for i in range(0, len(texts), batch_size):
            batch = list(texts[i : i + batch_size])

            # SDK Voyage é síncrono — rodar em executor pra não bloquear o loop
            result = await loop.run_in_executor(
                None,
                lambda b=batch: client.embed(
                    texts=b,
                    model=settings.voyage_model,
                    input_type=input_type,
                    output_dimension=settings.voyage_dimension,
                    truncation=True,
                ),
            )
            all_embeddings.extend(result.embeddings)
            logger.debug(
                "Voyage embed | batch=%d/%d | tokens=%d",
                i // batch_size + 1,
                (len(texts) + batch_size - 1) // batch_size,
                result.total_tokens,
            )

        return all_embeddings

    # ═══════════════════════════════════════════════════
    # MANUTENÇÃO
    # ═══════════════════════════════════════════════════

    async def delete_document_embeddings(self, document_id: str) -> int:
        """Remove todos os embeddings de um documento."""
        result = await self.db.execute(
            delete(KnowledgeEmbedding).where(KnowledgeEmbedding.document_id == document_id)
        )
        await self.db.flush()
        return result.rowcount or 0

    async def get_stats(self) -> dict:
        """Estatísticas do índice de embeddings."""
        docs_result = await self.db.execute(text("SELECT COUNT(*) FROM knowledge_documents"))
        emb_result = await self.db.execute(text("SELECT COUNT(*) FROM knowledge_embeddings"))

        return {
            "total_documents": docs_result.scalar() or 0,
            "total_embeddings": emb_result.scalar() or 0,
            "embedding_dimensions": EMBEDDING_DIMENSION,
            "embedding_model": settings.voyage_model,
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
        }
