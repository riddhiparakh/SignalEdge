'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useState } from 'react';
import { triggerPipeline } from '@/lib/api';

const navItems = [
  { href: '/markets',      icon: '📊', label: 'Live Markets'  },
  { href: '/signals',      icon: '⚡', label: 'Signals'        },
  { href: '/research',     icon: '💬', label: 'Research'       },
  { href: '/track-record', icon: '🏆', label: 'Track Record'   },
];

const pipelineSteps: { step: 'markets' | 'news' | 'agent'; label: string }[] = [
  { step: 'markets', label: 'Fetch markets'  },
  { step: 'news',    label: 'Fetch news'     },
  { step: 'agent',   label: 'Run agent'      },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [loading, setLoading] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const run = async (step: 'markets' | 'news' | 'agent') => {
    setLoading(step);
    setToast(null);
    try {
      const result = await triggerPipeline(step);
      setToast(
        result.status === 'ok'
          ? `✓ ${step}: ${result.count} item(s)`
          : `✗ ${result.message}`
      );
    } catch {
      setToast(`✗ Could not reach API`);
    } finally {
      setLoading(null);
      setTimeout(() => setToast(null), 4000);
    }
  };

  return (
    <aside className="fixed top-0 left-0 h-screen w-60 bg-[#1A1D23] border-r border-gray-800 flex flex-col p-5 z-10">
      {/* Brand */}
      <div className="mb-8">
        <h1 className="text-[#00D4AA] text-lg font-bold tracking-tight">📡 SignalEdge</h1>
        <p className="text-gray-600 text-xs mt-0.5">Not financial advice</p>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5">
        {navItems.map(({ href, icon, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                active
                  ? 'bg-[#00D4AA]/10 text-[#00D4AA] font-medium'
                  : 'text-gray-400 hover:bg-gray-800 hover:text-gray-100'
              }`}
            >
              <span className="text-base">{icon}</span>
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>

      {/* Pipeline controls */}
      <div className="border-t border-gray-800 pt-4">
        <p className="text-xs text-gray-600 uppercase tracking-widest mb-2 px-1">Pipeline</p>
        <div className="space-y-1">
          {pipelineSteps.map(({ step, label }) => (
            <button
              key={step}
              onClick={() => run(step)}
              disabled={loading !== null}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs text-gray-500 hover:text-gray-200 hover:bg-gray-800 rounded-lg transition-colors disabled:opacity-40 disabled:cursor-not-allowed text-left"
            >
              <span>{loading === step ? '⏳' : '▶'}</span>
              <span>{label}</span>
            </button>
          ))}
        </div>

        {toast && (
          <div className={`mt-3 px-3 py-2 rounded-lg text-xs ${
            toast.startsWith('✓') ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400'
          }`}>
            {toast}
          </div>
        )}
      </div>
    </aside>
  );
}
