"""
Villa — M02 Relatórios: Formatadores
Transforma dados consolidados em relatórios legíveis.

Formatos:
    - WhatsApp curto (diário): resumo de 5-8 linhas para mandar no WhatsApp
    - Semanal (texto): análise completa com insights
    - Mensal (PDF-ready): relatório completo para gerar PDF
"""


class ReportFormatter:
    """
    Formata relatórios consolidados em diferentes formatos de saída.

    Uso:
        formatter = ReportFormatter()                       # pt-BR por padrão
        formatter = ReportFormatter(locale="en-US")        # formato americano

        whatsapp_msg = formatter.format_daily_whatsapp(data, analysis)
        weekly_text = formatter.format_weekly(data, analysis)
    """

    def __init__(self, locale: str = "pt-BR") -> None:
        """
        Args:
            locale: "pt-BR" (padrão) — separador de milhar ponto, decimal vírgula
                    "en-US"          — separador de milhar vírgula, decimal ponto
        """
        self.locale = locale

    def _fmt(self, value: float, decimals: int = 2) -> str:
        """
        Formata um número monetário conforme o locale configurado.

        pt-BR: 2500.00 → "2.500,00"
        en-US: 2500.00 → "2,500.00"
        """
        formatted = f"{value:,.{decimals}f}"  # sempre gera formato en-US primeiro
        if self.locale == "pt-BR":
            # Troca: vírgula→X, ponto→vírgula, X→ponto
            formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
        return formatted

    def format_daily_whatsapp(
        self,
        data: dict,
        analysis: str | None = None,
        client_name: str | None = None,
    ) -> str:
        """
        Formato curto para envio diário via WhatsApp.
        5-8 linhas, direto ao ponto.
        """
        consolidated = data.get("consolidated", {})
        meta = data.get("meta_ads", {})
        leads = data.get("leads_summary", {})

        name = client_name or data.get("client_name", "Cliente")
        period = data.get("period_end", "hoje")

        lines = [
            f"📊 *{name}* — {period}",
            "",
        ]

        # Investimento e leads
        invest = consolidated.get("total_investment", 0)
        total_leads = consolidated.get("total_leads", 0)
        cpl = consolidated.get("cpl_consolidated", 0)

        if invest > 0:
            lines.append(f"💰 Investimento: R$ {self._fmt(invest)}")
        if total_leads > 0:
            lines.append(f"👤 Leads: {total_leads}")
        if cpl > 0:
            lines.append(f"📉 CPL: R$ {self._fmt(cpl)}")

        # Qualificação e conversão
        qualified = consolidated.get("total_qualified", 0)
        qual_rate = consolidated.get("qualification_rate", 0)
        if qualified > 0:
            lines.append(f"✅ Qualificados: {qualified} ({qual_rate}%)")

        won = consolidated.get("total_won", 0)
        if won > 0:
            revenue = consolidated.get("total_revenue", 0)
            lines.append(f"🏆 Fechados: {won} (R$ {self._fmt(revenue)})")

        # Show rate
        show_rate = consolidated.get("show_rate", 0)
        if show_rate > 0:
            lines.append(f"📅 Taxa de show: {show_rate}%")

        # Análise do Villa (se houver)
        if analysis:
            lines.append("")
            # Pegar apenas as 2 primeiras frases da análise
            sentences = analysis.split(". ")
            short_analysis = ". ".join(sentences[:2]) + "."
            lines.append(f"💡 {short_analysis}")

        return "\n".join(lines)

    def format_weekly(
        self,
        data: dict,
        analysis: str,
        previous_period_data: dict | None = None,
    ) -> str:
        """
        Relatório semanal completo com análise e comparativo.
        """
        consolidated = data.get("consolidated", {})
        meta = data.get("meta_ads", {})
        leads = data.get("leads_summary", {})
        apts = data.get("appointments", {})

        name = data.get("client_name", "Cliente")
        period_start = data.get("period_start", "")
        period_end = data.get("period_end", "")

        lines = [
            f"📊 RELATÓRIO SEMANAL — {name}",
            f"Período: {period_start} a {period_end}",
            "═" * 40,
            "",
        ]

        # ── Investimento e Performance ──
        lines.append("💰 INVESTIMENTO E PERFORMANCE")
        lines.append(
            f"  Investimento total: R$ {self._fmt(consolidated.get('total_investment', 0))}"
        )

        if meta and not meta.get("error"):
            lines.append(f"  Meta Ads: R$ {self._fmt(meta.get('total_spend', 0))}")
            lines.append(f"    Impressões: {meta.get('total_impressions', 0):,}")
            lines.append(f"    Cliques: {meta.get('total_clicks', 0):,}")
            lines.append(f"    CTR médio: {meta.get('avg_ctr', 0)}%")
            lines.append(f"    CPL médio: R$ {self._fmt(meta.get('avg_cpl', 0))}")

            if meta.get("campaigns"):
                lines.append("  Campanhas:")
                for camp in meta["campaigns"][:5]:
                    lines.append(
                        f"    • {camp['name']}: R${self._fmt(camp['spend'])} | {camp['leads']} leads | CPL R${self._fmt(camp.get('cpl', 0) or 0)}"
                    )

        lines.append("")

        # ── Leads ──
        lines.append("👤 LEADS")
        lines.append(f"  Total captados: {leads.get('total', 0)}")
        lines.append(
            f"  Qualificados: {leads.get('qualified', 0)} ({leads.get('qualification_rate', 0)}%)"
        )
        lines.append(f"  Fechados: {leads.get('won', 0)}")
        lines.append(f"  Perdidos: {leads.get('lost', 0)}")

        if leads.get("by_source"):
            lines.append("  Por fonte:")
            for source, count in leads["by_source"].items():
                lines.append(f"    • {source}: {count}")

        lines.append("")

        # ── Agendamentos ──
        if apts and apts.get("total", 0) > 0:
            lines.append("📅 AGENDAMENTOS")
            lines.append(f"  Total: {apts['total']}")
            lines.append(f"  Taxa de show: {apts.get('show_rate', 0)}%")
            lines.append(f"  No-show: {apts.get('no_show_rate', 0)}%")
            lines.append("")

        # ── ROI ──
        roi = consolidated.get("roi", 0)
        revenue = consolidated.get("total_revenue", 0)
        invest = consolidated.get("total_investment", 0)
        if invest > 0:
            lines.append("📈 ROI")
            lines.append(f"  Receita: R$ {self._fmt(revenue)}")
            lines.append(f"  Investimento: R$ {self._fmt(invest)}")
            lines.append(f"  ROI: {roi}%")
            lines.append("")

        # ── Comparativo com semana anterior ──
        if previous_period_data:
            lines.append("📊 COMPARATIVO COM SEMANA ANTERIOR")
            prev = previous_period_data.get("consolidated", {})
            curr = consolidated

            comparisons = [
                ("Leads", curr.get("total_leads", 0), prev.get("total_leads", 0)),
                (
                    "CPL",
                    curr.get("cpl_consolidated", 0),
                    prev.get("cpl_consolidated", 0),
                ),
                (
                    "Qualificação",
                    curr.get("qualification_rate", 0),
                    prev.get("qualification_rate", 0),
                ),
                (
                    "Investimento",
                    curr.get("total_investment", 0),
                    prev.get("total_investment", 0),
                ),
            ]

            for label, current, previous in comparisons:
                if previous and previous > 0:
                    change = ((current - previous) / previous) * 100
                    arrow = "↑" if change > 0 else "↓" if change < 0 else "→"
                    lines.append(f"  {label}: {arrow} {abs(change):.1f}%")

            lines.append("")

        # ── Análise do Villa ──
        lines.append("💡 ANÁLISE")
        lines.append(analysis)

        return "\n".join(lines)

    def format_monthly_data(
        self,
        data: dict,
        analysis: str,
        weekly_summaries: list[dict] | None = None,
    ) -> dict:
        """
        Prepara dados para relatório mensal em PDF.
        Retorna estrutura que pode ser usada por um gerador de PDF.
        """
        return {
            "type": "monthly",
            "client_name": data.get("client_name", ""),
            "period_start": data.get("period_start", ""),
            "period_end": data.get("period_end", ""),
            "consolidated": data.get("consolidated", {}),
            "meta_ads": data.get("meta_ads", {}),
            "google_ads": data.get("google_ads", {}),
            "leads_summary": data.get("leads_summary", {}),
            "appointments": data.get("appointments", {}),
            "analysis": analysis,
            "weekly_summaries": weekly_summaries or [],
        }
