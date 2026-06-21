'use client';

import { useEffect, useState } from 'react';
import SignalCard from '@/components/SignalCard';
import { fetchJudgments } from '@/lib/api';
import type { Judgment } from '@/lib/types';

const FILTERS = [
  { label: 'All',         value: 'all'     },
  { label: '↑ Up',        value: 'up'      },
  { label: '↓ Down',      value: 'down'    },
  { label: '→ Neutral',   value: 'neutral' },
] as const;

type Filter = typeof FILTERS[number]['value'];

export default function SignalsPage() {
  const [judgments, setJudgments] = useState<Judgment[]>([]);
  const [filter, setFilter]       = useState<Filter>('all');
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchJudgments(filter)
      .then(setJudgments)
      .catch(() => setError('Cannot reach API. Start the FastAPI server.'))
      .finally(() => setLoading(false));
  }, [filter]);

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-100">Divergence Signals</h1>
        <p className="text-sm text-gray-500 mt-1">
          Agent judgments where market price may not reflect current evidence · newest first
        </p>
      </div>

      {/* Filter tabs */}
      <div className="flex gap-1 bg-[#1A1D23] rounded-xl p-1 w-fit mb-6">
        {FILTERS.map(({ label, value }) => (
          <button
            key={value}
            onClick={() => setFilter(value)}
            className={`px-4 py-1.5 rounded-lg text-sm transition-colors ${
              filter === value
                ? 'bg-[#00D4AA]/10 text-[#00D4AA] font-medium'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-6 bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="bg-[#1A1D23] rounded-xl h-32 border border-gray-800 animate-pulse" />
          ))}
        </div>
      ) : judgments.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          <div className="text-5xl mb-4">⚡</div>
          <p className="text-gray-400 font-medium">No signals yet</p>
          <p className="text-sm mt-2">
            {filter === 'all'
              ? 'Click "Run agent" in the sidebar to generate divergence signals'
              : `No ${filter} signals found`}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          <p className="text-xs text-gray-600">{judgments.length} signal(s)</p>
          {judgments.map((j) => <SignalCard key={j.id} judgment={j} />)}
        </div>
      )}
    </div>
  );
}
