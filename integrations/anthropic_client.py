"""
Villa — Cliente Anthropic (Claude API)
Wrapper para chamadas ao Claude Sonnet 4 (raciocínio) e Haiku (triagem rápida).
Cérebro central do Villa — todos os módulos usam este cliente.
"""

import time
from collections.abc import AsyncGenerator

from anthropic import AsyncAnthropic

from core.config import settings


class AnthropicClient:
    """
    Wrapper async para a API do Claude.

    Uso:
        client = AnthropicClient()

        # Chamada simples
        response = await client.ask("Analise esta campanha: ...")

        # Com system prompt e modelo específico
        response = await client.ask(
            message="Gera um roteiro para implantes",
            system="Você é o Villa, agente da WebXP...",
            model="primary",
        )

        # Streaming
        async for chunk in client.stream("Explique o relatório"):
            print(chunk, end="")
    """

    def __init__(self):
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model_primary = settings.anthropic_model_primary  # Sonnet 4
        self.model_fast = settings.anthropic_model_fast  # Haiku

    def _get_model(self, model: str = "primary") -> str:
        """Resolve nome do modelo."""
        if model == "primary" or model == "sonnet":
            return self.model_primary
        if model == "fast" or model == "haiku":
            return self.model_fast
        return model  # Permite passar model string diretamente

    async def ask(
        self,
        message: str,
        system: str | None = None,
        model: str = "primary",
        max_tokens: int | None = None,
        temperature: float | None = None,
        conversation: list[dict] | None = None,
    ) -> dict:
        """
        Envia uma mensagem ao Claude e retorna a resposta completa.

        Args:
            message: Mensagem do usuário
            system: System prompt (personalidade do Villa)
            model: "primary" (Sonnet) ou "fast" (Haiku)
            max_tokens: Limite de tokens na resposta
            temperature: Criatividade (0.0 = preciso, 1.0 = criativo)
            conversation: Histórico de mensagens [{role, content}]

        Returns:
            dict com: text, model, tokens_input, tokens_output, cost_usd, duration_ms
        """
        start = time.time()

        # Montar mensagens
        messages = []
        if conversation:
            messages.extend(conversation)
        messages.append({"role": "user", "content": message})

        # Chamar API
        kwargs = {
            "model": self._get_model(model),
            "max_tokens": max_tokens or settings.anthropic_max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        else:
            kwargs["temperature"] = settings.anthropic_temperature

        response = await self._client.messages.create(**kwargs)

        duration_ms = int((time.time() - start) * 1000)

        # Extrair texto
        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        # Calcular custo estimado (preços aproximados maio/2026)
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = self._estimate_cost(self._get_model(model), tokens_in, tokens_out)

        return {
            "text": text,
            "model": response.model,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "stop_reason": response.stop_reason,
        }

    async def ask_with_images(
        self,
        message: str,
        images: list[dict],
        system: str | None = None,
        model: str = "primary",
        max_tokens: int | None = None,
        temperature: float | None = None,
        conversation: list[dict] | None = None,
    ) -> dict:
        """
        Envia mensagem com imagens ao Claude (Vision API).

        Mesma assinatura do ask() + parametro images. Existe como metodo
        paralelo (nao altera ask) para garantir zero regressao.

        Args:
            message: Texto da pergunta
            images: Lista de dicts no formato:
                [{"base64_data": "<b64>", "media_type": "image/jpeg"}, ...]
                media_type aceito: image/jpeg, image/png, image/gif, image/webp
            system: System prompt
            model: "primary" (Sonnet) ou "fast" (Haiku) — ambos suportam vision
            max_tokens: Limite de tokens na resposta
            temperature: Criatividade (0.0 a 1.0)
            conversation: Historico de mensagens [{role, content}]

        Returns:
            Mesma estrutura do ask(): text, model, tokens_input/output, cost_usd, etc.
        """
        start = time.time()

        # Montar content blocks: imagens primeiro, texto depois (recomendado pela Anthropic)
        content_blocks: list[dict] = []
        for img in images:
            content_blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img["media_type"],
                    "data": img["base64_data"],
                },
            })
        content_blocks.append({"type": "text", "text": message})

        messages = []
        if conversation:
            messages.extend(conversation)
        messages.append({"role": "user", "content": content_blocks})

        kwargs = {
            "model": self._get_model(model),
            "max_tokens": max_tokens or settings.anthropic_max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        else:
            kwargs["temperature"] = settings.anthropic_temperature

        response = await self._client.messages.create(**kwargs)
        duration_ms = int((time.time() - start) * 1000)

        text = ""
        for block in response.content:
            if block.type == "text":
                text += block.text

        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = self._estimate_cost(self._get_model(model), tokens_in, tokens_out)

        return {
            "text": text,
            "model": response.model,
            "tokens_input": tokens_in,
            "tokens_output": tokens_out,
            "cost_usd": cost,
            "duration_ms": duration_ms,
            "stop_reason": response.stop_reason,
            "images_sent": len(images),
        }

    async def stream(
        self,
        message: str,
        system: str | None = None,
        model: str = "primary",
        max_tokens: int | None = None,
        temperature: float | None = None,
        conversation: list[dict] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming de resposta do Claude. Retorna chunks de texto.

        Uso:
            async for chunk in client.stream("Gera roteiro"):
                print(chunk, end="")
        """
        messages = []
        if conversation:
            messages.extend(conversation)
        messages.append({"role": "user", "content": message})

        kwargs = {
            "model": self._get_model(model),
            "max_tokens": max_tokens or settings.anthropic_max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system
        if temperature is not None:
            kwargs["temperature"] = temperature
        else:
            kwargs["temperature"] = settings.anthropic_temperature

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text

    async def classify(
        self,
        text: str,
        categories: list[str],
        system: str | None = None,
    ) -> dict:
        """
        Classificação rápida via Haiku.
        Recebe texto e lista de categorias, retorna a melhor categoria.

        Uso:
            result = await client.classify(
                text="Quero agendar uma consulta",
                categories=["agendamento", "duvida", "reclamacao", "compra"],
            )
            print(result["category"])  # "agendamento"
        """
        categories_str = ", ".join(categories)
        prompt = (
            f"Classifique o seguinte texto em UMA das categorias: {categories_str}\n\n"
            f'Texto: "{text}"\n\n'
            f"Responda APENAS com o nome da categoria, sem explicação."
        )

        response = await self.ask(
            message=prompt,
            system=system or "Você é um classificador preciso. Responda apenas com a categoria.",
            model="fast",
            max_tokens=50,
            temperature=0.0,
        )

        category = response["text"].strip().lower()
        # Garantir que a resposta é uma das categorias válidas
        for cat in categories:
            if cat.lower() in category:
                return {"category": cat, **response}

        return {"category": category, **response}

    async def extract_json(
        self,
        message: str,
        system: str | None = None,
        model: str = "primary",
    ) -> dict:
        """
        Pede ao Claude para retornar JSON estruturado.
        Faz parse automático da resposta.

        Uso:
            data = await client.extract_json(
                message="Analise este lead e retorne score, motivo e próxima ação",
                system="Retorne APENAS um JSON válido, sem markdown."
            )
        """
        import json

        if not system:
            system = (
                "Retorne APENAS um JSON válido, sem markdown, sem blocos de código, sem explicação."
            )

        response = await self.ask(
            message=message,
            system=system,
            model=model,
            temperature=0.1,
        )

        text = response["text"].strip()
        # Limpar possíveis blocos de código markdown
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0]

        try:
            parsed = json.loads(text)
            return {"data": parsed, **response}
        except json.JSONDecodeError:
            return {"data": None, "parse_error": True, "raw_text": text, **response}

    def _estimate_cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """Estima custo em USD baseado nos preços por token."""
        # Preços aproximados (verificar anthropic.com/pricing para atuais)
        prices = {
            "claude-sonnet-4-20250514": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
            "claude-haiku-4-5-20251001": {"input": 0.80 / 1_000_000, "output": 4.0 / 1_000_000},
        }
        p = prices.get(model, prices.get("claude-sonnet-4-20250514"))
        return round(tokens_in * p["input"] + tokens_out * p["output"], 6)

    async def close(self):
        """Fecha o cliente HTTP."""
        await self._client.close()


# Instância global
claude = AnthropicClient()
