"""Add message_id_header index

Revision ID: 16e33ed6775b
Revises: 486c7fa5b533
Create Date: 2015-03-17 23:11:38.367488

"""

# revision identifiers, used by Alembic.
revision = '16e33ed6775b'
down_revision = '486c7fa5b533'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

def upgrade():
    op.create_index('ix_message_namespace_id_message_id_header', 'message', ['namespace_id', 'message_id_header'], unique=False)


def downgrade():
    op.drop_index('ix_message_namespace_id_message_id_header', table_name='message')
