"""
Villa — tests/prompts/test_m02_formatter_output.py

Testes de propriedades dos outputs do ReportFormatter.

O que está sendo testado:
    ReportFormatter processa dados consolidados e gera strings formatadas.
    Como não depende do Claude diretamente (recebe a análise já gerada),
    esses são testes de propriedades puras — sem mock de API.

    Propriedades verificadas:
        format_daily_whatsapp():
            - Contém nome do cliente
            - Contém investimento quando > 0
            - Contém leads quando > 0
            - Contém análise quando fornecida (truncada em 2 frases)
            - Retorna string não vazia mesmo com dados zerados
            - Sem crash com dados ausentes (dict vazio)

        format_weekly():
            - Contém header com nome e período
            - Contém seção de leads
            - Contém análise no final
            - Retorna string com mais de 10 linhas (relatório completo)
            - Comparativo presente quando previous_period fornecido

        format_monthly_data():
            - Retorna dict com campos obrigatórios
            - Campo type == "monthly"
            - Repassa consolidated, leads_summary, analysis sem alteração
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.prompts


# ── Factories de dados ────────────────────────────────────────────────────────


def make_consolidated(
    total_investment: float = 2500.0,
    total_leads: int = 42,
    cpl_consolidated: float = 59.52,
    total_qualified: int = 18,
    qualification_rate: float = 42.8,
    total_won: int = 3,
    total_revenue: float = 18000.0,
    show_rate: float = 75.0,
    roi: float = 620.0,
) -> dict:
    return {
        "total_investment": total_investment,
        "total_leads": total_leads,
        "cpl_consolidated": cpl_consolidated,
        "total_qualified": total_qualified,
        "qualification_rate": qualification_rate,
        "total_won": total_won,
        "total_revenue": total_revenue,
        "show_rate": show_rate,
        "roi": roi,
    }


def make_data(
    client_name: str = "Clínica Ottoboni",
    period_start: str = "2026-05-01",
    period_end: str = "2026-05-07",
    with_meta: bool = True,
    with_leads: bool = True,
    with_appointments: bool = True,
) -> dict:
    data: dict = {
        "client_name": client_name,
        "period_start": period_start,
        "period_end": period_end,
        "consolidated": make_consolidated(),
    }

    if with_meta:
        data["meta_ads"] = {
            "total_spend": 2000.0,
            "total_impressions": 45000,
            "total_clicks": 1350,
            "avg_ctr": 3.0,
            "avg_cpl": 57.14,
            "campaigns": [
                {"name": "Implante | Awareness", "spend": 1200.0, "leads": 21, "cpl": 57.14},
                {"name": "Ortodontia | Remarketing", "spend": 800.0, "leads": 14, "cpl": 57.14},
            ],
        }

    if with_leads:
        data["leads_summary"] = {
            "total": 42,
            "qualified": 18,
            "won": 3,
            "lost": 5,
            "qualification_rate": 42.8,
            "by_source": {"meta": 35, "google": 5, "organic": 2},
        }

    if with_appointments:
        data["appointments"] = {
            "total": 15,
            "show_rate": 75.0,
            "no_show_rate": 25.0,
        }

    return data


ANALYSIS_TEXTO = (
    "CPL estável em R$59,52, dentro do threshold. "
    "Campanha de Implante concentra 78% dos leads. "
    "Recomendo expandir lookalike 3% para reduzir frequência."
)


# ── format_daily_whatsapp ─────────────────────────────────────────────────────


class TestFormatDailyWhatsapp:
    def test_retorna_string_nao_vazia(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(make_data())

        assert isinstance(result, str)
        assert len(result) > 0

    def test_contem_nome_do_cliente(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(make_data(client_name="Clínica Ottoboni"))

        assert "Ottoboni" in result

    def test_contem_investimento_quando_positivo(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(make_data())

        assert "2.500" in result or "2500" in result

    def test_contem_leads_quando_positivo(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(make_data())

        assert "42" in result

    def test_contem_cpl_quando_positivo(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(make_data())

        assert "59" in result  # parte do CPL R$59,52

    def test_contem_analise_truncada_quando_fornecida(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(make_data(), analysis=ANALYSIS_TEXTO)

        # Análise deve aparecer (primeiras 2 frases)
        assert "CPL" in result

    def test_sem_analise_nao_adiciona_bloco_vazio(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(make_data(), analysis=None)

        # Sem analysis, não deve ter o emoji de lâmpada
        assert "💡" not in result

    def test_nao_crasha_com_dados_zerados(self):
        """Dados zerados não devem causar exceção nem divisão por zero."""
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        data = {
            "client_name": "Teste Zero",
            "consolidated": {
                "total_investment": 0,
                "total_leads": 0,
                "cpl_consolidated": 0,
                "total_qualified": 0,
                "total_won": 0,
                "show_rate": 0,
            },
        }

        result = f.format_daily_whatsapp(data)

        assert isinstance(result, str)
        assert "Teste Zero" in result

    def test_nao_crasha_com_dict_vazio(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp({})

        assert isinstance(result, str)

    def test_client_name_sobrescreve_dado_do_data(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_daily_whatsapp(
            make_data(client_name="Original"), client_name="Substituído"
        )

        assert "Substituído" in result


# ── format_weekly ─────────────────────────────────────────────────────────────


class TestFormatWeekly:
    def test_retorna_string_com_multiplas_linhas(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_weekly(make_data(), ANALYSIS_TEXTO)

        assert isinstance(result, str)
        assert result.count("\n") >= 10

    def test_contem_header_com_nome_e_periodo(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_weekly(
            make_data(
                client_name="Clínica Teste",
                period_start="2026-05-01",
                period_end="2026-05-07",
            ),
            ANALYSIS_TEXTO,
        )

        assert "Clínica Teste" in result
        assert "2026-05-01" in result
        assert "2026-05-07" in result

    def test_contem_secao_de_leads(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_weekly(make_data(), ANALYSIS_TEXTO)

        assert "LEADS" in result.upper()

    def test_contem_analise(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_weekly(make_data(), ANALYSIS_TEXTO)

        assert "CPL estável" in result

    def test_contem_comparativo_com_semana_anterior(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        previous = make_data()
        previous["consolidated"]["total_leads"] = 35

        result = f.format_weekly(make_data(), ANALYSIS_TEXTO, previous_period_data=previous)

        assert "COMPARATIVO" in result.upper() or "↑" in result or "↓" in result

    def test_sem_comparativo_quando_previous_none(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_weekly(make_data(), ANALYSIS_TEXTO, previous_period_data=None)

        assert "COMPARATIVO" not in result.upper()

    def test_contem_roi_quando_investimento_positivo(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_weekly(make_data(), ANALYSIS_TEXTO)

        assert "ROI" in result

    def test_contem_agendamentos_quando_tem_dados(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_weekly(make_data(with_appointments=True), ANALYSIS_TEXTO)

        assert "AGENDAMENTOS" in result.upper() or "show" in result.lower()


# ── format_monthly_data ───────────────────────────────────────────────────────


class TestFormatMonthlyData:
    def test_retorna_dict(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_monthly_data(make_data(), ANALYSIS_TEXTO)

        assert isinstance(result, dict)

    def test_type_e_monthly(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_monthly_data(make_data(), ANALYSIS_TEXTO)

        assert result["type"] == "monthly"

    def test_campos_obrigatorios_presentes(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_monthly_data(make_data(), ANALYSIS_TEXTO)

        campos = [
            "type",
            "client_name",
            "period_start",
            "period_end",
            "consolidated",
            "leads_summary",
            "analysis",
            "weekly_summaries",
        ]
        for campo in campos:
            assert campo in result, f"Campo ausente: '{campo}'"

    def test_analysis_repassada_sem_alteracao(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_monthly_data(make_data(), ANALYSIS_TEXTO)

        assert result["analysis"] == ANALYSIS_TEXTO

    def test_weekly_summaries_vazio_por_padrao(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        result = f.format_monthly_data(make_data(), ANALYSIS_TEXTO)

        assert result["weekly_summaries"] == []

    def test_weekly_summaries_repassado_quando_fornecido(self):
        from modules.m02_relatorios.formatters import ReportFormatter

        f = ReportFormatter()

        summaries = [{"week": 1, "leads": 10}, {"week": 2, "leads": 12}]
        result = f.format_monthly_data(make_data(), ANALYSIS_TEXTO, weekly_summaries=summaries)

        assert result["weekly_summaries"] == summaries
