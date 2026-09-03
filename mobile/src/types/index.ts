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

export interface Field {
  id: string;
  name: string;
  area_ha: number;
  centroid: [number, number];
  geometry: GeoJsonPolygon;
  created_at: string;
  crop?: CropCycle | null;
  soil?: SoilProfile | null;
}

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
  source: 'claude' | 'template';
  translated: boolean;
  notes: string[];
}

export interface DiseasePrediction { class_code: string; probability: number }

export interface Diagnosis {
  id: string;
  resolved_by: 'on_device' | 'claude_vision' | 'inconclusive';
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
