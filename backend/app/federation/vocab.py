"""Shared vocabulary for the AgriN network.

Interoperability fails on vocabulary long before it fails on transport. Two
nodes can both speak JSON over HTTPS and still be unable to exchange anything
useful if one says "wheat_spring" and the other says "trigo" or "4". So every
crop, unit and indicator this network exchanges is bound to an identifier that
exists outside AgriN:

  crops       AGROVOC concept URIs (FAO's multilingual agricultural thesaurus)
  units       UCUM codes, the same set used in health and lab data exchange
  geometry    GeoJSON, RFC 7946
  time        RFC 3339 timestamps, UTC

AGROVOC matters especially: it is already multilingual, so a Brazilian node can
resolve the same concept in Portuguese without AgriN shipping a translation
table. Codes below are AGROVOC concept identifiers; a node that cannot resolve
one must pass it through untouched rather than guessing.
"""

from dataclasses import dataclass

AGROVOC_BASE = "http://aims.fao.org/aos/agrovoc/"


@dataclass(frozen=True)
class CropConcept:
    crop_code: str
    agrovoc_id: str
    label_en: str

    @property
    def uri(self) -> str:
        return f"{AGROVOC_BASE}{self.agrovoc_id}"


# AGROVOC concept ids for the crops in engine/crops.py.
CROP_CONCEPTS: dict[str, CropConcept] = {
    "wheat_spring": CropConcept("wheat_spring", "c_8373", "wheat"),
    "wheat_winter": CropConcept("wheat_winter", "c_8373", "wheat"),
    "rice": CropConcept("rice", "c_6599", "rice"),
    "maize": CropConcept("maize", "c_12332", "maize"),
    "cotton": CropConcept("cotton", "c_1926", "cotton"),
    "soybean": CropConcept("soybean", "c_7247", "soybeans"),
    "chickpea": CropConcept("chickpea", "c_1591", "chickpeas"),
    "groundnut": CropConcept("groundnut", "c_1123", "groundnuts"),
    "potato": CropConcept("potato", "c_13551", "potatoes"),
    "sorghum": CropConcept("sorghum", "c_7247", "sorghum"),
    "pearl_millet": CropConcept("pearl_millet", "c_4837", "pearl millet"),
    "sunflower": CropConcept("sunflower", "c_7500", "sunflowers"),
    "mustard": CropConcept("mustard", "c_4982", "mustard"),
    "sugarcane": CropConcept("sugarcane", "c_7501", "sugarcane"),
}

# UCUM codes. Spelling these out stops the classic failure where one node reads
# a depth in millimetres and another writes it in centimetres.
UNITS = {
    "irrigation_depth": "mm",
    "rainfall": "mm",
    "et0": "mm/d",
    "nutrient_rate": "kg/ha",
    "area": "ha",
    "yield": "t/ha",
    "temperature": "Cel",
    "percolation": "mm/d",
    "ndvi": "1",          # dimensionless index
}

# Indicators a node may publish in an aggregate exchange.
INDICATORS = {
    "ndvi_anomaly_mean": {
        "unit": "1",
        "description": "Mean NDVI residual against the expected curve for the crop stage.",
    },
    "irrigation_depth_mean": {
        "unit": "mm",
        "description": "Mean recommended net irrigation depth.",
    },
    "disease_incidence": {
        "unit": "1",
        "description": "Share of diagnoses returning a given disease class.",
    },
    "water_saving_awd_pct": {
        "unit": "%",
        "description": "Mean irrigation water saved by AWD against continuous flooding.",
    },
}


def crop_uri(crop_code: str) -> str | None:
    concept = CROP_CONCEPTS.get(crop_code)
    return concept.uri if concept else None


def describe_crop(crop_code: str) -> dict:
    concept = CROP_CONCEPTS.get(crop_code)
    if concept is None:
        # Unknown to this node's vocabulary: pass through rather than guess.
        return {"crop_code": crop_code, "agrovoc_uri": None, "resolved": False}
    return {
        "crop_code": crop_code,
        "agrovoc_uri": concept.uri,
        "label_en": concept.label_en,
        "resolved": True,
    }
