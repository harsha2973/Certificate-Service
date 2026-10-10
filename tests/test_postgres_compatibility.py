from io import StringIO
from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex
from app.models import Job, Certificate


def test_alembic_generates_valid_postgresql_ddl():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", "postgresql+psycopg://user:pass@localhost:5432/certificates")
    
    # Run offline migration with postgresql url - ensures PostgresqlImpl works cleanly
    command.upgrade(cfg, "head", sql=True)


def test_models_compile_for_postgresql():
    pg_dialect = postgresql.dialect()
    
    job_sql = str(CreateTable(Job.__table__).compile(dialect=pg_dialect))
    assert "VARCHAR(32)" in job_sql
    assert "TIMESTAMP WITHOUT TIME ZONE" in job_sql
    assert "PRIMARY KEY (id)" in job_sql

    cert_sql = str(CreateTable(Certificate.__table__).compile(dialect=pg_dialect))
    assert "REFERENCES jobs (id)" in cert_sql
    assert "ON DELETE CASCADE" in cert_sql
    assert "UNIQUE (job_id, position)" in cert_sql
