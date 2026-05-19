# Villa — SaaS WebXP Agency

Sistema multi-agente modular que atua como funcionário sênior virtual da WebXP, agência de performance especializada em odontologia.

## O que o Villa faz

- **Gera roteiros** com tripla validação automática (gancho, corpo, CTA)
- **Monta relatórios** consolidando dados de Meta Ads, Google Ads, Kommo e InLead
- **Qualifica leads** via WhatsApp com handoff inteligente para humano
- **Analisa campanhas** e sugere otimizações baseadas em dados
- **Agenda consultas** integrando Google Calendar, Kommo e WhatsApp
- **Monitora métricas** e dispara alertas proativos antes de problemas
- **Aprende com resultados** via feedback loop — melhora com o tempo

## Stack

| Componente | Tecnologia |
|---|---|
| Linguagem | Python 3.12 |
| Framework | FastAPI |
| IA | Anthropic Claude (Sonnet 4 + Haiku) |
| Banco de dados | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
| Containers | Docker + Docker Compose |
| Automação | N8N (middleware) + APScheduler (cron) |

## Setup rápido (desenvolvimento local)

```bash
# 1. Clonar e entrar no projeto
git clone <repo-url> villa && cd villa

# 2. Copiar e configurar variáveis de ambiente
cp .env.example .env
# Editar .env com suas credenciais

# 3. Subir os containers
docker compose up -d

# 4. Verificar se tudo subiu
docker compose ps

# 5. Testar healthcheck
curl http://localhost:8000/health
```

## Estrutura do projeto

```
villa/
├── core/           # Orquestrador central (cérebro do Villa)
├── modules/        # 13 módulos independentes (M1–M13)
├── integrations/   # Conectores (Kommo, Meta, Google, WhatsApp...)
├── memory/         # Feedback loop, embeddings, base de conhecimento
├── scheduler/      # Rotinas diárias, semanais, monitoramento
├── security/       # Criptografia, audit log, LGPD
├── api/            # Endpoints REST (FastAPI)
├── db/             # Migrations e seeds
├── prompts/        # Todos os prompts centralizados
├── config/         # Configurações YAML por cliente
├── tests/          # Testes automatizados
├── scripts/        # Setup, backup, deploy
└── docs/           # Documentação técnica
```

## Módulos

| # | Módulo | Descrição | Fase |
|---|---|---|---|
| M1 | Roteiros | Geração + tripla validação | 2 |
| M2 | Relatórios | Coleta multi-fonte + análise | 2 |
| M3 | Qualificação | Lead scoring via WhatsApp | 2 |
| M4 | Campanhas | Análise de performance + otimização | 3 |
| M5 | Agendamento | Calendar + Kommo + WhatsApp | 3 |
| M6 | Atendimento | WhatsApp completo (substitui GPT Maker) | 3 |
| M7 | Retroalimentação | Loop comercial ↔ marketing | 4 |
| M8 | Onboarding | Automação das 10 etapas | 4 |
| M9 | Arquivos | Gestão Drive + indexação | 4 |
| M10 | Smooth | Integração Smooth Dentistry | 4 |
| M11 | Hipóteses | Sugestões de criativos baseadas em dados | 3 |
| M12 | Alertas | Monitoramento proativo de métricas | 3 |
| M13 | Conhecimento | RAG — base consultável | 4 |

## Documentação

- [Arquitetura](docs/arquitetura.md)
- [Módulos](docs/modulos.md)
- [Integrações](docs/integracoes.md)
- [Deploy](docs/deploy.md)

## Licença

Propriedade intelectual compartilhada entre Vitor Vilela e WebXP Agency.
Código-fonte confidencial — não distribuir.
