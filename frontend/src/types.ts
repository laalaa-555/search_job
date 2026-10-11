export const sourceNames: Record<string, string> = { '104': '104', '1111': '1111', cake: 'Cake', linkedin: 'LinkedIn', indeed: 'Indeed' };
export const allSources = Object.keys(sourceNames);
export interface Job {
  id: number; source: string; title: string; company: string; location: string; location_city: string;
  url: string; skills: string[]; salary_text: string; salary_currency: string | null; salary_period: string | null;
  salary_min: number | null; salary_max: number | null; annual_salary_min: number | null; annual_salary_max: number | null;
  salary_estimated: boolean; is_active: boolean; bookmarked: boolean; stale: boolean;
  last_seen_at: string; updated_at: string;
  detail_status: string; detail_collected_at: string | null;
}
export interface JobPage { items: Job[]; total: number; page: number; page_size: number }
export interface Source {
  source: string; label: string; enabled: boolean; status: string; last_started_at: string | null;
  last_success_at: string | null; last_finished_at: string | null; collected_count: number; stored_count: number;
  keywords_completed: number; keywords_total: number; error: string | null; truncated: boolean;
  reason_code: string | null; next_retry_at: string | null; last_data_at: string | null;
  cycle_id: number | null; cycle_started_at: string | null; cycle_completed: boolean;
  cycle_keywords_completed: number; cycle_keywords_total: number; pending_keywords: number;
  cycle_pages_collected: number; last_cycle_completed_at: string | null;
  keyword_progress: {keyword:string; next_page:number; next_offset:number; collected_count:number; completed:boolean; truncated:boolean}[];
}
export interface SourceReport {
  items: Source[]; keywords: string[]; location: string; max_results_per_keyword: number;
  schedule_timezone: string; schedule_hour: number; schedule_minute: number; worker_running: boolean;
  batch_pages_per_run: number;
}
