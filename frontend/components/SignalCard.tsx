'use client';

import { useState } from 'react';
import type { Judgment } from '@/lib/types';

interface Props { judgment: Judgment }

const DIR = {
  up:      { arrow: '↑', label: 'UP',      ring: 'border-green-500/30', badge: 'bg-green-500/10 text-green-400' },
  down:    { arrow: '↓', label: 'DOWN',    ring: 'border-red-500/30',   badge: 'bg-red-500/10   text-red-400'   },
  neutral: { arrow: '→', label: 'NEUTRAL', ring: 'border-gray-600/30',  badge: 'bg-gray-500/10  text-gray-400'  },
} as const;

export default function SignalCard({ judgment }: Props) {
  const [open, setOpen] = useState(false);
  const cfg = DIR[judgment.direction];

  const divPct = `${judgment.divergence > 0 ? '+' : ''}${(judgment.divergence * 100).toFixed(1)}%`;
  const divColor =
    judgment.divergence > 0.05  ? 'text-green-400'
    : judgment.divergence < -0.05 ? 'text-red-400'
    :                               'text-gray-400';

  return (
    <div className={`rounded-xl border ${cfg.ring} bg-[#1A1D23] p-5`}>
      <div className="flex items-start gap-4">
        {/* Direction badge */}
        <div className={`shrink-0 w-11 h-11 rounded-lg flex items-center justify-center text-lg font-bold ${cfg.badge}`}>
          {cfg.arrow}
        </div>

        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-100 line-clamp-2">{judgment.question}</p>

          {/* Metrics */}
          <div className="flex flex-wrap gap-5 mt-3">
            <Metric label="Market" value={`${(judgment.market_price_at_call * 100).toFixed(0)}%`} />
            <Metric
              label="Agent range"
              value={`${(judgment.confidence_low * 100).toFixed(0)}–${(judgment.confidence_high * 100).toFixed(0)}%`}
            />
            <Metric label="Divergence" value={divPct} valueClass={divColor} />
            <div className="ml-auto text-right">
              <div className="text-xs text-gray-600">{judgment.age}</div>
              <div className="text-xs text-gray-700 mt-0.5">{judgment.headline_count} headlines</div>
            </div>
          </div>
        </div>
      </div>

      <button
        className="mt-3 text-xs text-gray-600 hover:text-gray-400 transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '▲ hide' : '▼ rationale & sources'}
      </button>

      {open && (
        <div className="mt-3 pt-3 border-t border-gray-800 space-y-3">
          <p className="text-sm text-gray-300 leading-relaxed">{judgment.rationale}</p>

          {!judgment.was_sufficient && (
            <div className="text-xs text-yellow-500 bg-yellow-500/10 px-3 py-2 rounded-lg">
              ⚠ Agent flagged insufficient evidence
            </div>
          )}

          {judgment.cited_urls.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs text-gray-600 uppercase tracking-wider">Sources</p>
              {judgment.cited_urls.map((url, i) => (
                <a
                  key={i}
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block text-xs text-[#00D4AA] hover:underline truncate"
                >
                  {url}
                </a>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, valueClass = 'text-gray-100' }: {
  label: string; value: string; valueClass?: string;
}) {
  return (
    <div>
      <div className="text-xs text-gray-600">{label}</div>
      <div className={`text-sm font-mono font-semibold tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}
