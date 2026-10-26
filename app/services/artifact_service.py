import logging
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT
from app.errors import AppError
from app.models.artifact import Artifact
from app.models.experiment import ExperimentRun
from app.models.project import Project
from tools.compare_runs import REQUIRED, ComparisonError, parse_runs

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_RUNS = 10_000
ARTIFACT_ROOT = PROJECT_ROOT / ".runtime" / "artifacts"
logger = logging.getLogger(__name__)


def require_project(db: Session, project_id: str) -> None:
    if db.get(Project, project_id) is None:
        raise AppError(404, "PROJECT_NOT_FOUND", "项目不存在，请重新选择项目")


def get_artifact(db: Session, project_id: str, artifact_id: UUID) -> Artifact:
    require_project(db, project_id)
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id, Artifact.project_id == project_id
        )
    )
    if artifact is None:
        raise AppError(404, "ARTIFACT_NOT_FOUND", "当前项目中找不到这个文件")
    return artifact


def _find_by_hash(db: Session, project_id: str, sha256: str) -> Artifact | None:
    return db.scalar(
        select(Artifact).where(
            Artifact.project_id == project_id, Artifact.sha256 == sha256
        )
    )


def _remove_uncommitted_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not clean up uncommitted artifact %s", path.name)


def upload_artifact(
    db: Session, project_id: str, filename: str, raw: bytes
) -> tuple[Artifact, bool]:
    require_project(db, project_id)
    original_name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not original_name.lower().endswith(".csv") or len(original_name) > 255:
        raise AppError(
            422, "INVALID_FILENAME", "请选择文件名不超过 255 个字符的 .csv 文件"
        )
    if "\x00" in original_name:
        raise AppError(422, "INVALID_FILENAME", "文件名包含无效字符")
    if len(raw) > MAX_FILE_BYTES:
        raise AppError(413, "FILE_TOO_LARGE", "CSV 文件不能超过 5 MiB")

    try:
        runs, content_hash = parse_runs(raw)
    except ComparisonError as exc:
        details = {key: value for key, value in exc.details.items() if key != "reason"}
        message = str(exc)
        if "line" in details:
            message = f"第 {details['line']} 行：{message}"
        raise AppError(422, exc.code, message, details) from None

    if not runs:
        raise AppError(422, "EMPTY_CSV", "CSV 至少需要包含一条实验记录")
    if len(runs) > MAX_RUNS:
        raise AppError(422, "TOO_MANY_RUNS", "一个 CSV 最多支持 10000 条实验记录")
    for run in runs.values():
        if run["project_id"] != project_id:
            raise AppError(
                422,
                "PROJECT_MISMATCH",
                f"第 {run['source_line']} 行的 project_id 与当前项目 {project_id} 不一致",
                {"line": run["source_line"], "expected_project_id": project_id},
            )

    existing = _find_by_hash(db, project_id, content_hash)
    if existing is not None:
        return existing, True

    artifact_id = uuid4()
    storage_key = f"{artifact_id.hex}.csv"
    path = ARTIFACT_ROOT / storage_key
    artifact = Artifact(
        id=artifact_id,
        project_id=project_id,
        original_name=original_name,
        sha256=content_hash,
        storage_key=storage_key,
        size_bytes=len(raw),
        row_count=len(runs),
    )
    commit_started = False
    file_created = False
    try:
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stored:
            file_created = True
            stored.write(raw)

        db.add(artifact)
        db.flush()
        db.execute(
            insert(ExperimentRun),
            [
                {
                    **{key: run[key] for key in REQUIRED},
                    "id": uuid4(),
                    "artifact_id": artifact_id,
                    "project_id": project_id,
                    "source_line": run["source_line"],
                }
                for run in runs.values()
            ],
        )
        commit_started = True
        db.commit()
    except IntegrityError:
        db.rollback()
        if file_created:
            _remove_uncommitted_file(path)
        existing = _find_by_hash(db, project_id, content_hash)
        if existing is not None:
            return existing, True
        raise
    except OSError:
        db.rollback()
        if file_created:
            _remove_uncommitted_file(path)
        raise AppError(
            500, "FILE_STORAGE_ERROR", "无法保存文件，请检查项目所在磁盘空间和目录权限"
        ) from None
    except Exception:
        db.rollback()
        # A lost connection during commit may have committed the database row.
        # Preserve the file when the transaction outcome cannot be determined.
        if file_created and not commit_started:
            _remove_uncommitted_file(path)
        raise

    db.refresh(artifact)
    return artifact, False


def list_artifacts(db: Session, project_id: str) -> list[Artifact]:
    require_project(db, project_id)
    return list(
        db.scalars(
            select(Artifact)
            .where(Artifact.project_id == project_id)
            .order_by(Artifact.created_at.desc(), Artifact.id.asc())
        ).all()
    )


def list_runs(db: Session, project_id: str, artifact_id: UUID) -> list[ExperimentRun]:
    get_artifact(db, project_id, artifact_id)
    return list(
        db.scalars(
            select(ExperimentRun)
            .where(
                ExperimentRun.artifact_id == artifact_id,
                ExperimentRun.project_id == project_id,
            )
            .order_by(ExperimentRun.source_line.asc())
        ).all()
    )
