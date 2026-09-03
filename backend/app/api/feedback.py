"""Farmer feedback and grievances.

Two purposes, and the second is not a bonus:

1. Recourse. A farmer told to spray the wrong thing, or told their healthy crop
   is diseased, needs somewhere to say so and needs someone to see it. A
   `harmful` report is surfaced for human triage rather than counted in a chart.

2. Ground truth. A farmer correcting a diagnosis supplies a real field label,
   which is precisely what this project cannot otherwise obtain -- every dataset
   available is somebody else's collection. Corrections accumulate into the only
   locally-representative evaluation set a node will ever have.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.disease_taxonomy import DISEASES
from app.db.session import get_db
from app.security import CurrentUser, current_user

router = APIRouter(prefix="/feedback", tags=["feedback"])

VERDICTS = ("helpful", "unclear", "wrong", "harmful")


class FeedbackCreate(BaseModel):
    kind: str = Field(pattern="^(advisory|diagnosis)$")
    verdict: str = Field(pattern="^(helpful|unclear|wrong|harmful)$")
    field_id: str | None = None
    diagnosis_id: str | None = None
    corrected_label: str | None = None
    comment: str | None = Field(default=None, max_length=2000)


@router.post("", status_code=status.HTTP_201_CREATED)
async def submit(
    payload: FeedbackCreate,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    # A report of harm is never refused for a missing reference. Someone saying
    # they lost part of a crop must always get through -- requiring them to
    # first identify which field or which diagnosis is exactly the friction that
    # stops the reports that matter most from ever arriving.
    if payload.verdict != "harmful":
        if payload.kind == "diagnosis" and not payload.diagnosis_id:
            raise HTTPException(status_code=422, detail="diagnosis_id is required")
        if payload.kind == "advisory" and not payload.field_id:
            raise HTTPException(status_code=422, detail="field_id is required")

    # A correction is only useful if it names a class the model can predict.
    # Dropped rather than refused on a harm report, so a bad label cannot block
    # the report itself.
    corrected = payload.corrected_label
    if corrected and corrected not in DISEASES:
        if payload.verdict == "harmful":
            corrected = None
        else:
            raise HTTPException(
                status_code=422,
                detail="corrected_label must be a known disease class, or omitted",
            )

    feedback_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO feedback (id, user_id, kind, field_id, diagnosis_id, "
            "  verdict, corrected_label, comment) "
            "VALUES (:id, :user_id, :kind, :field_id, :diagnosis_id, :verdict, "
            "  :corrected, :comment)"
        ),
        {
            "id": feedback_id, "user_id": user.user_id, "kind": payload.kind,
            "field_id": payload.field_id, "diagnosis_id": payload.diagnosis_id,
            "verdict": payload.verdict, "corrected": corrected,
            "comment": payload.comment,
        },
    )
    await db.commit()

    urgent = payload.verdict == "harmful"
    return {
        "id": str(feedback_id),
        "recorded": True,
        "escalated": urgent,
        "message": (
            "Thank you. This has been flagged for someone to look at, and an "
            "extension officer should contact you."
            if urgent else
            "Thank you. This helps make the advice better for everyone here."
        ),
    }


@router.get("/summary")
async def summary(
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Operator view. Harmful and wrong reports are listed individually because
    a count is not an answer to somebody losing a crop."""
    if user.role not in ("extension", "admin"):
        raise HTTPException(
            status_code=403,
            detail="Only extension officers and administrators can read this.",
        )

    counts = await db.execute(text(
        "SELECT kind, verdict, count(*) AS n FROM feedback GROUP BY kind, verdict"))
    tally: dict[str, dict[str, int]] = {}
    for row in counts.mappings():
        tally.setdefault(row["kind"], {})[row["verdict"]] = row["n"]

    urgent = await db.execute(text(
        "SELECT id, kind, verdict, corrected_label, comment, created_at "
        "FROM feedback WHERE verdict IN ('wrong','harmful') "
        "  AND acknowledged_at IS NULL ORDER BY "
        "  CASE verdict WHEN 'harmful' THEN 0 ELSE 1 END, created_at DESC LIMIT 50"))

    # Corrections are the labelled data. Surface them as such.
    corrections = await db.scalar(text(
        "SELECT count(*) FROM feedback WHERE corrected_label IS NOT NULL")) or 0

    return {
        "counts": tally,
        "needs_attention": [
            {
                "id": str(row["id"]), "kind": row["kind"], "verdict": row["verdict"],
                "corrected_label": row["corrected_label"], "comment": row["comment"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in urgent.mappings()
        ],
        "field_corrections_collected": corrections,
        "note": (
            "Corrections are ground-truth labels from real fields. They are the "
            "only locally-representative evaluation data this node can obtain."
        ),
    }


@router.post("/{feedback_id}/acknowledge")
async def acknowledge(
    feedback_id: str,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if user.role not in ("extension", "admin"):
        raise HTTPException(status_code=403, detail="Not permitted")
    result = await db.execute(
        text("UPDATE feedback SET acknowledged_at = now(), acknowledged_by = :by "
             "WHERE id = :id AND acknowledged_at IS NULL RETURNING id"),
        {"id": feedback_id, "by": user.user_id},
    )
    if result.first() is None:
        raise HTTPException(status_code=404, detail="Not found or already acknowledged")
    await db.commit()
    return {"acknowledged": True}
