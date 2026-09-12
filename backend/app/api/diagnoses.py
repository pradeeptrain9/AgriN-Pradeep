"""Crop disease diagnosis endpoints."""

import json
import pathlib
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.disease import Prediction, Route, build_diagnosis, gate
from app.ai.disease_taxonomy import get_disease
from app.ai.disease_taxonomy import DATASET_SOURCES, SUPPORTED_CROPS, classes_for_crop
from app.ai import budget
from app.ai.open_ended import identify_open_ended
from app.ai.vision import (
    VisionIdentification, identify_with_vision, prepare_image,
)
from app.config import get_settings
from app.db.session import get_db
from app.engine.crops import get_crop
from app.engine.treatments import lookup_chemicals
from app.security import CurrentUser, current_user

async def _cached_identification(
    db, image_sha256: str, crop_code: str
) -> tuple[str, float] | None:
    """A previous cloud answer for this exact photograph, if there is one.

    Only rows the cloud actually answered are reused. An earlier
    "inconclusive" is not cached: that was a refusal to answer, and re-asking
    it is a reasonable thing for a farmer to do -- it may have been a bad
    photograph of a real disease, and the budget check still gates the retry.
    """
    row = await db.execute(
        text(
            "SELECT server_label, server_conf FROM diagnoses "
            "WHERE image_sha256 = :hash AND crop_code = :crop "
            "AND server_label IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"hash": image_sha256, "crop": crop_code},
    )
    found = row.first()
    if found is None or found[0] is None:
        return None
    return str(found[0]), float(found[1] or 0.0)


router = APIRouter(prefix="/diagnoses", tags=["diagnoses"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# Magic bytes, because a Content-Type header is caller-supplied and worthless.
IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"RIFF", "webp"),
)


def _looks_like_image(raw: bytes) -> bool:
    return any(raw.startswith(sig) for sig, _ in IMAGE_SIGNATURES)


def _media_dir() -> pathlib.Path:
    path = pathlib.Path(get_settings().media_root) / "diagnoses"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _parse_predictions(raw: str | None) -> list[Prediction]:
    """Parse the on-device softmax the app sends alongside the photo.

    Shape: [{"class_code": "rice__blast", "probability": 0.94}, ...]
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422, detail="on_device_predictions must be valid JSON"
        ) from exc
    if not isinstance(parsed, list):
        raise HTTPException(status_code=422, detail="on_device_predictions must be a list")

    out: list[Prediction] = []
    for item in parsed:
        try:
            probability = float(item["probability"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="each prediction needs class_code and probability",
            ) from exc
        if not 0.0 <= probability <= 1.0:
            raise HTTPException(status_code=422, detail="probability must be 0..1")
        out.append(Prediction(class_code=str(item.get("class_code")), probability=probability))
    return out


@router.get("/classes")
async def list_classes(crop_code: str | None = None) -> dict:
    """Disease classes the on-device model covers, and what backs each crop."""
    if crop_code:
        try:
            crop = get_crop(crop_code)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        classes = classes_for_crop(crop.code)
        return {
            "crop_code": crop.code,
            "supported": crop.code in SUPPORTED_CROPS,
            "dataset": DATASET_SOURCES.get(crop.code),
            "classes": [
                {
                    "code": d.code,
                    "label": d.label_en,
                    "is_healthy": d.is_healthy,
                    "pathogen_type": d.pathogen_type,
                }
                for d in classes
            ],
        }
    return {
        "supported_crops": sorted(SUPPORTED_CROPS),
        "datasets": DATASET_SOURCES,
        "note": (
            "Crops outside this list are not covered by the on-device model and "
            "are sent to cloud diagnosis instead."
        ),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_diagnosis(
    crop_code: str = Form(...),
    image: UploadFile = File(...),
    field_id: str | None = Form(None),
    on_device_predictions: str | None = Form(None),
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Diagnose a leaf photo.

    The app runs its bundled model first and posts the softmax with the image.
    If the gate accepts that prediction the photo is never uploaded anywhere
    else; if it refuses, the photo goes to cloud diagnosis.
    """
    settings = get_settings()

    try:
        crop = get_crop(crop_code)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    raw = await image.read()
    if not raw:
        raise HTTPException(status_code=422, detail="Empty upload")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    if not _looks_like_image(raw):
        raise HTTPException(status_code=422, detail="Upload is not a JPEG, PNG or WebP image")

    predictions = _parse_predictions(on_device_predictions)

    if field_id:
        owned = await db.execute(
            text(
                "SELECT 1 FROM fields WHERE id = :id AND user_id = :user_id "
                "AND archived_at IS NULL"
            ),
            {"id": field_id, "user_id": user.user_id},
        )
        if owned.first() is None:
            raise HTTPException(status_code=404, detail="Field not found")

    # Re-encode before anything else touches it: this strips EXIF, including the
    # GPS coordinates phone cameras embed by default.
    try:
        prepared, _ = prepare_image(raw)
    except Exception as exc:  # noqa: BLE001 - malformed image data
        raise HTTPException(status_code=422, detail="Image could not be read") from exc

    # Hash the prepared bytes, so the fingerprint is stable for a given photo
    # regardless of what metadata the phone wrote.
    image_sha256 = hashlib.sha256(prepared).hexdigest()

    diagnosis_id = uuid.uuid4()
    stored = _media_dir() / f"{diagnosis_id}.jpg"
    stored.write_bytes(prepared)

    decision = gate(predictions, crop_code=crop.code)

    async def chemicals(disease_code: str) -> list[dict]:
        options = await lookup_chemicals(
            db,
            country=settings.node_country,
            crop_code=crop.code,
            disease_code=disease_code,
        )
        return [o.to_dict() for o in options]

    # Identify first, look up verified chemicals for whatever was identified,
    # and only then assemble the diagnosis. Attaching chemicals afterwards would
    # leave the "no chemical treatment is shown" note sitting next to a chemical.
    extra_notes: list[str] = []
    # Bound before the branch, because only the escalation path assigns it and
    # the assembly below reads it either way. Written as `identification.x if
    # identification else None` this looked safe and was not: the guard tests a
    # name that does not exist yet on the accepted path, so Python raises
    # UnboundLocalError before the condition is ever evaluated.
    #
    # The failure lands on the *best* outcome available -- the on-device model
    # answered confidently, for free, offline -- and turns it into a 500. The
    # farmer sees "could not check the photo" for the one photograph the node
    # handled perfectly.
    identification: VisionIdentification | None = None
    if decision.accepted and decision.top_class:
        disease = get_disease(decision.top_class)
        confidence = decision.top_probability
        resolved_by = Route.ON_DEVICE.value
    else:
        # Off the event loop. identify_with_vision uses the synchronous
        # Anthropic client and a vision call takes seconds; calling it inline
        # from an async endpoint stalls every other request on this node for
        # the duration.
        # Refuse before spending on a question with no possible answer.
        #
        # The vision prompt offers the model this crop's disease codes plus
        # "unknown". For a crop with no disease list that is a menu of one, so
        # the call can only ever return "unknown" -- and it is billed all the
        # same. Four such calls were paid for on soybean and chickpea before
        # this existed.
        #
        # The client now hides those crops, but the client is not the
        # authority: an older APK, or a photo replayed from the outbox, still
        # arrives here.
        no_disease_list = not classes_for_crop(crop.code)

        # A photograph already identified is never bought twice -- a retry after
        # "inconclusive", a double tap on a slow connection, an outbox replay.
        # Only the coded path caches: an open-ended name is not stored against a
        # disease code, so there is nothing to look it up by.
        prior = (
            None if no_disease_list
            else await _cached_identification(db, image_sha256, crop.code)
        )

        if prior is not None:
            cached_label, cached_conf = prior
            identification = VisionIdentification(
                disease_code=cached_label,
                confidence=cached_conf,
                notes=[
                    "This is the same photograph as an earlier check, so the "
                    "answer from that check is shown again.",
                    "Confirm it with your extension officer before spending "
                    "money on treatment.",
                ],
            )
            extra_notes = identification.notes
        else:
            # One budget gate in front of every path that can spend. Checked
            # before the call, not after: a cap noticed once the money is gone
            # is a report, not a cap.
            try:
                await budget.check_budget(db, user.user_id)
            except budget.BudgetReached as exc:
                identification = None
                extra_notes = [
                    str(exc),
                    "Show the plant to your extension officer before treating it.",
                ]
            else:
                if no_disease_list:
                    # No verified list for this crop, so there is no code to
                    # identify and nothing to key a treatment off. Ask for a
                    # name anyway: a farmer holding a diseased plant can carry a
                    # name to an extension officer, and silence helps nobody.
                    identification = await run_in_threadpool(
                        identify_open_ended, prepared, crop_label=crop.label_en
                    )
                else:
                    identification = await run_in_threadpool(
                        identify_with_vision,
                        prepared,
                        crop_code=crop.code,
                        crop_label=crop.label_en,
                    )
                extra_notes = identification.notes
                if identification.usage is not None and identification.model:
                    await budget.record(
                        db,
                        purpose="diagnosis",
                        model=identification.model,
                        usage=identification.usage,
                        user_id=user.user_id,
                    )

        identified = identification is not None and identification.identified
        disease = get_disease(identification.disease_code) if identified else None
        confidence = identification.confidence if identification else 0.0
        if identified:
            resolved_by = Route.CLOUD_VISION.value
        elif identification is not None and identification.provisional_name:
            resolved_by = Route.PROVISIONAL.value
        else:
            resolved_by = Route.INCONCLUSIVE.value

    options = await chemicals(disease.code) if disease is not None else []
    diagnosis = build_diagnosis(
        disease=disease,
        crop_code=crop.code,
        confidence=confidence,
        resolved_by=resolved_by,
        decision=decision,
        chemical_options=options,
        extra_notes=extra_notes,
        provisional_name=(
            identification.provisional_name if identification else None
        ),
    )

    # The highest-probability prediction, not the first one sent. The client
    # posts the full softmax in label order, so predictions[0] is merely the
    # alphabetically first class -- storing that made the audit column read
    # "bacterial_leaf_blight, 0.044" for a diagnosis the gate accepted at 0.89.
    top = max(predictions, key=lambda p: p.probability) if predictions else None
    await db.execute(
        text(
            "INSERT INTO diagnoses (id, user_id, field_id, crop_code, image_path, "
            "on_device_label, on_device_conf, on_device_entropy, server_label, "
            "server_conf, resolved_by, treatment, gate, image_sha256) VALUES "
            "(:id, :user_id, :field_id, :crop_code, :image_path, :od_label, "
            ":od_conf, :od_entropy, :srv_label, :srv_conf, :resolved_by, "
            "CAST(:treatment AS jsonb), CAST(:gate AS jsonb), :image_sha256)"
        ),
        {
            "id": diagnosis_id,
            "user_id": user.user_id,
            "field_id": field_id,
            "crop_code": crop.code,
            "image_path": str(stored),
            "image_sha256": image_sha256,
            "od_label": top.class_code if top else None,
            "od_conf": top.probability if top else None,
            "od_entropy": decision.normalised_entropy,
            "srv_label": diagnosis.disease_code
            if diagnosis.resolved_by != Route.ON_DEVICE.value
            else None,
            "srv_conf": diagnosis.confidence
            if diagnosis.resolved_by != Route.ON_DEVICE.value
            else None,
            "resolved_by": diagnosis.resolved_by,
            "treatment": json.dumps(diagnosis.to_dict()),
            "gate": json.dumps(decision.to_dict()),
        },
    )
    await db.commit()

    return {"id": str(diagnosis_id), "created_at": datetime.now(timezone.utc).isoformat(),
            **diagnosis.to_dict()}


@router.get("")
async def list_diagnoses(
    limit: int = 20,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    result = await db.execute(
        text(
            "SELECT id, field_id, crop_code, resolved_by, treatment, created_at "
            "FROM diagnoses WHERE user_id = :user_id "
            "ORDER BY created_at DESC LIMIT :limit"
        ),
        {"user_id": user.user_id, "limit": min(limit, 100)},
    )
    return [
        {
            "id": str(row["id"]),
            "field_id": str(row["field_id"]) if row["field_id"] else None,
            "crop_code": row["crop_code"],
            "resolved_by": row["resolved_by"],
            "label": (row["treatment"] or {}).get("label"),
            "urgent": (row["treatment"] or {}).get("urgent"),
            "created_at": row["created_at"].isoformat(),
        }
        for row in result.mappings()
    ]


@router.get("/{diagnosis_id}")
async def get_diagnosis(
    diagnosis_id: str,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        text(
            "SELECT id, crop_code, resolved_by, treatment, gate, created_at "
            "FROM diagnoses WHERE id = :id AND user_id = :user_id"
        ),
        {"id": diagnosis_id, "user_id": user.user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Diagnosis not found")
    return {
        "id": str(row["id"]),
        "crop_code": row["crop_code"],
        "created_at": row["created_at"].isoformat(),
        "gate": row["gate"],
        **(row["treatment"] or {}),
    }
