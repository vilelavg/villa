# Villa — Identidade WebXP

## Contexto de negócio

A WebXP é uma empresa de performance digital com foco exclusivo no mercado odontológico brasileiro. Opera desde [data a confirmar] e atualmente atende 17 clientes ativos.

### Diferencial competitivo

A WebXP integra todo o funil de vendas — do anúncio ao fechamento. Diferente de agências que entregam apenas leads, a WebXP gerencia:
- Criação de criativos e roteiros de vídeo
- Gestão de campanhas (Meta Ads + Google Ads)
- Captação via InLead (formulários com qualificação)
- Atendimento e qualificação via WhatsApp
- Agendamento de consultas
- CRM (Kommo) com pipeline completo
- Relatórios de performance com retroalimentação

Framework de diagnóstico: "Em qual etapa do funil você está perdendo dinheiro?"

### Tipos de cliente

**Dentistas-Professores (foco principal — Booker)**
- Vendem cursos e turmas presenciais
- Ticket médio: R$ 5.000 - R$ 30.000 por turma
- Funil: Anúncio → Landing Page → InLead → WhatsApp → Qualificação → Agendamento → Venda
- Métricas-chave: CPL, taxa de qualificação, taxa de show, ticket médio

**Clínicas Odontológicas Premium (foco secundário)**
- Oferecem procedimentos de alto valor (implantes, lentes, ortodontia)
- Ticket médio: R$ 3.000 - R$ 20.000 por procedimento
- Funil similar, com foco em agendamento de avaliação
- Métricas-chave: CPL, taxa de agendamento, taxa de comparecimento, conversão

### Equipe

- **Caio Frison** — Sócio. Comercial, conteúdo (roteiros/copies), desenvolvimento técnico. Role: admin.
- **Thaís Marangon** — Sócia. Performance, análise estratégica, gestão criativa, N8N. Role: admin.
- **Ana Lívia** — Customer Success e relacionamento pós-venda. Role: operator.
- **Mariana Oliveira** — Operacional/CS. Contrato com não concorrência de 12 meses. Role: operator.
- **Jasmyne** — SDR. Modelo híbrido (fixo + R$25/agendamento). Role: sdr.

### Stack tecnológica

- Meta Ads + Google Ads (6 contas)
- N8N em VPS Hostinger (Docker + PostgreSQL + Caddy)
- Kommo CRM (API + webhooks)
- InLead (campos com naming aleatório por cliente)
- WhatsApp Business API oficial
- Google Apps Script como dashboard de Google Ads
- Anthropic API (Claude) no N8N para qualificação
- Freepik Spaces com LoRA por cliente
- ActiveCampaign (pouco usado)
- CapCut para edição
- Tactiq para transcrição

### Métricas e thresholds padrão

Estes são os valores de referência. Cada cliente pode ter thresholds customizados em sua config:

- CPL aceitável: R$ 20 - R$ 80 (varia por especialidade e ticket)
- CTR mínimo: 1.2% - 2.0%
- Frequência máxima: 3.0 (acima disso, audience fatigue)
- Taxa de qualificação: > 40% dos leads captados
- Taxa de show (consulta agendada): > 60%
- Taxa de conversão (proposta → venda): > 15%

### Processo de roteiros (tripla validação)

**1. Gancho (primeiros 3 segundos)**
- Critério: pattern interrupt — faz a pessoa parar de scrollar
- Deve ser específico para o nicho (não genérico)
- Evitar clichês do tipo "Você sabia que..."
- Score mínimo: 7.0/10

**2. Corpo (persuasão)**
- Critério: segue framework SPIN ou AIDA
- Deve ter prova social, autoridade ou lógica
- Fluxo narrativo coerente
- Score mínimo: 7.0/10

**3. CTA (chamada para ação)**
- Critério: ação única, clara, baixo atrito
- Deve ser rastreável (link com UTM)
- Evitar múltiplas CTAs
- Score mínimo: 7.0/10

### Comunidade Smooth Dentistry

Sociedade paralela (6 sócios, incluindo Caio e Thaís). 795+ membros no WhatsApp. Portal em Wix + app. Mensalidade planejada: R$ 997/mês. Gateway: Pagar.me. Operação independente da WebXP — integração futura via módulo M10.

### Produto SaaS para dentistas

Visão futura: versão simplificada do Villa vendida para dentistas-professores. Modelo de negócio: R$ 2.500 fixo/mês + 20% do lucro líquido do curso/turma. Funcionalidades: atendimento 24/7, qualificação, aquecimento, agendamento, suporte ao aluno, relatórios.
