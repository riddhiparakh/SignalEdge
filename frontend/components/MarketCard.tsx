'use client';

import { useState } from 'react';
import PriceChart from './PriceChart';
import { fetchMarketPrices } from '@/lib/api';
import type { Market, PricePoint } from '@/lib/types';

interface Props { market: Market }

export default function MarketCard({ market }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [prices, setPrices]     = useState<PricePoint[]>([]);
  const [loading, setLoading]   = useState(false);

  const toggle = async () => {
    if (!expanded && prices.length === 0) {
      setLoading(true);
      try { setPrices(await fetchMarketPrices(market.id)); }
      finally { setLoading(false); }
    }
    setExpanded((v) => !v);
  };

  const yesPrice = market.yes_price ?? null;
  const yesPct   = yesPrice !== null ? `${(yesPrice * 100).toFixed(0)}%` : '—';
  const volume   = market.volume
    ? `$${market.volume.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
    : '—';
  const priceColor =
    yesPrice === null   ? 'text-gray-400'
    : yesPrice > 0.6   ? 'text-green-400'
    : yesPrice < 0.4   ? 'text-red-400'
    :                    'text-yellow-400';

  return (
    <div
      className="bg-[#1A1D23] rounded-xl p-5 border border-gray-800 hover:border-gray-600 transition-colors cursor-pointer select-none"
      onClick={toggle}
    >
      <div className="flex items-start gap-4">
        <div className="flex-1 min-w-0">
          <p className="text-sm text-gray-100 font-medium leading-snug line-clamp-2">
            {market.question}
          </p>
          <div className="flex flex-wrap items-center gap-2 mt-2">
            {market.category && (
              <span className="text-xs text-[#00D4AA] bg-[#00D4AA]/10 px-2 py-0.5 rounded-full">
                {market.category}
              </span>
            )}
            {market.end_date && (
              <span className="text-xs text-gray-600">Resolves {market.end_date}</span>
            )}
          </div>
        </div>

        <div className="text-right shrink-0">
          <div className={`text-2xl font-bold tabular-nums ${priceColor}`}>{yesPct}</div>
          <div className="text-xs text-gray-600 mt-0.5">YES</div>
        </div>
      </div>

      <div className="flex items-center mt-3 pt-3 border-t border-gray-800 text-xs text-gray-600">
        <span>Vol {volume}</span>
        <span className="ml-auto">{expanded ? '▲ hide' : '▼ price history'}</span>
      </div>

      {expanded && (
        <div className="mt-4" onClick={(e) => e.stopPropagation()}>
          {loading ? (
            <div className="h-32 flex items-center justify-center text-gray-600 text-sm">
              Loading...
            </div>
          ) : prices.length >= 2 ? (
            <PriceChart data={prices} />
          ) : (
            <div className="h-24 flex items-center justify-center text-gray-600 text-sm">
              Run the pipeline a few times to build price history
            </div>
          )}
        </div>
      )}
    </div>
  );
}
