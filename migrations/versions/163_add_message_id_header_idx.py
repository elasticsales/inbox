"""Add message_id_header index

Revision ID: 1b0b4e6fdf96
Revises: 4a8aacb82076
Create Date: 2015-05-08 16:25:39.140215

"""

# revision identifiers, used by Alembic.
revision = '1b0b4e6fdf96'
down_revision = '4a8aacb82076'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

def upgrade():
    op.create_index('ix_message_message_id_header', 'message',
                    ['message_id_header'], unique=False,
                    mysql_length={'message_id_header': 191})


def downgrade():
    op.drop_index('ix_message_message_id_header', table_name='message')
