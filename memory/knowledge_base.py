"""
Villa — Base de Conhecimento (RAG)
Memória institucional da WebXP.

Indexa e permite consultar:
    - Transcrições de reuniões (Tactiq)
    - Roteiros aprovados e seus resultados
    - Relatórios históricos
    - Mapeamentos estratégicos de clientes
    - FAQs por especialidade
    - Decisões importantes documentadas
    - Processos operacionais

O Villa consulta essa base antes de responder perguntas como:
    "Qual foi o melhor criativo de implante?"
    "Como tratamos o caso do Ottoboni?"
    "Qual a abordagem aprovada para lentes de contato?"
    "Quando foi a última reunião com o Linardi?"
"""


from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, KnowledgeDocument
from integrations.anthropic_client import claude
from memory.embeddings import EmbeddingService


class KnowledgeBaseService:
    """
    Sistema RAG (Retrieval-Augmented Generation) do Villa.
    
    Pipeline de consulta:
        1. Usuário pergunta algo
        2. Pergunta é vetorizada
        3. Busca semântica encontra chunks relevantes
        4. Chunks são injetados no prompt do Claude como contexto
        5. Claude responde usando a base de conhecimento como referência
    
    Uso:
        kb = KnowledgeBaseService(db)
        
        # Indexar documento
        await kb.add_document(
            title="Reunião Ottoboni — Jan/2026",
            content="Transcrição da reunião...",
            doc_type="transcricao",
            client_slug="ottoboni",
            source="tactiq",
        )
        
        # Consultar
        answer = await kb.ask(
            "Qual foi a estratégia definida pro Ottoboni?",
            client_slug="ottoboni",
        )
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.embeddings = EmbeddingService(db)

    # ═══════════════════════════════════════════════════
    # INGESTÃO DE DOCUMENTOS
    # ═══════════════════════════════════════════════════

    async def add_document(
        self,
        title: str,
        content: str,
        doc_type: str,
        client_slug: str | None = None,
        source: str | None = None,
        source_url: str | None = None,
    ) -> dict:
        """
        Adiciona e indexa um documento na base de conhecimento.
        
        Args:
            title: Título do documento
            content: Conteúdo textual completo
            doc_type: Tipo — "transcricao" | "roteiro" | "relatorio" | "briefing" | "faq" | "processo"
            client_slug: Cliente relacionado (None se é geral)
            source: Origem — "tactiq" | "drive" | "manual" | "villa"
            source_url: URL de origem (se aplicável)
        """
        result = await self.embeddings.index_text(
            title=title,
            content=content,
            doc_type=doc_type,
            client_slug=client_slug,
            source=source,
            source_url=source_url,
        )

        return {
            "document_id": result["document_id"],
            "title": title,
            "doc_type": doc_type,
            "client": client_slug,
            "chunks_indexed": result["chunks_count"],
            "content_length": len(content),
        }

    async def add_roteiro_approved(
        self,
        client_slug: str,
        title: str,
        roteiro_text: str,
        performance_data: dict | None = None,
    ) -> dict:
        """
        Atalho: indexa um roteiro aprovado com seus dados de performance.
        Roteiros aprovados são referência para geração futura.
        """
        # Montar conteúdo enriquecido
        content = f"ROTEIRO APROVADO: {title}\n\n{roteiro_text}"
        if performance_data:
            metrics = ", ".join(f"{k}: {v}" for k, v in performance_data.items())
            content += f"\n\nPERFORMANCE: {metrics}"

        return await self.add_document(
            title=f"Roteiro aprovado — {title}",
            content=content,
            doc_type="roteiro",
            client_slug=client_slug,
            source="villa",
        )

    async def add_transcription(
        self,
        title: str,
        transcription: str,
        client_slug: str | None = None,
        meeting_date: str | None = None,
    ) -> dict:
        """Atalho: indexa transcrição de reunião (Tactiq)."""
        content = transcription
        if meeting_date:
            content = f"DATA DA REUNIÃO: {meeting_date}\n\n{content}"

        return await self.add_document(
            title=title,
            content=content,
            doc_type="transcricao",
            client_slug=client_slug,
            source="tactiq",
        )

    async def add_faq(
        self,
        question: str,
        answer: str,
        client_slug: str | None = None,
        specialty: str | None = None,
    ) -> dict:
        """Atalho: indexa par pergunta/resposta para FAQ."""
        content = f"PERGUNTA: {question}\n\nRESPOSTA: {answer}"
        if specialty:
            content = f"ESPECIALIDADE: {specialty}\n\n{content}"

        return await self.add_document(
            title=f"FAQ — {question[:100]}",
            content=content,
            doc_type="faq",
            client_slug=client_slug,
            source="manual",
        )

    # ═══════════════════════════════════════════════════
    # BUSCA
    # ═══════════════════════════════════════════════════

    async def search(
        self,
        query: str,
        limit: int = 5,
        client_slug: str | None = None,
        doc_type: str | None = None,
        min_score: float = 0.3,
    ) -> list[dict]:
        """
        Busca semântica na base de conhecimento.
        
        Args:
            query: Pergunta ou busca em linguagem natural
            limit: Máximo de resultados
            client_slug: Filtrar por cliente
            doc_type: Filtrar por tipo de documento
            min_score: Score mínimo de similaridade
            
        Returns:
            Lista de chunks relevantes ordenados por relevância
        """
        # Resolver client_id
        client_id = None
        if client_slug:
            result = await self.db.execute(
                select(Client.id).where(Client.slug == client_slug)
            )
            client_id = result.scalar_one_or_none()

        results = await self.embeddings.search(
            query=query,
            limit=limit,
            client_id=client_id,
            doc_type=doc_type,
            similarity_threshold=min_score,
        )

        return results

    async def ask(
        self,
        question: str,
        client_slug: str | None = None,
        doc_type: str | None = None,
        system_prompt_extra: str | None = None,
        max_context_chunks: int = 5,
    ) -> dict:
        """
        Pergunta algo à base de conhecimento usando RAG completo.
        
        Pipeline:
            1. Busca chunks relevantes via embeddings
            2. Monta contexto com os chunks encontrados
            3. Envia ao Claude com o contexto como referência
            4. Retorna resposta + fontes usadas
        
        Args:
            question: Pergunta em linguagem natural
            client_slug: Filtrar contexto por cliente
            doc_type: Filtrar por tipo de documento
            system_prompt_extra: Instruções adicionais para o Claude
            max_context_chunks: Máximo de chunks no contexto
            
        Returns:
            Dict com: answer, sources, chunks_used, tokens_used
        """
        # Buscar contexto relevante
        chunks = await self.search(
            query=question,
            limit=max_context_chunks,
            client_slug=client_slug,
            doc_type=doc_type,
        )

        if not chunks:
            return {
                "answer": (
                    "Não encontrei informações relevantes na base de conhecimento "
                    "para responder essa pergunta. A base pode precisar ser alimentada "
                    "com mais documentos sobre esse assunto."
                ),
                "sources": [],
                "chunks_used": 0,
                "has_context": False,
            }

        # Montar contexto para o prompt
        context_blocks = []
        sources = []
        for i, chunk in enumerate(chunks):
            context_blocks.append(
                f"[Fonte {i+1}: {chunk['title']} ({chunk['doc_type']}) — Relevância: {chunk['score']}]\n"
                f"{chunk['text']}"
            )
            sources.append({
                "title": chunk["title"],
                "doc_type": chunk["doc_type"],
                "score": chunk["score"],
                "document_id": chunk["document_id"],
            })

        context_text = "\n\n---\n\n".join(context_blocks)

        # System prompt
        system = (
            "Você é o Villa, agente da WebXP. Responda a pergunta usando EXCLUSIVAMENTE "
            "as informações fornecidas no contexto abaixo. Se a informação não estiver "
            "no contexto, diga que não tem essa informação na base.\n\n"
            "Cite a fonte quando usar uma informação específica (ex: 'De acordo com a "
            "reunião do Ottoboni...').\n\n"
            f"## CONTEXTO DA BASE DE CONHECIMENTO\n\n{context_text}"
        )

        if system_prompt_extra:
            system += f"\n\n## INSTRUÇÕES ADICIONAIS\n{system_prompt_extra}"

        # Perguntar ao Claude
        response = await claude.ask(
            message=question,
            system=system,
            model="primary",
        )

        return {
            "answer": response["text"],
            "sources": sources,
            "chunks_used": len(chunks),
            "has_context": True,
            "tokens_used": response["tokens_input"] + response["tokens_output"],
            "cost_usd": response["cost_usd"],
        }

    # ═══════════════════════════════════════════════════
    # GESTÃO
    # ═══════════════════════════════════════════════════

    async def list_documents(
        self,
        client_slug: str | None = None,
        doc_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Lista documentos na base de conhecimento."""
        query = select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).limit(limit)

        if client_slug:
            result = await self.db.execute(
                select(Client.id).where(Client.slug == client_slug)
            )
            client_id = result.scalar_one_or_none()
            if client_id:
                query = query.where(KnowledgeDocument.client_id == client_id)

        if doc_type:
            query = query.where(KnowledgeDocument.doc_type == doc_type)

        result = await self.db.execute(query)
        docs = result.scalars().all()

        return [
            {
                "id": d.id,
                "title": d.title,
                "doc_type": d.doc_type,
                "source": d.source,
                "client_id": d.client_id,
                "content_length": len(d.content) if d.content else 0,
                "chunks_count": len(d.chunks) if d.chunks else 0,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in docs
        ]

    async def delete_document(self, document_id: str) -> bool:
        """Remove um documento e seus embeddings."""
        # Embeddings são deletados via CASCADE
        result = await self.db.execute(
            delete(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        await self.db.flush()
        return (result.rowcount or 0) > 0

    async def get_stats(self) -> dict:
        """Estatísticas da base de conhecimento."""
        emb_stats = await self.embeddings.get_stats()

        # Contar por tipo
        type_result = await self.db.execute(
            text(
                "SELECT doc_type, COUNT(*) as count "
                "FROM knowledge_documents "
                "GROUP BY doc_type ORDER BY count DESC"
            )
        )
        by_type = {row[0]: row[1] for row in type_result.fetchall()}

        return {
            **emb_stats,
            "documents_by_type": by_type,
        }
