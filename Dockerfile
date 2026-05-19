# ═══════════════════════════════════════════════════════════════
# VILLA — Dockerfile
# Imagem de produção otimizada: Python 3.12 slim
# ═══════════════════════════════════════════════════════════════

FROM python:3.12-slim AS base

# Metadados
LABEL maintainer="Vitor Vilela"
LABEL description="Villa — Agente SaaS WebXP"

# Variáveis de ambiente para Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Diretório de trabalho
WORKDIR /app

# ── Instalar dependências Python ──
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Copiar código da aplicação ──
COPY . .

# ── Criar usuário não-root para segurança ──
RUN groupadd -r villa && useradd -r -g villa -d /app -s /sbin/nologin villa \
    && chown -R villa:villa /app

USER villa

# ── Healthcheck ──
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ── Porta ──
EXPOSE 8000

# ── Comando de inicialização ──
CMD ["uvicorn", "core.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
