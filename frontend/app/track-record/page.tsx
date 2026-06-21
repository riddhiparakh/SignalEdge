'use client';

import { useEffect, useState } from 'react';
import { fetchTrackRecord } from '@/lib/api';
import type { TrackRecord } from '@/lib/types';

export default function TrackRecordPage() {
  const [record, setRecord] = useState<TrackRecord | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchTrackRecord()
      .then(setRecord)
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div>
        <PageHeader />
        <div className="grid grid-cols-3 gap-4 mt-8">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="bg-[#1A1D23] rounded-xl h-24 border border-gray-800 animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (!record) return null;

  const hitPct = record.hit_rate !== null ? `${(record.hit_rate * 100).toFixed(1)}%` : '—';

  return (
    <div>
      <PageHeader />

      {/* Sample size warning — shown first, always */}
      {record.sample_size_warning && (
        <div className="mt-6 bg-yellow-500/10 border border-yellow-500/20 rounded-xl p-4 text-sm text-yellow-400">
          ⚠️ <strong>Insufficient data for reliable statistics.</strong>
          {' '}{record.sample_size_note}
          {' '}Meaningful hit rates require at least 30 graded judgments.
        </div>
      )}

      {record.total_graded === 0 ? (
        <div className="mt-8 text-center py-16 text-gray-600">
          <div className="text-5xl mb-4">🏆</div>
          <p className="text-gray-400 font-medium">No graded judgments yet</p>
          <p className="text-sm mt-2">Markets must resolve on Polymarket before scoring is possible</p>
          <div className="mt-6 bg-[#1A1D23] rounded-xl p-5 border border-gray-800 text-left max-w-md mx-auto text-sm text-gray-500 space-y-1.5">
            <p className="text-gray-400 font-medium mb-2">How scoring works:</p>
            <p>1. Each pipeline run checks Polymarket for resolved markets</p>
            <p>2. "up" judgment + market resolved YES = ✓ correct</p>
            <p>3. "down" judgment + market resolved YES = ✗ wrong</p>
            <p>4. "neutral" abstentions are never graded</p>
          </div>
        </div>
      ) : (
        <>
          {/* Summary metrics */}
          <div className="grid grid-cols-3 gap-4 mt-6">
            <StatCard label="Hit Rate" value={hitPct} accent />
            <StatCard label="Correct / Graded" value={`${record.correct} / ${record.total_graded}`} />
            <StatCard label="Abstentions" value={String(record.total_scored - record.total_graded)} />
          </div>

          {/* By-direction table */}
          <div className="mt-8">
            <h2 className="text-sm font-medium text-gray-400 uppercase tracking-wider mb-3">
              By Direction
            </h2>
            <div className="bg-[#1A1D23] rounded-xl border border-gray-800 overflow-hidden">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-800">
                    {['Direction', 'Graded', 'Correct', 'Hit Rate'].map((h) => (
                      <th key={h} className="px-5 py-3 text-left text-xs text-gray-600 uppercase tracking-wider">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(['up', 'down'] as const).map((dir) => {
                    const stats = record.by_direction[dir];
                    const hr = stats.hit_rate !== null ? `${(stats.hit_rate * 100).toFixed(1)}%` : '—';
                    return (
                      <tr key={dir} className="border-b border-gray-800 last:border-0">
                        <td className="px-5 py-3 font-medium">
                          <span className={dir === 'up' ? 'text-green-400' : 'text-red-400'}>
                            {dir === 'up' ? '↑ UP' : '↓ DOWN'}
                          </span>
                        </td>
                        <td className="px-5 py-3 text-gray-300 tabular-nums">{stats.graded}</td>
                        <td className="px-5 py-3 text-gray-300 tabular-nums">{stats.correct}</td>
                        <td className="px-5 py-3 text-gray-300 tabular-nums font-mono">{hr}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <p className="mt-6 text-xs text-gray-700 leading-relaxed max-w-2xl">
            <strong className="text-gray-600">Honest framing:</strong> Hit rates at small sample sizes
            have wide confidence intervals and can be driven by luck rather than signal.
            A 70% rate from 10 judgments has a 95% CI of roughly ±29 percentage points.
            We report this transparently rather than claiming edge we haven&apos;t earned.
          </p>
        </>
      )}
    </div>
  );
}

function PageHeader() {
  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-100">Track Record</h1>
      <p className="text-sm text-gray-500 mt-1">
        Agent accuracy graded against Polymarket resolutions · honestly reported
      </p>
    </div>
  );
}

function StatCard({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="bg-[#1A1D23] rounded-xl p-5 border border-gray-800">
      <div className={`text-2xl font-bold tabular-nums ${accent ? 'text-[#00D4AA]' : 'text-gray-100'}`}>
        {value}
      </div>
      <div className="text-xs text-gray-600 mt-1">{label}</div>
    </div>
  );
}
