"""
Villa — Prompts do Módulo M01 (Roteiros)
Templates de prompt para geração e validação de roteiros.
Separados do código para facilitar iteração e teste de prompts.
"""


SYSTEM_PROMPT = """Você é o Villa, módulo de criação de roteiros da WebXP Agency.

A WebXP é uma empresa de performance digital especializada no mercado odontológico brasileiro. Seus clientes são dentistas autônomos, clínicas odontológicas e dentistas-professores que vendem cursos.

## Sua função

Criar roteiros de vídeo (Reels, Stories, anúncios) para os clientes da WebXP. Cada roteiro segue a estrutura GANCHO + CORPO + CTA e deve passar pela tripla validação antes de ser entregue.

## Regras de ouro

1. NUNCA use clichês genéricos como "Você sabia que...", "Está cansado de...", "Neste vídeo eu vou te mostrar..."
2. O gancho DEVE parar o scroll em 3 segundos — seja específico, use números, contraste ou polêmica controlada
3. O corpo DEVE seguir um framework de persuasão (SPIN, AIDA, PAS) — não basta informar, precisa persuadir
4. A CTA DEVE ser UMA ação única, clara e de baixo atrito — nunca dê duas opções
5. Tom de voz varia por cliente — sempre consulte as instruções de tom antes de escrever
6. Use linguagem do nicho odontológico — termos técnicos simplificados que o público entende
7. Roteiros são para VÍDEO — escreva como se fala, não como se lê. Frases curtas. Ritmo.

## Formato de saída

Sempre retorne o roteiro neste formato exato:

---
TÍTULO: [título descritivo do roteiro]

GANCHO (3 segundos):
[texto do gancho]

CORPO:
[texto do corpo com persuasão]

CTA:
[chamada para ação única]

ROTEIRO COMPLETO (como falar):
[texto corrido, como o dentista vai falar no vídeo]
---
"""


GENERATION_PROMPT = """Crie um roteiro de vídeo para o seguinte briefing:

CLIENTE: {client_name} ({specialty})
TOM DE VOZ: {tone}
TEMA: {topic}
FORMATO: {format}
PÚBLICO-ALVO: {audience}
OBJETIVO: {objective}

{memory_context}

{training_examples}

Crie o roteiro seguindo rigorosamente a estrutura GANCHO + CORPO + CTA.
"""


HOOK_VALIDATION_PROMPT = """Avalie este GANCHO de roteiro para vídeo de {specialty}:

GANCHO: "{hook}"

Critérios de avaliação (0-10 cada):

1. **Pattern Interrupt (peso 3):** Faz a pessoa PARAR de scrollar nos primeiros 3 segundos? Usa contraste, número específico, polêmica controlada, pergunta retórica forte ou afirmação surpreendente?

2. **Especificidade (peso 3):** É específico para o nicho odontológico ou poderia ser de qualquer área? Menciona algo concreto (procedimento, resultado, valor, tempo)?

3. **Curiosidade (peso 2):** Cria um "gap de informação" que faz a pessoa querer ver o resto? O espectador sente que precisa saber mais?

4. **Anti-clichê (peso 2):** Evita fórmulas batidas como "Você sabia...", "Neste vídeo...", "Está cansado de..."? É original?

Responda em JSON:
{{
    "scores": {{
        "pattern_interrupt": 0-10,
        "especificidade": 0-10,
        "curiosidade": 0-10,
        "anti_cliche": 0-10
    }},
    "weighted_score": 0-10,
    "passed": true/false,
    "feedback": "explicação do que está bom e do que precisa melhorar",
    "suggestion": "se não passou, sugerir uma versão melhorada do gancho"
}}
"""


BODY_VALIDATION_PROMPT = """Avalie este CORPO de roteiro para vídeo de {specialty}:

CORPO: "{body}"
GANCHO usado: "{hook}"

Critérios de avaliação (0-10 cada):

1. **Framework de persuasão (peso 3):** Segue SPIN (Situação, Problema, Implicação, Need-payoff), AIDA (Atenção, Interesse, Desejo, Ação) ou PAS (Problema, Agitação, Solução)? A estrutura é identificável?

2. **Prova e autoridade (peso 3):** Usa pelo menos um destes: número/estatística, caso real, depoimento, autoridade técnica, comparação antes/depois? Não basta afirmar — precisa provar.

3. **Fluxo narrativo (peso 2):** As ideias fluem naturalmente? Cada frase leva à próxima? Não tem saltos lógicos ou informações soltas?

4. **Conexão com gancho (peso 2):** O corpo entrega o que o gancho prometeu? Se o gancho criou uma expectativa, o corpo a satisfaz?

Responda em JSON:
{{
    "scores": {{
        "framework_persuasao": 0-10,
        "prova_autoridade": 0-10,
        "fluxo_narrativo": 0-10,
        "conexao_gancho": 0-10
    }},
    "weighted_score": 0-10,
    "passed": true/false,
    "feedback": "explicação do que está bom e do que precisa melhorar",
    "suggestion": "se não passou, indicar o que ajustar no corpo"
}}
"""


CTA_VALIDATION_PROMPT = """Avalie esta CTA (chamada para ação) de roteiro para vídeo de {specialty}:

CTA: "{cta}"
CONTEXTO DO ROTEIRO:
- Gancho: "{hook}"
- Corpo resumido: "{body_summary}"

Critérios de avaliação (0-10 cada):

1. **Ação única (peso 3):** É UMA ação só? Não tem "clique no link E me siga E comente"? Quanto mais focada, melhor.

2. **Baixo atrito (peso 3):** A ação é fácil de executar? "Clique no link da bio" é mais fácil que "Acesse meusite.com.br/pagina-especifica-longa". Quanto menor o esforço, maior a conversão.

3. **Urgência/escassez (peso 2):** Tem algum gatilho de ação imediata? Limite de vagas, prazo, bônus por tempo limitado? (Não precisa ser forçado — se for orgânico, melhor.)

4. **Rastreabilidade (peso 2):** A ação é rastreável? Dá pra saber quem clicou? Link com UTM > "me chame no WhatsApp" genérico.

Responda em JSON:
{{
    "scores": {{
        "acao_unica": 0-10,
        "baixo_atrito": 0-10,
        "urgencia": 0-10,
        "rastreabilidade": 0-10
    }},
    "weighted_score": 0-10,
    "passed": true/false,
    "feedback": "explicação do que está bom e do que precisa melhorar",
    "suggestion": "se não passou, sugerir uma CTA melhorada"
}}
"""


HOOK_VARIATIONS_PROMPT = """Com base neste roteiro aprovado, gere {count} variações do gancho para teste A/B:

GANCHO ORIGINAL: "{hook}"
CORPO: "{body}"
ESPECIALIDADE: {specialty}

Cada variação deve:
- Manter a mesma promessa/ideia central
- Usar uma abordagem diferente (número, pergunta, polêmica, afirmação, contraste)
- Funcionar nos primeiros 3 segundos de vídeo

Responda em JSON:
{{
    "variations": [
        {{"hook": "texto do gancho alternativo", "approach": "qual abordagem usou"}}
    ]
}}
"""


REFINEMENT_PROMPT = """O roteiro abaixo não passou na validação. Reescreva corrigindo os pontos indicados.

ROTEIRO ORIGINAL:
Gancho: {hook}
Corpo: {body}
CTA: {cta}

FEEDBACK DA VALIDAÇÃO:
{validation_feedback}

Reescreva o roteiro COMPLETO corrigindo TODOS os pontos indicados. Mantenha o formato padrão (GANCHO + CORPO + CTA + ROTEIRO COMPLETO).
"""
