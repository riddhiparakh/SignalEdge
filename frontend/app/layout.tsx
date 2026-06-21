import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import Sidebar from '@/components/Sidebar';
import './globals.css';

const inter = Inter({ subsets: ['latin'] });

export const metadata: Metadata = {
  title: 'SignalEdge',
  description: 'AI-powered prediction market signal detector',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <div className="flex min-h-screen bg-[#0E1117]">
          <Sidebar />
          <main className="flex-1 ml-60 p-8 min-h-screen">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
