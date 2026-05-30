"""
Villa — Vision Pipeline (P1.B)

Conecta o Google Drive ao Claude Vision API. Permite que o Villa veja os
criativos antes de propor hipoteses, em vez de trabalhar cego.

Fluxo:
    1. fetch_creatives(client, db) busca arquivos de imagem no Drive
       - Se client.drive_folder_id existir, busca dentro da pasta
       - Caso contrario, busca pelo nome do cliente
       - Filtra apenas MIME types suportados pela Vision API
       - Baixa ate max_images (default 3) para controlar custo
    2. build_image_blocks(images_bytes, media_types) converte para
       content blocks no formato esperado pela Anthropic API
    3. M11 usa o VisionContext.image_blocks em ask_with_images()

Defensivo: qualquer falha retorna VisionContext vazio. O M11 cai no
comportamento atual (texto puro) sem erro. Nunca quebra o fluxo.

Custo: 3 imagens em Sonnet 4 ~ $0.003-0.006 adicionais por chamada M11.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# MIME types suportados pela Anthropic Vision API
SUPPORTED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

DEFAULT_MAX_IMAGES = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5MB — limite seguro da API


@dataclass
class VisionContext:
    """
    Resultado de uma busca de criativos. Sempre seguro de usar — se
    creatives_found=0, image_blocks fica vazio e o consumidor processa
    sem imagens normalmente.
    """

    image_blocks: list[dict] = field(default_factory=list)
    creatives_found: int = 0
    creatives_metadata: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def has_images(self) -> bool:
        return len(self.image_blocks) > 0


class VisionPipeline:
    """
    Busca e prepara criativos visuais do Drive para o Claude Vision.

    Uso:
        pipeline = VisionPipeline()
        ctx = await pipeline.fetch_creatives(client, db, max_images=3)
        if ctx.has_images:
            response = await claude.ask_with_images(
                message=prompt,
                images=ctx.image_blocks,
            )
    """

    def __init__(self, drive_client: Any | None = None):
        # Lazy import do google_drive para nao criar dependencia rigida
        # nos testes unitarios. None aqui = resolvido no primeiro uso.
        self._drive = drive_client

    async def fetch_creatives(
        self,
        client: Any,
        db: AsyncSession,
        max_images: int = DEFAULT_MAX_IMAGES,
    ) -> VisionContext:
        """
        Busca criativos do cliente no Drive e prepara content blocks.

        Args:
            client: instancia Client do modelo (precisa de .drive_folder_id e/ou .name)
            db: sessao SQLAlchemy (passada por compatibilidade, nao usada aqui)
            max_images: maximo de imagens a baixar (default 3)

        Returns:
            VisionContext — sempre seguro, mesmo em caso de erro.
        """
        ctx = VisionContext()

        try:
            drive = await self._ensure_drive_client()
        except Exception as e:
            ctx.error = f"drive_unavailable: {e}"
            logger.debug("VisionPipeline: drive indisponivel: %s", e)
            return ctx

        # Buscar arquivos no Drive
        try:
            files = await self._search_creatives(drive, client, max_images * 3)
        except Exception as e:
            ctx.error = f"search_failed: {e}"
            logger.debug("VisionPipeline: busca falhou para %s: %s",
                         getattr(client, "slug", "?"), e)
            return ctx

        if not files:
            return ctx

        # Baixar imagens (limita a max_images, defensivo por arquivo)
        downloaded: list[tuple[bytes, str, dict]] = []
        for f in files:
            if len(downloaded) >= max_images:
                break
            mime = f.get("mimeType")
            if mime not in SUPPORTED_MIME_TYPES:
                continue
            file_id = f.get("id")
            if not file_id:
                continue
            try:
                content = await drive.get_file_content(file_id)
            except Exception as e:
                logger.debug("VisionPipeline: download falhou para %s: %s", file_id, e)
                continue
            if not content or len(content) == 0:
                continue
            if len(content) > MAX_IMAGE_BYTES:
                logger.debug("VisionPipeline: pulando arquivo %s (>5MB)", file_id)
                continue
            downloaded.append((content, mime, f))

        if not downloaded:
            return ctx

        # Montar content blocks
        ctx.image_blocks = self.build_image_blocks(
            [(b, m) for (b, m, _) in downloaded]
        )
        ctx.creatives_found = len(downloaded)
        ctx.creatives_metadata = [
            {
                "id": f.get("id"),
                "name": f.get("name"),
                "mime_type": f.get("mimeType"),
                "modified_time": f.get("modifiedTime"),
            }
            for (_, _, f) in downloaded
        ]
        return ctx

    @staticmethod
    def build_image_blocks(
        images: list[tuple[bytes, str]],
    ) -> list[dict]:
        """
        Converte lista de (bytes, media_type) para o formato esperado
        pelo ask_with_images() do AnthropicClient.

        Args:
            images: lista de tuplas (bytes_do_arquivo, mime_type)

        Returns:
            Lista de dicts: [{"base64_data": "<b64>", "media_type": "image/jpeg"}, ...]
        """
        blocks: list[dict] = []
        for content, mime in images:
            if mime not in SUPPORTED_MIME_TYPES:
                continue
            try:
                b64 = base64.standard_b64encode(content).decode("utf-8")
            except Exception:
                continue
            blocks.append({
                "base64_data": b64,
                "media_type": mime,
            })
        return blocks

    # ── Internos ──

    async def _ensure_drive_client(self) -> Any:
        """Resolve o drive client com lazy import."""
        if self._drive is None:
            from integrations.google_drive import google_drive

            self._drive = google_drive
        return self._drive

    async def _search_creatives(
        self,
        drive: Any,
        client: Any,
        limit: int,
    ) -> list[dict]:
        """
        Estrategia de busca:
            1. Se client.drive_folder_id: lista arquivos da pasta (qualquer nome)
            2. Caso contrario: busca pelo client.name no Drive todo
        """
        folder_id = getattr(client, "drive_folder_id", None)
        client_name = getattr(client, "name", "") or ""

        # Estrategia 1: pasta dedicada
        if folder_id:
            # Busca arquivos de imagem dentro da pasta (qualquer nome)
            for mime in SUPPORTED_MIME_TYPES:
                try:
                    files = await drive.search_files(
                        query="",
                        folder_id=folder_id,
                        mime_type=mime,
                        limit=limit,
                    )
                    if files:
                        return files
                except Exception as e:
                    logger.debug("VisionPipeline: busca por mime %s falhou: %s", mime, e)
            # Sem mime filter
            try:
                return await drive.search_files(
                    query="",
                    folder_id=folder_id,
                    limit=limit,
                )
            except Exception as e:
                logger.debug("VisionPipeline: busca em folder %s falhou: %s", folder_id, e)
                return []

        # Estrategia 2: busca por nome do cliente
        if client_name:
            try:
                return await drive.search_files(
                    query=client_name,
                    limit=limit,
                )
            except Exception as e:
                logger.debug("VisionPipeline: busca por nome '%s' falhou: %s", client_name, e)

        return []


# ── Instancia global ──
vision_pipeline = VisionPipeline()


__all__ = [
    "VisionPipeline",
    "VisionContext",
    "vision_pipeline",
    "SUPPORTED_MIME_TYPES",
    "DEFAULT_MAX_IMAGES",
]
