from uuid import UUID, uuid4

from sqlalchemy import ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id", "run_id", name="uq_experiment_runs_artifact_run"
        ),
        ForeignKeyConstraint(
            ["artifact_id", "project_id"],
            ["artifacts.id", "artifacts.project_id"],
            name="fk_experiment_runs_artifact_project",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_id: Mapped[UUID] = mapped_column(nullable=False)
    run_id: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    split_id: Mapped[str] = mapped_column(Text, nullable=False)
    protocol_version: Mapped[str] = mapped_column(Text, nullable=False)
    metric_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_unit: Mapped[str] = mapped_column(Text, nullable=False)
    optimization: Mapped[str] = mapped_column(String(3), nullable=False)
    metric_value: Mapped[str] = mapped_column(Text, nullable=False)
    seed: Mapped[str] = mapped_column(Text, nullable=False)
    source_line: Mapped[int] = mapped_column(Integer, nullable=False)
