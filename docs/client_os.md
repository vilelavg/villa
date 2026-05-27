# Client OS — Estado Narrativo Vivo por Cliente

> **Status:** Fase 1.A — Fundação isolada (entregável atual)
> **Próximas:** 1.B (integração no VillaCore) → 1.C (adoção pelos módulos M1–M12)

## O que é

`memory/client_os/` é uma **camada acima** do `memory/feedback_loop.py`. Onde o
feedback loop faz RAG semântico em interações passadas (recuperação de exemplos
similares), o Client OS mantém **estado estruturado e narrativo** sobre cada
cliente — fatos estáveis, episódios temporais, preferências observadas,
pendências abertas e objetivos ativos.

A diferença prática: ao receber qualquer requisição de um cliente, a Villa pode
injetar no contexto do Claude uma narrativa compacta tipo:

```
## Estado atual do cliente — ottoboni

### Fatos estabelecidos
**budget:**
- monthly: amount=5000, currency=BRL
**owner_profile:**
- risk_tolerance: conservative

### Preferências e padrões observados
- [approvals] demora 3 dias para aprovar criativos (observado 5x)

### Objetivos ativos
- Reduzir CPL em 20% → cpl = 40.0 (prazo: em 14 dias)

### Pendências abertas
- [caio] Aprovar criativos da campanha de implantes — vencimento em 2 dias

### Histórico recente
- há 3h [M4]: Campanha de implantes lançada ✓
- há 2 dias [M2]: Relatório semanal enviado
```

Isso muda o comportamento da Villa de "respondedor de query" para "agente
que sabe quem é o cliente".

## Modelo conceitual

| Tabela | O que guarda | Quando usar |
|---|---|---|
| `client_state` | Singleton por cliente (versão + summary compilado) | Metadado |
| `client_facts` | Fatos estáveis: perfil, budget, especialidade | Quando descobre algo que não muda toda hora |
| `client_episodes` | Eventos temporais: o que aconteceu, quando, com que outcome | Toda ação relevante de qualquer módulo |
| `client_preferences` | Padrões observados: como o cliente se comporta | Quando nota um padrão repetido |
| `client_pending` | Open loops: coisas esperando alguém agir | Quando algo fica em aberto |
| `client_objectives` | Metas ativas com target e prazo | Quando o cliente (ou Villa) define meta — base do Proactive Agent |

## Uso típico

```python
from memory.client_os import ClientOS

os_ = await ClientOS.for_slug(db, "ottoboni")

# Antes de qualquer ação: pega narrativa pra injetar no prompt
narrative = await os_.narrative()

# Após qualquer evento relevante: registra
await os_.record_episode(
    "campaign_launched",
    "Campanha de implantes lançada com R$ 500/dia",
    details={"budget_daily": 500, "specialty": "implantes"},
    outcome="neutral",
    module_source="M4",
    linked_refs={"campaign_id": 17},
)

# Quando observa padrão de comportamento
await os_.observe_preference(
    "approvals",
    "demora 3 dias para aprovar criativos"
)

# Quando algo fica pendente
loop_id = await os_.open_loop(
    "Aguardar aprovação de 3 criativos",
    owner="caio",
    due_at=datetime.now(timezone.utc) + timedelta(days=2),
    module_source="M4",
)

# Quando resolve
await os_.close_loop(loop_id, status="resolved")

# Quando estabelece meta
await os_.add_objective(
    "Reduzir CPL em 20%",
    target_metric="cpl",
    target_value=40.0,
    deadline=datetime(2026, 7, 31, tzinfo=timezone.utc),
)
await os_.update_objective_progress(oid, {"current": 50.0, "trend": "improving"})
```

## Integração com VillaCore (Fase 1.B)

Na Fase 1.B (próxima entrega) o `VillaCore.dispatch()` chamará automaticamente:

```python
os_ = await ClientOS.for_slug(db, client_slug)
narrative = await os_.narrative()
context["client_narrative"] = narrative
# ...passa pro módulo apropriado
```

E os módulos M1–M12 ganharão acesso ao `os_` no contexto, sem precisar
instanciar manualmente.

## Schema

Todas as tabelas:
- Têm FK `clients.id` com `ondelete=CASCADE` (deletar cliente apaga todo o estado)
- Usam `JSONB` (PostgreSQL) para campos flexíveis (`value`, `details`,
  `linked_refs`, `progress`)
- Têm índices compostos otimizados para as queries mais comuns (por cliente +
  campo de filtro)

A migration `db/migrations/versions/client_os_001_add_tables.py` cria todas
as 6 tabelas + índices + constraints unique.

⚠️ Ajustar antes de rodar:
- O `down_revision = None` precisa ser apontado pro head atual do projeto
- Se `clients.id` for `BigInteger` em vez de `Integer`, ajustar o tipo das FKs

## Convenções

### `episode_type` (snake_case curto)

Exemplos recomendados (não-exaustivo):
- `campaign_launched`, `campaign_paused`, `campaign_finished`
- `lead_received`, `lead_qualified`, `lead_converted`, `lead_lost`
- `creative_submitted`, `creative_approved`, `creative_rejected`
- `report_sent`, `report_acknowledged`
- `anomaly_detected`, `anomaly_resolved`
- `objective_set`, `objective_achieved`, `objective_abandoned`
- `meeting_held`, `feedback_received`

### `owner` de pendências

`villa | caio | thais | client | <nome>`

### `outcome` de episódios

`positive | negative | neutral | pending`

### Categorias de fatos (sugeridas)

- `owner_profile`: traços do dono da clínica (risk_tolerance, communication_style)
- `budget`: monthly, daily, allocation por procedimento
- `specialty_focus`: primary, secondary, growing_interest
- `region`: city, radius_km, target_neighborhoods
- `contact_preferences`: preferred_channel, response_window
- `historical_performance`: best_quarter, typical_cpl

### Tópicos de preferência (sugeridos)

- `approvals`: padrões de aprovação de criativos/campanhas
- `copy_style`: que tipo de copy funciona melhor
- `creative_style`: estética visual preferida
- `communication_channel`: WhatsApp vs e-mail vs ligação
- `report_format`: detalhado vs resumido, bullets vs prosa

## Próximos passos

### Fase 1.B — Integração (próxima)
- Auto-load do `ClientOS` em `VillaCore.dispatch()`
- Injeção automática de `narrative` no context dos módulos
- Hooks no `feedback_loop` pra registrar episódios de aprendizado

### Fase 1.C — Adoção
- M1–M12 escrevem episódios/fatos via `ClientOS`
- Cron semanal regenera `client_state.summary` chamando Claude
  (compactação semântica dos últimos 30 dias)

### Fora do escopo desta fase
- Autonomy Engine (níveis de confiança por tipo de ação) — depende de
  Client OS ativo
- Proactive Agent (Villa age sem ser acionada) — depende de Objectives
  populados

## Erros previstos

| Exception | Quando |
|---|---|
| `ClientNotFoundError` | `for_slug()` com slug que não existe |
| `ClientOSError` | Erro genérico (DB falhou, ID inválido, módulo do schema não importou) |
| `ValueError` | Argumentos inválidos (confidence fora de 0..1, outcome inválido, etc) |

Todos os métodos têm `try/except + logger.error/warning` conforme padrão Villa.
`_bump_version()` é metadado — falhas são logadas como warning e não
interrompem a operação principal.
