import { useState } from 'react';
import { ChevronDown, Circle, Settings, Mic, Send } from 'lucide-react';

type StepType = 'SYSTEM' | 'THINKING' | 'ACTION' | 'RESULT';

interface AgentStep {
  id: string;
  type: StepType;
  timestamp: string;
  content: string;
}

const mockSteps: AgentStep[] = [
  {
    id: '1',
    type: 'SYSTEM',
    timestamp: '14:32:01',
    content: 'Agent initialized with gpt-4-turbo model. Context window set to 128k tokens with temperature 0.7',
  },
  {
    id: '2',
    type: 'THINKING',
    timestamp: '14:32:01',
    content: 'Analyzing user request and breaking down into subtasks. Identified required data sources and planning retrieval pipeline.',
  },
  {
    id: '3',
    type: 'ACTION',
    timestamp: '14:32:02',
    content: 'Querying database: users table with active status filter, limited to 100 records',
  },
  {
    id: '4',
    type: 'RESULT',
    timestamp: '14:32:03',
    content: 'Query completed successfully. Retrieved 87 records in 674ms',
  },
  {
    id: '5',
    type: 'THINKING',
    timestamp: '14:32:03',
    content: 'Applying filters and transformations to results. Validating data integrity before aggregation.',
  },
  {
    id: '6',
    type: 'ACTION',
    timestamp: '14:32:03',
    content: 'Transforming data: aggregating by region with count and average score metrics',
  },
  {
    id: '7',
    type: 'RESULT',
    timestamp: '14:32:03',
    content: 'Transformation complete. Generated 5 regional groups in 379ms',
  },
];

const stepAccents: Record<StepType, string> = {
  SYSTEM: 'bg-blue-500/10 border-blue-400/30',
  THINKING: 'bg-purple-500/10 border-purple-400/30',
  ACTION: 'bg-orange-500/10 border-orange-400/30',
  RESULT: 'bg-emerald-500/10 border-emerald-400/30',
};

const stepLabels: Record<StepType, string> = {
  SYSTEM: 'text-blue-600',
  THINKING: 'text-purple-600',
  ACTION: 'text-orange-600',
  RESULT: 'text-emerald-600',
};

export function AgentConsole() {
  const [selectedModel, setSelectedModel] = useState('gpt-4-turbo');
  const [inputValue, setInputValue] = useState('');

  return (
    <div 
      className="w-full max-w-md h-[700px] bg-white/30 backdrop-blur-3xl border border-white/60 rounded-lg shadow-2xl shadow-blue-500/10 flex flex-col text-neutral-900"
      role="region"
      aria-label="AI Agent Console"
    >
      {/* Slim Top Bar */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/40 bg-white/20 backdrop-blur-xl">
        <div className="flex items-center gap-2">
          {/* Model Selector */}
          <div className="relative">
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="appearance-none bg-white/40 backdrop-blur-md border border-white/50 px-2.5 py-1 pr-7 rounded text-xs text-neutral-700 cursor-pointer hover:bg-white/60 focus:outline-none focus:ring-1 focus:ring-blue-300/60 transition-all"
              aria-label="Select AI model"
            >
              <option value="gpt-4-turbo" className="bg-white">GPT-4 Turbo</option>
              <option value="gpt-4" className="bg-white">GPT-4</option>
              <option value="claude-3-opus" className="bg-white">Claude 3 Opus</option>
              <option value="claude-3-sonnet" className="bg-white">Claude 3 Sonnet</option>
            </select>
            <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-neutral-500 pointer-events-none" />
          </div>

          {/* Status Indicator */}
          <div className="flex items-center gap-1.5 px-2 py-1 bg-white/40 backdrop-blur-md border border-white/50 rounded">
            <Circle className="w-1.5 h-1.5 fill-emerald-500 text-emerald-500" />
            <span className="text-xs text-neutral-600">Active</span>
          </div>
        </div>

        {/* Settings Icon */}
        <button 
          className="p-1.5 hover:bg-white/30 rounded transition-colors backdrop-blur-md"
          aria-label="Settings"
        >
          <Settings className="w-4 h-4 text-neutral-600" />
        </button>
      </div>

      {/* Scrollable Main Area */}
      <div 
        className="flex-1 overflow-y-auto px-3 py-3"
        role="log"
        aria-live="polite"
        aria-label="Agent activity log"
      >
        <div className="space-y-2.5">
          {mockSteps.map((step) => (
            <div
              key={step.id}
              className="bg-white/35 backdrop-blur-xl border border-white/50 rounded p-3 hover:bg-white/50 transition-colors shadow-sm"
              role="article"
              aria-label={`${step.type} step`}
            >
              {/* Header */}
              <div className="flex items-center justify-between mb-2 gap-2">
                <div className="flex items-center gap-2">
                  <div className={`w-1.5 h-1.5 rounded-full ${stepAccents[step.type]}`} />
                  <span className={`text-xs tracking-wide ${stepLabels[step.type]}`}>
                    {step.type}
                  </span>
                </div>
                <span className="text-xs font-mono text-neutral-500 tabular-nums">
                  {step.timestamp}
                </span>
              </div>

              {/* Content */}
              <p className="text-sm text-neutral-700 leading-relaxed">
                {step.content}
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* Fixed Bottom Input Bar */}
      <div className="border-t border-white/40 bg-white/20 backdrop-blur-2xl px-3 py-2.5">
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder="Type a message..."
            className="flex-1 bg-white/40 backdrop-blur-md border border-white/50 px-3 py-2 rounded text-sm text-neutral-700 placeholder:text-neutral-400 focus:outline-none focus:ring-1 focus:ring-blue-300/60 focus:bg-white/60 transition-all"
            aria-label="Message input"
          />

          <button 
            className="p-2 bg-white/40 backdrop-blur-md border border-white/50 rounded hover:bg-white/60 transition-colors"
            aria-label="Voice input"
          >
            <Mic className="w-4 h-4 text-neutral-600" />
          </button>

          <button 
            className="p-2 bg-blue-500 rounded hover:bg-blue-600 transition-colors shadow-lg shadow-blue-500/20"
            aria-label="Send message"
          >
            <Send className="w-4 h-4 text-white" />
          </button>
        </div>
      </div>
    </div>
  );
}