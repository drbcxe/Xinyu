from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models.comparison import ComparisonRecord
from app.schemas.comparisons import ComparisonCreate
from app.services.artifact_service import (
    ARTIFACT_ROOT,
    MAX_FILE_BYTES,
    get_artifact,
    require_project,
)
from tools.compare_runs import ComparisonError, compare_runs


def create_comparison(
    db: Session,
    project_id: str,
    data: ComparisonCreate,
    *,
    agent_run_id: UUID | None = None,
    tool_call_id: str | None = None,
) -> ComparisonRecord:
    artifact = get_artifact(db, project_id, data.artifact_id)
    if agent_run_id is not None and tool_call_id is not None:
        existing = db.scalar(
            select(ComparisonRecord).where(
                ComparisonRecord.agent_run_id == agent_run_id,
                ComparisonRecord.tool_call_id == tool_call_id,
                ComparisonRecord.project_id == project_id,
                ComparisonRecord.artifact_id == data.artifact_id,
            )
        )
        if existing is not None:
            return existing
    if data.baseline_run_id == data.candidate_run_id:
        raise AppError(422, "SAME_RUN", "请选择两个不同的实验")

    # Only a server-generated key from the scoped artifact may identify a file.
    if artifact.storage_key != f"{artifact.id.hex}.csv":
        raise AppError(409, "SOURCE_FILE_UNAVAILABLE", "文件记录异常，暂时无法进行比较")
    path = ARTIFACT_ROOT / artifact.storage_key
    try:
        if path.resolve().parent != ARTIFACT_ROOT.resolve() or not path.is_file():
            raise AppError(
                409, "SOURCE_FILE_UNAVAILABLE", "找不到原始文件，请恢复原始文件后再比较"
            )
        if (
            path.stat().st_size != artifact.size_bytes
            or path.stat().st_size > MAX_FILE_BYTES
        ):
            raise AppError(
                409,
                "SOURCE_FILE_CHANGED",
                "原始文件内容已变化，无法使用这个文件记录进行比较",
            )
        result = compare_runs(
            path, project_id, data.baseline_run_id, data.candidate_run_id
        )
    except OSError:
        raise AppError(
            409, "SOURCE_FILE_UNAVAILABLE", "无法读取原始文件，请检查文件是否存在"
        ) from None
    except ComparisonError as exc:
        if exc.code == "INVALID_FILE":
            raise AppError(
                409, "SOURCE_FILE_UNAVAILABLE", "原始 CSV 无法读取或格式已损坏"
            ) from None
        details = {key: value for key, value in exc.details.items() if key != "reason"}
        raise AppError(422, exc.code, str(exc), details) from None

    # compare_runs hashes the exact bytes used for the calculation.
    if result["provenance"]["sha256"] != artifact.sha256:
        raise AppError(
            409,
            "SOURCE_FILE_CHANGED",
            "原始文件哈希与上传记录不一致，请恢复原始文件后再比较",
        )

    result["provenance"].pop("csv_path", None)
    result["provenance"]["original_name"] = artifact.original_name
    record = ComparisonRecord(
        project_id=project_id,
        artifact_id=artifact.id,
        baseline_run_id=data.baseline_run_id,
        candidate_run_id=data.candidate_run_id,
        agent_run_id=agent_run_id,
        tool_call_id=tool_call_id,
        result=result,
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(record)
    return record


def list_comparisons(
    db: Session, project_id: str, artifact_id: UUID
) -> list[ComparisonRecord]:
    get_artifact(db, project_id, artifact_id)
    return list(
        db.scalars(
            select(ComparisonRecord)
            .where(
                ComparisonRecord.project_id == project_id,
                ComparisonRecord.artifact_id == artifact_id,
            )
            .order_by(ComparisonRecord.created_at.desc(), ComparisonRecord.id.asc())
            .limit(20)
        ).all()
    )


def get_comparison(
    db: Session, project_id: str, comparison_id: UUID
) -> ComparisonRecord:
    require_project(db, project_id)
    record = db.scalar(
        select(ComparisonRecord).where(
            ComparisonRecord.id == comparison_id,
            ComparisonRecord.project_id == project_id,
        )
    )
    if record is None:
        raise AppError(404, "COMPARISON_NOT_FOUND", "当前项目中找不到这条比较记录")
    return record
