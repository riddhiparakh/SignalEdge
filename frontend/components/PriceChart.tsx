'use client';

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts';
import type { PricePoint } from '@/lib/types';

interface Props {
  data: PricePoint[];
}

export default function PriceChart({ data }: Props) {
  const chartData = data.map((p) => ({
    time: new Date(p.fetched_at).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    }),
    price: Math.round(p.yes_price * 100),
  }));

  return (
    <ResponsiveContainer width="100%" height={130}>
      <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
        <XAxis
          dataKey="time"
          tick={{ fill: '#6B7280', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          interval="preserveStartEnd"
        />
        <YAxis
          domain={[0, 100]}
          tick={{ fill: '#6B7280', fontSize: 10 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}%`}
          width={36}
        />
        <Tooltip
          contentStyle={{
            background: '#1A1D23',
            border: '1px solid #374151',
            borderRadius: 8,
            fontSize: 12,
          }}
          labelStyle={{ color: '#9CA3AF' }}
          formatter={(v: number) => [`${v}%`, 'YES price']}
        />
        <Line
          type="monotone"
          dataKey="price"
          stroke="#00D4AA"
          strokeWidth={2}
          dot={false}
          activeDot={{ r: 4, fill: '#00D4AA' }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
