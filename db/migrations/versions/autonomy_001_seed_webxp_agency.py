"""seed webxp_agency institutional client

Revision ID: autonomy_001
Revises: client_os_002_voyage
Create Date: 2026-05-27
"""

import sqlalchemy as sa
from alembic import op

revision = "autonomy_001"
down_revision = "client_os_002_voyage"
branch_labels = None
depends_on = None

WEBXP_AGENCY_SLUG = "webxp_agency"
WEBXP_AGENCY_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.execute(
        sa.text(
            f"""
            INSERT INTO clients (id, slug, name, config)
            VALUES (
                '{WEBXP_AGENCY_ID}'::uuid,
                '{WEBXP_AGENCY_SLUG}',
                'WebXP Agency (institucional)',
                '{{"type": "institutional"}}'::jsonb
            )
            ON CONFLICT (slug) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(f"DELETE FROM clients WHERE slug = '{WEBXP_AGENCY_SLUG}'")
    )
