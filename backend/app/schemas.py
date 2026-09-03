"""Request and response models.

Field geometry crosses the wire as GeoJSON (RFC 7946) rather than a bespoke
shape, because the federation contract commits to open formats.
"""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class OtpRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    lang: str = "en"
    country: str = "IN"

    @field_validator("phone")
    @classmethod
    def normalise_phone(cls, value: str) -> str:
        cleaned = value.strip().replace(" ", "").replace("-", "")
        if not cleaned.lstrip("+").isdigit():
            raise ValueError("phone must be digits, optionally prefixed with +")
        return cleaned


class OtpVerify(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=8)

    @field_validator("phone")
    @classmethod
    def normalise_phone(cls, value: str) -> str:
        return value.strip().replace(" ", "").replace("-", "")


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    is_new_user: bool


class GeoJsonPolygon(BaseModel):
    type: Literal["Polygon"]
    coordinates: list[list[list[float]]]

    @field_validator("coordinates")
    @classmethod
    def validate_ring(cls, value: list[list[list[float]]]) -> list[list[list[float]]]:
        if not value:
            raise ValueError("polygon needs at least one ring")
        ring = value[0]
        if len(ring) < 4:
            raise ValueError("a polygon ring needs at least 4 positions")
        if ring[0] != ring[-1]:
            raise ValueError("polygon ring must be closed (first position == last)")
        for lon, lat, *_ in ring:
            if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                raise ValueError(f"position out of range: {lon},{lat}")
        return value


class FieldCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    geometry: GeoJsonPolygon


class FieldOut(BaseModel):
    id: str
    name: str
    area_ha: float
    centroid: list[float]
    geometry: dict[str, Any]
    created_at: datetime
    crop: dict[str, Any] | None = None
    soil: dict[str, Any] | None = None


class CropCycleCreate(BaseModel):
    crop_code: str
    sowing_date: date
    variety: str | None = None
    previous_crop: str | None = None
    irrigation_available: bool = False
    residue_retained: bool = False


class SoilCardCreate(BaseModel):
    """Values transcribed from a government Soil Health Card."""

    ph: float | None = Field(default=None, ge=2.0, le=11.0)
    organic_carbon_pct: float | None = Field(default=None, ge=0.0, le=20.0)
    available_n_kg_ha: float | None = Field(default=None, ge=0.0)
    available_p_kg_ha: float | None = Field(default=None, ge=0.0)
    available_k_kg_ha: float | None = Field(default=None, ge=0.0)
    texture_hint: str | None = None
    sand_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    silt_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    clay_pct: float | None = Field(default=None, ge=0.0, le=100.0)


class FeelTestCreate(BaseModel):
    answer: str


class HealthResponse(BaseModel):
    status: str
    node_id: str
    country: str
    database: str
    version: str
