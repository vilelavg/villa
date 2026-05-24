"""
Villa — Configurações centrais
Carrega variáveis de ambiente do .env via pydantic-settings.
Importar em qualquer lugar: from core.config import settings
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações globais do Villa. Lê do .env automaticamente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Ambiente ──
    environment: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    # ── FastAPI ──
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_workers: int = 2
    allowed_origins: str = "http://localhost:3000"

    # ── PostgreSQL ──
    postgres_user: str = "villa"
    postgres_password: str = "villa_dev_2026"
    postgres_db: str = "villa_db"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    database_url: str = ""

    # ── Redis ──
    redis_url: str = "redis://redis:6379/0"

    # ── Anthropic (Claude) ──
    anthropic_api_key: str = ""
    anthropic_model_primary: str = "claude-sonnet-4-20250514"
    anthropic_model_fast: str = "claude-haiku-4-5-20251001"
    anthropic_max_tokens: int = 4096
    anthropic_temperature: float = 0.3

    # ── Kommo CRM ──
    kommo_api_token: str = ""
    kommo_account_url: str = ""
    kommo_webhook_secret: str = ""

    # ── Meta Ads ──
    meta_access_token: str = ""
    meta_app_secret: str = ""
    meta_ad_account_ids: str = ""
    meta_pixel_id: str = ""
    meta_capi_token: str = ""

    # ── WhatsApp Business API ──
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_verify_token: str = ""
    whatsapp_webhook_secret: str = ""

    # ── Google ──
    google_service_account_json: str = "config/google_service_account.json"
    google_ads_script_url: str = ""

    # ── InLead ──
    inlead_webhook_secret: str = ""

    # ── N8N ──
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str = ""

    # ── Segurança ──
    jwt_secret_key: str = "TROCAR_CHAVE_JWT_FORTE_AQUI"
    jwt_algorithm: str = "HS256"
    jwt_expiration_minutes: int = 1440
    encryption_key: str = "TROCAR_CHAVE_FERNET_AQUI"

    # ── Scheduler ──
    daily_routine_hour: int = 7
    daily_routine_minute: int = 0
    weekly_report_day: str = "fri"
    weekly_report_hour: int = 8
    monitor_interval_minutes: int = 30

    # ── Backup ──
    backup_dir: str = "/opt/villa/data/backups"
    backup_retention_days: int = 30

    @property
    def async_database_url(self) -> str:
        """Monta a URL de conexão async se não foi definida explicitamente."""
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sync_database_url(self) -> str:
        """URL síncrona para Alembic migrations."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins(self) -> list[str]:
        """Lista de origens CORS permitidas."""
        return [o.strip() for o in self.allowed_origins.split(",")]

    @property
    def meta_account_ids_list(self) -> list[str]:
        """Lista de IDs de contas de anúncio do Meta."""
        if not self.meta_ad_account_ids:
            return []
        return [a.strip() for a in self.meta_ad_account_ids.split(",")]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    """Retorna instância cacheada das configurações."""
    return Settings()


# Atalho para importação direta
settings = get_settings()
