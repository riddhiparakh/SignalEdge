'use client';

import { useEffect, useRef, useState } from 'react';
import { postResearch } from '@/lib/api';
import type { Article } from '@/lib/types';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  articles?: Article[];
  sensitive?: boolean;
  error?: boolean;
}

const EXAMPLES = [
  'Federal Reserve interest rate cuts',
  'US presidential election 2026',
  'Bitcoin ETF approval',
];

export default function ChatInterface() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput]       = useState('');
  const [loading, setLoading]   = useState(false);
  const bottomRef               = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const send = async (query: string) => {
    const q = query.trim();
    if (!q || loading) return;
    setInput('');
    setMessages((m) => [...m, { role: 'user', text: q }]);
    setLoading(true);

    try {
      const data = await postResearch(q);
      setMessages((m) => [
        ...m,
        {
          role: 'assistant',
          text: data.sensitive
            ? 'Sensitive topic detected.'
            : data.articles.length === 0
            ? 'No relevant headlines found — run the pipeline first to populate the database.'
            : `Found ${data.articles.length} relevant headline(s):`,
          articles: data.articles,
          sensitive: data.sensitive,
        },
      ]);
    } catch {
      setMessages((m) => [
        ...m,
        { role: 'assistant', text: 'Could not reach the API. Is the FastAPI server running?', error: true },
      ]);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col" style={{ height: 'calc(100vh - 10rem)' }}>
      {/* Message list */}
      <div className="flex-1 overflow-y-auto space-y-5 pb-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center pb-8">
            <div className="text-5xl mb-4">💬</div>
            <p className="text-gray-300 font-medium text-lg">Research any prediction market topic</p>
            <p className="text-gray-600 text-sm mt-1 mb-6">
              Semantic search across indexed headlines — not just keyword matching
            </p>
            <div className="flex flex-wrap gap-2 justify-center">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => send(ex)}
                  className="text-xs px-3 py-1.5 bg-[#1A1D23] text-gray-400 hover:text-gray-200 rounded-full border border-gray-700 hover:border-gray-500 transition-colors"
                >
                  {ex}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) =>
          msg.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <div className="bg-[#00D4AA]/10 text-[#00D4AA] rounded-2xl rounded-tr-sm px-4 py-2.5 max-w-lg text-sm">
                {msg.text}
              </div>
            </div>
          ) : (
            <div key={i} className="flex justify-start">
              <div className="max-w-2xl w-full space-y-3">
                {msg.sensitive ? (
                  <div className="bg-yellow-500/10 border border-yellow-500/20 rounded-xl p-4 text-sm text-yellow-400">
                    ⚠️ <strong>Sensitive topic detected.</strong> Please ask about economic indicators, geopolitical treaties, elections, or other publicly-relevant events.
                  </div>
                ) : msg.error ? (
                  <div className="bg-red-500/10 border border-red-500/20 rounded-xl p-4 text-sm text-red-400">
                    {msg.text}
                  </div>
                ) : (
                  <>
                    <p className="text-sm text-gray-500">{msg.text}</p>
                    {msg.articles?.map((a, j) => (
                      <div key={j} className="bg-[#1A1D23] rounded-xl p-4 border border-gray-800">
                        <p className="text-sm font-medium text-gray-100">{a.title}</p>
                        <p className="text-xs text-gray-600 mt-1">{a.source} · {a.age}</p>
                        {a.description && (
                          <p className="text-xs text-gray-400 mt-2 line-clamp-2">{a.description}</p>
                        )}
                        {a.url && (
                          <a
                            href={a.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-[#00D4AA] hover:underline mt-2 block truncate"
                          >
                            {a.url}
                          </a>
                        )}
                      </div>
                    ))}
                  </>
                )}
              </div>
            </div>
          )
        )}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-[#1A1D23] rounded-xl px-4 py-3 text-sm text-gray-500 flex items-center gap-2">
              <span className="inline-block animate-spin">⏳</span> Searching headlines...
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div className="flex gap-3 pt-4 border-t border-gray-800">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input); } }}
          placeholder="Ask about any market topic…"
          className="flex-1 bg-[#1A1D23] border border-gray-700 rounded-xl px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-[#00D4AA] transition-colors"
        />
        <button
          onClick={() => send(input)}
          disabled={!input.trim() || loading}
          className="px-5 py-3 bg-[#00D4AA] text-[#0E1117] rounded-xl text-sm font-semibold hover:bg-[#00D4AA]/90 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Send
        </button>
      </div>
    </div>
  );
}
