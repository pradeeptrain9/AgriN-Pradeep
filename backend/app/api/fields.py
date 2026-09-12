"""Field registry: polygons, crop cycles and soil entry."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.providers.sentinel import polygon_area_ha
from app.providers.soil import from_feel_test, from_soil_health_card
from app.schemas import (
    CropCycleCreate,
    FeelTestCreate,
    FieldCreate,
    FieldOut,
    SoilCardCreate,
)
from app.security import CurrentUser, current_user

router = APIRouter(prefix="/fields", tags=["fields"])

# Sentinel-2 is 10 m; below roughly 0.1 ha a field is a handful of pixels and
# after the 10 m boundary erosion there is nothing left to sample.
MIN_FIELD_AREA_HA = 0.1
MAX_FIELD_AREA_HA = 5000.0


async def _load_field(db: AsyncSession, field_id: str, user_id: str) -> dict:
    result = await db.execute(
        text(
            "SELECT id, name, area_ha, ST_AsGeoJSON(geom::geometry) AS geometry, "
            "ST_X(centroid::geometry) AS lon, ST_Y(centroid::geometry) AS lat, "
            "created_at, source "
            "FROM fields WHERE id = :id AND user_id = :user_id AND archived_at IS NULL"
        ),
        {"id": field_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail="Field not found")
    return dict(row)


async def _attach_crop_and_soil(db: AsyncSession, field: dict) -> dict:
    crop = await db.execute(
        text(
            "SELECT id, crop_code, variety, sowing_date, previous_crop, status "
            "FROM crop_cycles WHERE field_id = :id AND status = 'active' "
            "ORDER BY sowing_date DESC LIMIT 1"
        ),
        {"id": field["id"]},
    )
    crop_row = crop.mappings().first()

    soil = await db.execute(
        text("SELECT props, source, fetched_at FROM soil_profiles WHERE field_id = :id"),
        {"id": field["id"]},
    )
    soil_row = soil.mappings().first()

    return {
        **field,
        "crop": dict(crop_row) if crop_row else None,
        "soil": soil_row["props"] if soil_row else None,
    }


@router.post("", response_model=FieldOut, status_code=status.HTTP_201_CREATED)
async def create_field(
    payload: FieldCreate,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> FieldOut:
    geometry = payload.geometry.model_dump()

    area = polygon_area_ha(geometry)
    if area < MIN_FIELD_AREA_HA:
        raise HTTPException(
            status_code=422,
            detail=(
                f"This field is {area:.2f} ha. Satellite pixels are 10 m across, so "
                f"fields under {MIN_FIELD_AREA_HA} ha cannot be monitored reliably."
            ),
        )
    if area > MAX_FIELD_AREA_HA:
        raise HTTPException(status_code=422, detail="Field is implausibly large")

    field_id = uuid.uuid4()
    geojson = json.dumps(geometry)
    await db.execute(
        text(
            "INSERT INTO fields (id, user_id, name, geom, centroid, area_ha, source) VALUES ("
            "  :id, :user_id, :name,"
            "  ST_GeomFromGeoJSON(:geojson)::geography,"
            "  ST_Centroid(ST_GeomFromGeoJSON(:geojson))::geography,"
            "  ST_Area(ST_GeomFromGeoJSON(:geojson)::geography) / 10000.0,"
            "  :source)"
        ),
        {
            "id": field_id,
            "user_id": user.user_id,
            "name": payload.name,
            "geojson": geojson,
            "source": payload.source,
        },
    )
    await db.commit()

    field = await _load_field(db, str(field_id), user.user_id)
    return _to_out(await _attach_crop_and_soil(db, field))


def _to_out(field: dict) -> FieldOut:
    return FieldOut(
        id=str(field["id"]),
        name=field["name"],
        area_ha=round(field["area_ha"], 3),
        centroid=[field["lon"], field["lat"]],
        geometry=json.loads(field["geometry"]),
        created_at=field["created_at"],
        source=field.get("source") or "walked",
        crop=_json_safe(field.get("crop")),
        soil=field.get("soil"),
    )


def _json_safe(value: dict | None) -> dict | None:
    if value is None:
        return None
    return {k: (str(v) if hasattr(v, "hex") else v) for k, v in value.items()}


@router.get("", response_model=list[FieldOut])
async def list_fields(
    user: CurrentUser = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> list[FieldOut]:
    result = await db.execute(
        text(
            "SELECT id, name, area_ha, ST_AsGeoJSON(geom::geometry) AS geometry, "
            "ST_X(centroid::geometry) AS lon, ST_Y(centroid::geometry) AS lat, "
            "created_at, source "
            "FROM fields WHERE user_id = :user_id AND archived_at IS NULL "
            "ORDER BY created_at DESC"
        ),
        {"user_id": user.user_id},
    )
    fields = [dict(row) for row in result.mappings()]
    return [_to_out(await _attach_crop_and_soil(db, f)) for f in fields]


@router.get("/{field_id}", response_model=FieldOut)
async def get_field(
    field_id: str,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> FieldOut:
    field = await _load_field(db, field_id, user.user_id)
    return _to_out(await _attach_crop_and_soil(db, field))


@router.delete("/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_field(
    field_id: str,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _load_field(db, field_id, user.user_id)
    await db.execute(
        text("UPDATE fields SET archived_at = now() WHERE id = :id"), {"id": field_id}
    )
    await db.commit()


@router.post("/{field_id}/crop", status_code=status.HTTP_201_CREATED)
async def set_crop_cycle(
    field_id: str,
    payload: CropCycleCreate,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.engine.crops import get_crop

    await _load_field(db, field_id, user.user_id)
    try:
        crop = get_crop(payload.crop_code)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    previous_code = None
    if payload.previous_crop:
        try:
            previous_code = get_crop(payload.previous_crop).code
        except KeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Only one active cycle per field.
    await db.execute(
        text(
            "UPDATE crop_cycles SET status = 'harvested' "
            "WHERE field_id = :id AND status = 'active'"
        ),
        {"id": field_id},
    )
    cycle_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO crop_cycles (id, field_id, crop_code, variety, sowing_date, "
            "previous_crop) VALUES (:id, :field_id, :crop_code, :variety, :sowing, :prev)"
        ),
        {
            "id": cycle_id,
            "field_id": field_id,
            "crop_code": crop.code,
            "variety": payload.variety,
            "sowing": payload.sowing_date,
            "prev": previous_code,
        },
    )
    await db.commit()
    return {
        "id": str(cycle_id),
        "crop_code": crop.code,
        "label": crop.label_en,
        "sowing_date": payload.sowing_date.isoformat(),
        "expected_season_days": crop.season_days,
    }


async def _store_soil(db: AsyncSession, field_id: str, profile) -> dict:
    props = profile.to_dict()
    await db.execute(
        text(
            "INSERT INTO soil_profiles (field_id, source, props) "
            "VALUES (:id, :source, CAST(:props AS jsonb)) "
            "ON CONFLICT (field_id) DO UPDATE SET source = EXCLUDED.source, "
            "props = EXCLUDED.props, fetched_at = now()"
        ),
        {"id": field_id, "source": profile.source.value, "props": json.dumps(props)},
    )
    await db.commit()
    return props


@router.put("/{field_id}/soil/card")
async def set_soil_card(
    field_id: str,
    payload: SoilCardCreate,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _load_field(db, field_id, user.user_id)
    profile = from_soil_health_card(**payload.model_dump())
    return await _store_soil(db, field_id, profile)


@router.put("/{field_id}/soil/feel-test")
async def set_soil_feel_test(
    field_id: str,
    payload: FeelTestCreate,
    user: CurrentUser = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    await _load_field(db, field_id, user.user_id)
    try:
        profile = from_feel_test(payload.answer)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _store_soil(db, field_id, profile)
