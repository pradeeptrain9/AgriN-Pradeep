import uuid
from datetime import date, datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    phone: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str | None] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text, default="en")
    country: Mapped[str] = mapped_column(Text, default="IN")
    role: Mapped[str] = mapped_column(Text, default="farmer")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    phone: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Field(Base):
    __tablename__ = "fields"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    geom = mapped_column(Geography("POLYGON", srid=4326))
    centroid = mapped_column(Geography("POINT", srid=4326))
    area_ha: Mapped[float] = mapped_column(Float)
    country: Mapped[str | None] = mapped_column(Text)
    admin1: Mapped[str | None] = mapped_column(Text)
    admin2: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CropCycle(Base):
    __tablename__ = "crop_cycles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    field_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fields.id", ondelete="CASCADE"))
    crop_code: Mapped[str] = mapped_column(Text)
    variety: Mapped[str | None] = mapped_column(Text)
    sowing_date: Mapped[date] = mapped_column(Date)
    harvest_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, default="active")
    previous_crop: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SoilProfile(Base):
    __tablename__ = "soil_profiles"

    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fields.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, default="soilgrids-v2")
    props: Mapped[dict] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Observation(Base):
    __tablename__ = "observations"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fields.id", ondelete="CASCADE"), primary_key=True
    )
    index_name: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    valid_fraction: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(Text, default="sentinel2-l2a")


class WeatherDaily(Base):
    __tablename__ = "weather_daily"

    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    field_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fields.id", ondelete="CASCADE"), primary_key=True
    )
    tmax_c: Mapped[float | None] = mapped_column(Float)
    tmin_c: Mapped[float | None] = mapped_column(Float)
    tmean_c: Mapped[float | None] = mapped_column(Float)
    rh_mean: Mapped[float | None] = mapped_column(Float)
    wind2_ms: Mapped[float | None] = mapped_column(Float)
    rad_mj: Mapped[float | None] = mapped_column(Float)
    precip_mm: Mapped[float | None] = mapped_column(Float)
    et0_mm: Mapped[float | None] = mapped_column(Float)
    soil_moist: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(Text, default="open-meteo")


class Advisory(Base):
    __tablename__ = "advisories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    field_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fields.id", ondelete="CASCADE"))
    crop_cycle_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("crop_cycles.id"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    engine_version: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB)
    narration: Mapped[dict | None] = mapped_column(JSONB)
    lang: Mapped[str] = mapped_column(Text, default="en")


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    field_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fields.id"))
    image_path: Mapped[str | None] = mapped_column(Text)
    on_device_label: Mapped[str | None] = mapped_column(Text)
    on_device_conf: Mapped[float | None] = mapped_column(Float)
    on_device_entropy: Mapped[float | None] = mapped_column(Float)
    server_label: Mapped[str | None] = mapped_column(Text)
    server_conf: Mapped[float | None] = mapped_column(Float)
    resolved_by: Mapped[str] = mapped_column(Text)
    treatment: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Pesticide(Base):
    __tablename__ = "pesticides"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    country: Mapped[str] = mapped_column(Text)
    crop_code: Mapped[str] = mapped_column(Text)
    disease_code: Mapped[str] = mapped_column(Text)
    active_ingredient: Mapped[str] = mapped_column(Text)
    product_name: Mapped[str | None] = mapped_column(Text)
    dose: Mapped[str] = mapped_column(Text)
    phi_days: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)


class PuLedger(Base):
    __tablename__ = "pu_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[date] = mapped_column(Date)
    units: Mapped[float] = mapped_column(Float)
    endpoint: Mapped[str] = mapped_column(Text)
    field_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
