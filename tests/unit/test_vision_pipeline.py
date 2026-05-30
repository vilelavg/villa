"""
Testes do Vision Pipeline (P1.B).

Cobre:
1. VisionContext — defaults e has_images
2. build_image_blocks — encoding correto, filtro de mime types
3. fetch_creatives — busca por folder_id quando disponivel
4. fetch_creatives — fallback para busca por nome do cliente
5. fetch_creatives — defensivo: Drive falha, retorna ctx vazio
6. fetch_creatives — filtro de tamanho (>5MB pulado)
7. fetch_creatives — filtro de MIME type
8. fetch_creatives — limite max_images respeitado
"""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock

import pytest

from integrations.vision_pipeline import (
    DEFAULT_MAX_IMAGES,
    MAX_IMAGE_BYTES,
    SUPPORTED_MIME_TYPES,
    VisionContext,
    VisionPipeline,
)


pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# VisionContext
# ─────────────────────────────────────────────────────────────────────────────

class TestVisionContext:
    def test_defaults_vazios(self):
        ctx = VisionContext()
        assert ctx.image_blocks == []
        assert ctx.creatives_found == 0
        assert ctx.error is None
        assert ctx.has_images is False

    def test_has_images_true_quando_tem_blocks(self):
        ctx = VisionContext(
            image_blocks=[{"base64_data": "abc", "media_type": "image/jpeg"}],
            creatives_found=1,
        )
        assert ctx.has_images is True


# ─────────────────────────────────────────────────────────────────────────────
# build_image_blocks
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildImageBlocks:
    def test_encoding_base64_correto(self):
        content = b"fake-image-bytes"
        blocks = VisionPipeline.build_image_blocks([(content, "image/jpeg")])
        assert len(blocks) == 1
        assert blocks[0]["media_type"] == "image/jpeg"
        decoded = base64.standard_b64decode(blocks[0]["base64_data"])
        assert decoded == content

    def test_filtra_mime_nao_suportado(self):
        blocks = VisionPipeline.build_image_blocks([
            (b"abc", "image/jpeg"),
            (b"def", "application/pdf"),  # nao suportado
            (b"ghi", "image/png"),
        ])
        assert len(blocks) == 2
        assert blocks[0]["media_type"] == "image/jpeg"
        assert blocks[1]["media_type"] == "image/png"

    def test_lista_vazia_retorna_vazio(self):
        assert VisionPipeline.build_image_blocks([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# fetch_creatives — caminho feliz
# ─────────────────────────────────────────────────────────────────────────────

def _make_drive_mock(search_result=None, content_map=None):
    drive = MagicMock()
    drive.search_files = AsyncMock(return_value=search_result or [])

    async def _get_content(file_id):
        return (content_map or {}).get(file_id, b"")

    drive.get_file_content = AsyncMock(side_effect=_get_content)
    return drive


def _make_client(slug="ottoboni", name="Ottoboni", folder_id=None):
    client = MagicMock()
    client.slug = slug
    client.name = name
    client.drive_folder_id = folder_id
    return client


class TestFetchCreativesHappyPath:
    async def test_busca_por_folder_id(self):
        files = [
            {"id": "f1", "name": "criativo1.jpg", "mimeType": "image/jpeg"},
            {"id": "f2", "name": "criativo2.png", "mimeType": "image/png"},
        ]
        drive = _make_drive_mock(
            search_result=files,
            content_map={"f1": b"jpg-bytes", "f2": b"png-bytes"},
        )
        pipeline = VisionPipeline(drive_client=drive)
        client = _make_client(folder_id="folder123")

        ctx = await pipeline.fetch_creatives(client, db=MagicMock(), max_images=3)

        assert ctx.creatives_found == 2
        assert ctx.has_images is True
        assert len(ctx.image_blocks) == 2
        # Confirma que buscou na pasta correta
        drive.search_files.assert_called()
        call_kwargs = drive.search_files.call_args.kwargs
        assert call_kwargs.get("folder_id") == "folder123"

    async def test_fallback_busca_por_nome_sem_folder(self):
        files = [{"id": "f1", "name": "Ottoboni criativo.jpg", "mimeType": "image/jpeg"}]
        drive = _make_drive_mock(
            search_result=files,
            content_map={"f1": b"data"},
        )
        pipeline = VisionPipeline(drive_client=drive)
        client = _make_client(folder_id=None, name="Ottoboni")

        ctx = await pipeline.fetch_creatives(client, db=MagicMock())

        assert ctx.creatives_found == 1
        call_kwargs = drive.search_files.call_args.kwargs
        assert call_kwargs.get("query") == "Ottoboni"
        assert call_kwargs.get("folder_id") is None


# ─────────────────────────────────────────────────────────────────────────────
# fetch_creatives — defensivo
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchCreativesDefensive:
    async def test_drive_falha_retorna_ctx_vazio(self):
        drive = MagicMock()
        drive.search_files = AsyncMock(side_effect=RuntimeError("drive down"))
        pipeline = VisionPipeline(drive_client=drive)

        ctx = await pipeline.fetch_creatives(
            _make_client(folder_id="x"), db=MagicMock(),
        )

        # Falha no search_files e absorvida internamente (fail-safe por design).
        # Pipeline retorna contexto vazio sem imagens ? comportamento correto.
        assert ctx.has_images is False
        assert ctx.creatives_found == 0

    async def test_sem_arquivos_retorna_ctx_vazio_sem_erro(self):
        drive = _make_drive_mock(search_result=[])
        pipeline = VisionPipeline(drive_client=drive)

        ctx = await pipeline.fetch_creatives(_make_client(folder_id="x"), db=MagicMock())

        assert ctx.creatives_found == 0
        assert ctx.error is None

    async def test_download_falha_em_um_arquivo_continua_outros(self):
        files = [
            {"id": "f1", "name": "ok.jpg", "mimeType": "image/jpeg"},
            {"id": "f2", "name": "broken.jpg", "mimeType": "image/jpeg"},
            {"id": "f3", "name": "ok2.jpg", "mimeType": "image/jpeg"},
        ]

        async def _get_content(file_id):
            if file_id == "f2":
                raise RuntimeError("download falhou")
            return b"data"

        drive = MagicMock()
        drive.search_files = AsyncMock(return_value=files)
        drive.get_file_content = AsyncMock(side_effect=_get_content)

        pipeline = VisionPipeline(drive_client=drive)
        ctx = await pipeline.fetch_creatives(
            _make_client(folder_id="x"), db=MagicMock(), max_images=3,
        )
        # Os outros 2 arquivos foram baixados com sucesso
        assert ctx.creatives_found == 2

    async def test_filtra_arquivos_muito_grandes(self):
        big = b"x" * (MAX_IMAGE_BYTES + 1)
        small = b"y" * 100
        files = [
            {"id": "big", "name": "big.jpg", "mimeType": "image/jpeg"},
            {"id": "small", "name": "small.jpg", "mimeType": "image/jpeg"},
        ]
        drive = _make_drive_mock(
            search_result=files,
            content_map={"big": big, "small": small},
        )
        pipeline = VisionPipeline(drive_client=drive)

        ctx = await pipeline.fetch_creatives(
            _make_client(folder_id="x"), db=MagicMock(), max_images=3,
        )

        # So o pequeno deve passar
        assert ctx.creatives_found == 1

    async def test_filtra_mime_nao_suportado(self):
        files = [
            {"id": "f1", "name": "doc.pdf", "mimeType": "application/pdf"},
            {"id": "f2", "name": "img.jpg", "mimeType": "image/jpeg"},
        ]
        drive = _make_drive_mock(
            search_result=files,
            content_map={"f2": b"data"},
        )
        pipeline = VisionPipeline(drive_client=drive)

        ctx = await pipeline.fetch_creatives(
            _make_client(folder_id="x"), db=MagicMock(), max_images=3,
        )

        # Apenas o jpeg
        assert ctx.creatives_found == 1
        assert ctx.image_blocks[0]["media_type"] == "image/jpeg"

    async def test_max_images_respeitado(self):
        files = [
            {"id": f"f{i}", "name": f"c{i}.jpg", "mimeType": "image/jpeg"}
            for i in range(10)
        ]
        drive = _make_drive_mock(
            search_result=files,
            content_map={f"f{i}": b"data" for i in range(10)},
        )
        pipeline = VisionPipeline(drive_client=drive)

        ctx = await pipeline.fetch_creatives(
            _make_client(folder_id="x"), db=MagicMock(), max_images=2,
        )

        assert ctx.creatives_found == 2


# ─────────────────────────────────────────────────────────────────────────────
# Constantes
# ─────────────────────────────────────────────────────────────────────────────

class TestConstants:
    def test_supported_mime_types(self):
        assert "image/jpeg" in SUPPORTED_MIME_TYPES
        assert "image/png" in SUPPORTED_MIME_TYPES
        assert "image/gif" in SUPPORTED_MIME_TYPES
        assert "image/webp" in SUPPORTED_MIME_TYPES
        assert "application/pdf" not in SUPPORTED_MIME_TYPES

    def test_default_max_images(self):
        assert DEFAULT_MAX_IMAGES == 3

    def test_max_image_bytes_5mb(self):
        assert MAX_IMAGE_BYTES == 5 * 1024 * 1024
