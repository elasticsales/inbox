"""Adding debug field to account table

Revision ID: 4a8aacb82076
Revises: 365071c47fa7
Create Date: 2015-05-07 21:30:37.105575

"""

# revision identifiers, used by Alembic.
revision = '4a8aacb82076'
down_revision = '365071c47fa7'

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

def upgrade():
    op.add_column('account', sa.Column('debug', sa.Boolean(), server_default='0', nullable=True))


def downgrade():
    op.drop_column('account', 'debug')
