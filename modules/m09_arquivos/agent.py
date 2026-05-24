"""
Villa — Módulo M09: Gestão de Arquivos
Organização inteligente do Google Drive por cliente.
Busca, versionamento, indexação para a base de conhecimento.
"""


from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.models import Client, ModuleCode, User
from integrations.google_drive import google_drive
from memory.feedback_loop import FeedbackLoop
from memory.knowledge_base import KnowledgeBaseService
from modules.base import BaseModule

SEARCH_PROMPT = """Interprete o que o usuário está buscando e gere termos de busca.

COMANDO: "{message}"
CLIENTE: {client_name}

Responda em JSON:
{{
    "search_query": "termos para buscar no Drive",
    "file_type": "pdf|docx|xlsx|image|video|any",
    "context": "o que o usuário provavelmente precisa"
}}
"""


class M09Arquivos(BaseModule):
    code = ModuleCode.M09_ARQUIVOS
    name = "Gestão de Arquivos"
    description = "Organiza Google Drive por cliente, busca arquivos inteligente, indexa documentos na base de conhecimento."

    KEYWORDS = ["arquivo", "arquivos", "documento", "documentos", "drive", "pasta", "upload", "download", "buscar arquivo", "onde está", "onde tá", "briefing", "criativo"]

    async def can_handle(self, message: str, context: dict | None = None) -> float:
        msg_lower = message.lower()
        matches = sum(1 for kw in self.KEYWORDS if kw in msg_lower)
        if matches >= 2: return 0.8
        if matches >= 1: return 0.5
        return 0.0

    async def execute(self, message: str, db: AsyncSession, user: User | None = None, client_slug: str | None = None, context: dict | None = None) -> dict:
        feedback_loop = FeedbackLoop(db)

        # Interpretar o que o usuário quer
        parsed = await self.claude.extract_json(
            message=SEARCH_PROMPT.format(
                message=message,
                client_name=client_slug or "geral",
            ),
            model="fast",
        )
        search_data = parsed.get("data", {})
        query = search_data.get("search_query", message)

        # Buscar no Drive
        client = None
        folder_id = None
        if client_slug:
            result = await db.execute(select(Client).where(Client.slug == client_slug))
            client = result.scalar_one_or_none()
            if client:
                folder_id = (client.config or {}).get("drive_folder_id")

        try:
            files = await google_drive.search_files(
                query=query,
                folder_id=folder_id,
                limit=10,
            )
        except Exception as e:
            return {"success": False, "message": f"Erro ao buscar no Drive: {str(e)}", "actions_taken": ["drive_search_failed"]}

        if not files:
            return {
                "success": True,
                "message": f"Nenhum arquivo encontrado para '{query}'" + (f" na pasta de {client.name}" if client else ""),
                "actions_taken": ["search_empty"],
            }

        # Formatar resultado
        lines = [f"📁 **Resultados para '{query}':**\n"]
        for i, f in enumerate(files[:5], 1):
            size = int(f.get("size", 0))
            size_str = f"{size/1024:.0f}KB" if size < 1048576 else f"{size/1048576:.1f}MB"
            lines.append(f"{i}. **{f['name']}** ({size_str})")
            if f.get("webViewLink"):
                lines.append(f"   {f['webViewLink']}")

        await feedback_loop.record_decision(
            module=self.code, action="buscar_arquivo",
            input_data={"query": query, "client": client_slug},
            output_data={"files_found": len(files)},
            client_slug=client_slug,
        )

        return {
            "success": True,
            "message": "\n".join(lines),
            "data": {"files": files[:5], "total_found": len(files)},
            "actions_taken": ["drive_search_complete"],
        }

    async def index_file_to_knowledge(self, db: AsyncSession, file_id: str, title: str, doc_type: str, client_slug: str | None = None) -> dict:
        """Baixa arquivo do Drive e indexa na base de conhecimento."""
        kb = KnowledgeBaseService(db)

        try:
            content_bytes = await google_drive.get_file_content(file_id)
            content_text = content_bytes.decode("utf-8", errors="ignore")
        except Exception as e:
            return {"success": False, "error": str(e)}

        result = await kb.add_document(
            title=title,
            content=content_text,
            doc_type=doc_type,
            client_slug=client_slug,
            source="drive",
            source_url=f"https://drive.google.com/file/d/{file_id}",
        )

        return {"success": True, **result}
