import type { Market, PricePoint, Judgment, ResearchResponse, TrackRecord } from './types';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, options);
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

export const fetchMarkets = () =>
  apiFetch<Market[]>('/api/markets');

export const fetchMarketPrices = (id: string) =>
  apiFetch<PricePoint[]>(`/api/markets/${id}/prices`);

export const fetchJudgments = (direction = 'all', limit = 50) =>
  apiFetch<Judgment[]>(`/api/judgments?direction=${direction}&limit=${limit}`);

export const fetchTrackRecord = () =>
  apiFetch<TrackRecord>('/api/track-record');

export const postResearch = (query: string, top_k = 8) =>
  apiFetch<ResearchResponse>('/api/research', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_k }),
  });

export const triggerPipeline = (step: 'markets' | 'news' | 'agent') =>
  apiFetch<{ status: string; count?: number; message?: string }>(
    `/api/pipeline/${step}`,
    { method: 'POST' }
  );
