import type { JobPage, SourceReport } from './types';

const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${base}${path}`, options);
  if (!response.ok) throw new Error(response.status === 503 ? '資料庫尚未就緒，請稍後重試。' : '無法讀取本機服務，請確認 Docker Compose 已啟動。');
  return response.status === 204 ? undefined as T : response.json();
}
export const fetchJobs = (params: URLSearchParams, signal: AbortSignal) => request<JobPage>(`/jobs?${params}`, {signal});
export const fetchBookmarks = (page: number, signal: AbortSignal) => request<JobPage>(`/bookmarks?page=${page}`, {signal});
export const fetchSources = (signal: AbortSignal) => request<SourceReport>('/sources', {signal});
export const setBookmark = (id: number, saved: boolean) => request<void>(`/bookmarks/${id}`, {method: saved ? 'PUT' : 'DELETE'});
