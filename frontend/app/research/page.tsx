import ChatInterface from '@/components/ChatInterface';

export default function ResearchPage() {
  return (
    <div className="flex flex-col h-full">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-100">Research Chat</h1>
        <p className="text-sm text-gray-500 mt-1">
          Semantic search across all indexed headlines — finds relevant articles even without exact keyword matches
        </p>
      </div>
      <ChatInterface />
    </div>
  );
}
