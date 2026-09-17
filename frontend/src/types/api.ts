export type TrainRun = { id: string; train_number: string; service_date: string; origin_station_id: string; destination_station_id: string };
export type Conflict = { id: string; conflict_type: string; train_run_a: string; train_run_b: string; resource_type: string; resource_id: string; start_time_s: number; end_time_s: number; severity: string };
export type Block = { id: string; name: string; length_m: number; block_type: string };
export type Edge = { id: string; coordinates: [number, number][]; railway_type: string | null; service: string | null; mode: string };
export type Station = { id: string; name: string; lon: number; lat: number; code?: string };
