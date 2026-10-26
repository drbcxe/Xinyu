"""Add accounts, durable tasks and notification delivery records."""

import sqlalchemy as sa
from alembic import op

revision = "e82b3da4c612"
down_revision = "d71a2c93b501"
branch_labels = None
depends_on = None


def upgrade():
    # Frozen PostgreSQL schema; migration does not import future ORM models.
    op.execute("""
CREATE TABLE relationship_accounts (
	username VARCHAR(80) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	password_hash TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_relationship_accounts PRIMARY KEY (username), 
	CONSTRAINT uq_relationship_accounts_owner UNIQUE (owner)
)
""")
    op.execute("""
CREATE TABLE relationship_sessions (
	token_hash VARCHAR(64) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_relationship_sessions PRIMARY KEY (token_hash)
)
""")
    op.execute(
        "CREATE INDEX ix_relationship_sessions_owner ON relationship_sessions (owner)"
    )
    op.execute("""
CREATE TABLE relationship_login_limits (
	key VARCHAR(64) NOT NULL, 
	failures INTEGER NOT NULL, 
	reset_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_relationship_login_limits PRIMARY KEY (key)
)
""")
    op.execute("""
CREATE TABLE relationship_jobs (
	id VARCHAR(36) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	request_id VARCHAR(36) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	payload JSON NOT NULL, 
	turn_id VARCHAR(36), 
	error TEXT NOT NULL, 
	attempts INTEGER NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_relationship_jobs PRIMARY KEY (id), 
	CONSTRAINT uq_relationship_jobs_owner UNIQUE (owner, request_id)
)
""")
    op.execute("CREATE INDEX ix_relationship_jobs_owner ON relationship_jobs (owner)")
    op.execute("CREATE INDEX ix_relationship_jobs_status ON relationship_jobs (status)")
    op.execute("""
CREATE TABLE relationship_notices (
	id VARCHAR(36) NOT NULL, 
	owner VARCHAR(64) NOT NULL, 
	item_id VARCHAR(36) NOT NULL, 
	revision INTEGER NOT NULL, 
	title VARCHAR(500) NOT NULL, 
	status VARCHAR(20) NOT NULL, 
	weixin_status VARCHAR(20) NOT NULL, 
	error TEXT NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_relationship_notices PRIMARY KEY (id), 
	CONSTRAINT uq_relationship_notices_item_id UNIQUE (item_id, revision)
)
""")
    op.execute(
        "CREATE INDEX ix_relationship_notices_item_id ON relationship_notices (item_id)"
    )
    op.execute(
        "CREATE INDEX ix_relationship_notices_owner ON relationship_notices (owner)"
    )
    op.execute("""
CREATE TABLE relationship_weixin_bindings (
	owner VARCHAR(64) NOT NULL, 
	bot_id VARCHAR(200) NOT NULL, 
	user_id VARCHAR(200) NOT NULL, 
	token_encrypted TEXT NOT NULL, 
	base_url VARCHAR(500) NOT NULL, 
	context_token TEXT NOT NULL, 
	cursor TEXT NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	CONSTRAINT pk_relationship_weixin_bindings PRIMARY KEY (owner)
)
""")
    op.add_column(
        "relationship_items",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "relationship_items",
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade():
    for name in (
        "relationship_weixin_bindings",
        "relationship_notices",
        "relationship_jobs",
        "relationship_login_limits",
        "relationship_sessions",
        "relationship_accounts",
    ):
        op.drop_table(name)
    op.drop_column("relationship_items", "details")
    op.drop_column("relationship_items", "revision")
