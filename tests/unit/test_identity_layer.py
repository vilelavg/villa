"""
Testes do Identity Layer (P1.D).

Cobre o comportamento de _prepend_identity() em BaseModule:
1. Arquivo existe → prefixo aplicado ao prompt do modulo
2. Arquivo nao existe → retorna prompt original sem prefixo (defensivo)
3. Cache: arquivo lido uma vez, reusado em chamadas subsequentes
4. Cache reset entre testes via fixture autouse
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from core.models import ModuleCode
from modules import base as base_module
from modules.base import BaseModule


class _FakeModule(BaseModule):
    code = ModuleCode.M01_ROTEIROS
    name = "Fake"
    description = "Modulo de teste"

    async def execute(self, message, db, user=None, client_slug=None, context=None):
        return {"success": True}

    async def can_handle(self, message, context=None):
        return 0.0


@pytest.fixture(autouse=True)
def reset_identity_cache():
    """Cache de identidade e global; resetar entre testes garante isolamento."""
    base_module._identity_cache = None
    yield
    base_module._identity_cache = None


@pytest.fixture
def fake_module():
    return _FakeModule()


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 1: arquivo existe → prefixo aplicado
# ─────────────────────────────────────────────────────────────────────────────


class TestIdentityPrependedWhenFileExists:
    def test_identity_is_prepended(self, fake_module, tmp_path: Path):
        identity_content = "# Villa — Identidade WebXP\nEmpresa de performance odontologica."
        identity_file = tmp_path / "identity.md"
        identity_file.write_text(identity_content, encoding="utf-8")

        with patch.object(base_module, "IDENTITY_FILE_PATH", str(identity_file)):
            result = fake_module._prepend_identity("Voce eh o modulo M01.")

        assert identity_content in result
        assert "Voce eh o modulo M01." in result
        assert result.startswith(identity_content)
        assert "---" in result  # separador entre identity e module prompt

    def test_separator_between_identity_and_module_prompt(self, fake_module, tmp_path: Path):
        identity_file = tmp_path / "identity.md"
        identity_file.write_text("IDENTITY", encoding="utf-8")

        with patch.object(base_module, "IDENTITY_FILE_PATH", str(identity_file)):
            result = fake_module._prepend_identity("MODULE_PROMPT")

        # Garantir que existe separacao clara
        idx_identity = result.index("IDENTITY")
        idx_separator = result.index("---")
        idx_module = result.index("MODULE_PROMPT")
        assert idx_identity < idx_separator < idx_module


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 2: arquivo nao existe → retorna prompt original
# ─────────────────────────────────────────────────────────────────────────────


class TestIdentityAbsentReturnsOriginal:
    def test_missing_file_returns_module_prompt_unchanged(self, fake_module):
        nonexistent = "/tmp/does_not_exist_villa_identity_xyz.md"
        assert not os.path.exists(nonexistent)

        with patch.object(base_module, "IDENTITY_FILE_PATH", nonexistent):
            result = fake_module._prepend_identity("ORIGINAL_PROMPT")

        assert result == "ORIGINAL_PROMPT"

    def test_empty_identity_file_returns_module_prompt_unchanged(self, fake_module, tmp_path: Path):
        identity_file = tmp_path / "identity.md"
        identity_file.write_text("   \n\t\n   ", encoding="utf-8")  # whitespace only

        with patch.object(base_module, "IDENTITY_FILE_PATH", str(identity_file)):
            result = fake_module._prepend_identity("ORIGINAL_PROMPT")

        # Whitespace-only → strip() vira "" → cache = "" → sem prefixo
        assert result == "ORIGINAL_PROMPT"

    def test_io_error_returns_module_prompt_unchanged(self, fake_module, tmp_path):
        """Permission error simulada → exception capturada → sem prefixo."""
        identity_file = tmp_path / "identity.md"
        identity_file.write_text("IDENTITY", encoding="utf-8")

        with patch.object(base_module, "IDENTITY_FILE_PATH", str(identity_file)):
            # Simular erro ao abrir arquivo
            with patch("builtins.open", side_effect=PermissionError("denied")):
                result = fake_module._prepend_identity("ORIGINAL_PROMPT")

        assert result == "ORIGINAL_PROMPT"


# ─────────────────────────────────────────────────────────────────────────────
# Cenario 3: cache funciona — arquivo lido apenas 1x
# ─────────────────────────────────────────────────────────────────────────────


class TestIdentityCaching:
    def test_file_is_read_only_once(self, fake_module, tmp_path: Path):
        identity_file = tmp_path / "identity.md"
        identity_file.write_text("CACHED_IDENTITY", encoding="utf-8")

        with patch.object(base_module, "IDENTITY_FILE_PATH", str(identity_file)):
            real_open = open
            call_count = {"opens": 0}

            def counting_open(path, *args, **kwargs):
                if str(path) == str(identity_file):
                    call_count["opens"] += 1
                return real_open(path, *args, **kwargs)

            with patch("builtins.open", side_effect=counting_open):
                fake_module._prepend_identity("P1")
                fake_module._prepend_identity("P2")
                fake_module._prepend_identity("P3")

        # Mesmo com 3 chamadas, arquivo foi aberto so 1x
        assert call_count["opens"] == 1
