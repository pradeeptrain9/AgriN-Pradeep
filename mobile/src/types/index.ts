export interface Coordinate {
  latitude: number;
  longitude: number;
  timestamp: number;
  accuracy?: number;
}

export interface GeoJsonPolygon {
  type: 'Polygon';
  coordinates: number[][][];
}

/**
 * `source` is how the boundary was obtained, and it is not cosmetic. A walked
 * boundary is a survey; a drawn one is an estimate over a satellite basemap.
 * Every fertiliser and water figure the advisory produces is per hectare, so
 * the area multiplies all of them -- a drawn field must never be read back as
 * a surveyed one. Absent means walked: that is what every field created before
 * drawing existed actually is.
 */
export interface Field {
  id: string;
  name: string;
  area_ha: number;
  centroid: [number, number];
  geometry: GeoJsonPolygon;
  created_at: string;
  source?: FieldSource;
  crop?: CropCycle | null;
  soil?: SoilProfile | null;
}

export type FieldSource = 'walked' | 'drawn';

export interface CropCycle {
  id?: string;
  crop_code: string;
  variety?: string | null;
  sowing_date: string;
  previous_crop?: string | null;
  status?: string;
}

export interface SoilProfile {
  source: string;
  confidence: string;
  texture: string;
  ph?: number | null;
  organic_carbon_pct?: number | null;
  available_n_kg_ha?: number | null;
  available_p_kg_ha?: number | null;
  available_k_kg_ha?: number | null;
  notes?: string[];
}

export interface Advisory {
  version: string;
  field_id: string;
  status: string;
  generated_at: string;
  crop?: {
    code: string; label: string; sowing_date: string;
    days_after_sowing: number; stage: string; season_days: number;
  };
  soil?: SoilProfile;
  health?: CropHealth;
  irrigation?: Irrigation | null;
  nutrients?: Nutrients;
  rotation?: RotationCandidate[];
  climate?: { mean_et0_mm_day: number; rainfall_since_sowing_mm: number; weather_days_available: number };
  gaps: string[];
}

export interface CropHealth {
  severity: 'ok' | 'watch' | 'alert' | 'unknown';
  latest_ndvi: number | null;
  expected_ndvi: number | null;
  residual: number | null;
  days_since_observation: number | null;
  is_stale: boolean;
  observations_used: number;
  water_stress_flag: boolean;
  notes: string[];
}

export interface Irrigation {
  model: 'paddy' | 'upland';
  irrigate_now: boolean;
  recommended_depth_mm: number;
  gross_depth_mm: number;
  days_until_irrigation: number | null;
  forecast_irrigation_date: string | null;
  rainfall_next_7d_mm: number;
  notes: string[];
  // paddy only
  regime?: string;
  water_level_mm?: number;
  water_saving_pct?: number;
  // upland only
  soil_moisture_pct?: number;
}

export interface NutrientDose { low_kg_ha: number; high_kg_ha: number; mid_kg_ha: number }

export interface Nutrients {
  n: NutrientDose; p2o5: NutrientDose; k2o: NutrientDose;
  fertility_class: { n: string; p: string; k: string };
  products_kg_ha: Record<string, number>;
  splits: Array<{ when: string; days_after_sowing: number; n_kg_ha: number }>;
  regenerative_actions: string[];
  notes: string[];
}

export interface RotationCandidate {
  crop_code: string; label: string; score: number;
  seasonal_water_need_mm: number; reasons: string[]; warnings: string[];
}

export interface Narration {
  summary: string;
  actions: Array<{ title: string; detail: string; urgency: string }>;
  explanation: string;
  lang: string;
  source: 'gemini' | 'template';
  translated: boolean;
  notes: string[];
}

export interface DiseasePrediction { class_code: string; probability: number }

export interface Diagnosis {
  id: string;
  resolved_by: 'on_device' | 'cloud_vision' | 'inconclusive';
  disease_code: string | null;
  label: string | null;
  crop_code: string;
  confidence: number;
  is_healthy: boolean;
  urgent: boolean;
  needs_expert_review: boolean;
  ipm_actions: string[];
  chemical_options: Array<Record<string, unknown>>;
  notes: string[];
  gate: { route: string; reasons: string[] };
}

export type DeviceTier = 'low' | 'high';

/** A crop the node offers, as served by GET /crops and mirrored locally. */
export interface CropOption {
  code: string;
  label: string;
  season_days: number;
  fixes_nitrogen: boolean;
  /** Whether a leaf photograph of this crop can be diagnosed at all. */
  diagnosable?: boolean;
}

/** One day of weather for a field. `kind` says measured or predicted. */
export interface WeatherDay {
  day: string;
  kind: 'observed' | 'forecast';
  tmax_c: number | null;
  tmin_c: number | null;
  precip_mm: number | null;
  et0_mm: number | null;
}

export interface FieldWeather {
  field_id: string;
  generated_at: string;
  daily: WeatherDay[];
  rain_ahead_mm: number;
  forecast_days: number;
  gaps: string[];
}

/** One candidate crop, with the reasoning that produced its rank. */
export interface CropSuggestion {
  crop_code: string;
  label: string;
  score: number;
  components: Record<string, number>;
  seasonal_water_need_mm: number;
  reasons: string[];
  warnings: string[];
}

export interface CropSuggestions {
  version: string;
  field_id: string;
  generated_at: string;
  based_on: {
    rainfall_last_180d_mm: number;
    mean_et0_mm_day: number;
    weather_days: number;
    previous_crop: string | null;
    soil_source: string | null;
  };
  suggestions: CropSuggestion[];
  choice_is_open: boolean;
  gaps: string[];
}
