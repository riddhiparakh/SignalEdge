export interface Market {
  id: string;
  question: string;
  category: string | null;
  end_date: string | null;
  yes_price: number | null;
  no_price: number | null;
  volume: number | null;
  fetched_at: string;
}

export interface PricePoint {
  yes_price: number;
  fetched_at: string;
}

export interface Judgment {
  id: number;
  market_id: string;
  question: string;
  direction: 'up' | 'down' | 'neutral';
  confidence_low: number;
  confidence_high: number;
  divergence: number;
  rationale: string;
  cited_urls: string[];
  market_price_at_call: number;
  headline_count: number;
  was_sufficient: boolean;
  created_at: string;
  age: string;
}

export interface Article {
  id: string;
  title: string;
  description: string;
  url: string;
  source: string;
  published_at: string;
  age: string;
}

export interface ResearchResponse {
  articles: Article[];
  sensitive: boolean;
  query: string;
}

export interface DirectionStats {
  graded: number;
  correct: number;
  hit_rate: number | null;
}

export interface TrackRecord {
  total_scored: number;
  total_graded: number;
  correct: number;
  hit_rate: number | null;
  by_direction: { up: DirectionStats; down: DirectionStats };
  sample_size_warning: boolean;
  sample_size_note: string;
}
