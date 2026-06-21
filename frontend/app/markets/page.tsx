'use client';

import { useEffect, useState } from 'react';
import MarketCard from '@/components/MarketCard';
import { fetchMarkets } from '@/lib/api';
import type { Market } from '@/lib/types';

export default function MarketsPage() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState<string | null>(null);

  useEffect(() => {
    fetchMarkets()
      .then(setMarkets)
      .catch(() => setError('Cannot reach API. Start the FastAPI server: uvicorn api.main:app --reload'))
      .finally(() => setLoading(false));
  }, []);

  const totalVol = markets.reduce((s, m) => s + (m.volume ?? 0), 0);
  const avgPrice = markets.length
    ? markets.reduce((s, m) => s + (m.yes_price ?? 0), 0) / markets.length
    : 0;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-100">Live Markets</h1>
        <p className="text-sm text-gray-500 mt-1">
          Active Polymarket prediction markets · ordered by volume · click to expand price history
        </p>
      </div>

      {error && (
        <div className="mb-6 bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-sm text-red-400">
          {error}
        </div>
      )}

      {!loading && markets.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-8">
          {[
            { label: 'Active Markets', value: String(markets.length) },
            { label: 'Total Volume', value: `$${totalVol.toLocaleString('en-US', { maximumFractionDigits: 0 })}` },
            { label: 'Avg YES Price', value: `${(avgPrice * 100).toFixed(0)}%` },
          ].map(({ label, value }) => (
            <div key={label} className="bg-[#1A1D23] rounded-xl p-5 border border-gray-800">
              <div className="text-2xl font-bold text-gray-100 tabular-nums">{value}</div>
              <div className="text-xs text-gray-600 mt-1">{label}</div>
            </div>
          ))}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-[#1A1D23] rounded-xl h-28 border border-gray-800 animate-pulse" />
          ))}
        </div>
      ) : markets.length === 0 ? (
        <Empty
          emoji="📭"
          title="No markets yet"
          hint='Click "Fetch markets" in the sidebar to pull live Polymarket data'
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {markets.map((m) => <MarketCard key={m.id} market={m} />)}
        </div>
      )}
    </div>
  );
}

function Empty({ emoji, title, hint }: { emoji: string; title: string; hint: string }) {
  return (
    <div className="text-center py-20 text-gray-600">
      <div className="text-5xl mb-4">{emoji}</div>
      <p className="text-gray-400 font-medium">{title}</p>
      <p className="text-sm mt-2">{hint}</p>
    </div>
  );
}
