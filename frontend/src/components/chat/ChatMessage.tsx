import React, { useState, memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { User, Copy, Check, Eye, Layers } from 'lucide-react';
import type { Message } from '../../types';
import { GroundingScoreBadge } from './GroundingScoreBadge';
import { SourceTypeBadge } from './SourceTypeBadge';
import { CitationModal } from './CitationModal';

interface ChatMessageProps {
  message: Message;
  isStreaming?: boolean;
}

const ChatMessageComponent: React.FC<ChatMessageProps> = ({ message, isStreaming = false }) => {
  const [copied, setCopied] = useState(false);
  const [showSourcesModal, setShowSourcesModal] = useState(false);

  const isUser = message.role === 'user';
  const hasCitations = !!(message.citations && message.citations.length > 0);
  const hasWebSources = !!(message.web_sources && message.web_sources.length > 0);
  const hasSources = !isUser && (hasCitations || hasWebSources);
  const sourceCount = (message.citations?.length || 0) + (message.web_sources?.length || 0);

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (isUser) {
    return (
      <div className="py-4 px-4 sm:px-6 md:px-8 border-b border-slate-900/60">
        <div className="max-w-3xl mx-auto flex gap-3.5 items-start justify-end">
          <div className="max-w-xl bg-slate-900 border border-slate-800 rounded-xl px-4 py-2.5 shadow-xs">
            <div className="flex items-center gap-1.5 mb-1 text-[11px] font-medium text-slate-400">
              <User className="w-3 h-3 text-slate-500" />
              <span>You</span>
            </div>
            <p className="text-xs sm:text-sm text-slate-100 whitespace-pre-wrap leading-relaxed select-text">
              {message.content}
            </p>
          </div>
        </div>
      </div>
    );
  }

  // Assistant message: open, professional enterprise workspace layout
  return (
    <>
      <div className="py-5 px-4 sm:px-6 md:px-8 border-b border-slate-900/80 bg-slate-950/30">
        <div className="max-w-3xl mx-auto flex gap-3.5 items-start">
          {/* Subtle assistant icon */}
          <div className="w-7 h-7 rounded-lg bg-slate-900 border border-slate-700/80 flex items-center justify-center text-sap-400 shrink-0 mt-0.5">
            <Layers className="w-3.5 h-3.5" />
          </div>

          <div className="flex-1 min-w-0">
            {/* Metadata Bar */}
            <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-semibold text-slate-200">
                  SAP Knowledge Assistant
                </span>
                <SourceTypeBadge sourceType={message.source_type} />
                {message.source_type !== 'refusal' && <GroundingScoreBadge score={message.grounding_score} />}
              </div>

              {/* Action buttons */}
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={handleCopy}
                  className="flex items-center gap-1 px-2 py-1 rounded text-slate-400 hover:text-slate-200 hover:bg-slate-850 text-[11px] transition"
                  title="Copy response"
                  aria-label="Copy response"
                >
                  {copied ? (
                    <>
                      <Check className="w-3 h-3 text-emerald-400" />
                      <span className="text-emerald-400 font-medium">Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3 text-slate-500" />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              </div>
            </div>

            {/* Markdown content */}
            <div className="prose-sap text-xs sm:text-sm text-slate-200 leading-relaxed break-words">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {message.content}
              </ReactMarkdown>
              {isStreaming && (
                <span className="inline-block w-1.5 h-4 ml-1 bg-sap-400 animate-pulse align-middle" />
              )}
            </div>

            {/* Sources / Citations control */}
            {hasSources && (
              <div className="mt-3 pt-2.5 border-t border-slate-900 flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setShowSourcesModal(true)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-slate-900 hover:bg-slate-850 text-slate-300 hover:text-sap-300 border border-slate-800 text-xs font-medium transition cursor-pointer"
                  title="View grounding sources & verified excerpts"
                >
                  <Eye className="w-3.5 h-3.5 text-sap-400" />
                  <span>Sources ({sourceCount})</span>
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Citation Detail Modal */}
      <CitationModal
        citations={message.citations}
        webSources={message.web_sources}
        isOpen={showSourcesModal}
        onClose={() => setShowSourcesModal(false)}
      />
    </>
  );
};

export const ChatMessage = memo(ChatMessageComponent, (prevProps, nextProps) => {
  return (
    prevProps.message.id === nextProps.message.id &&
    prevProps.message.content === nextProps.message.content &&
    prevProps.message.grounding_score === nextProps.message.grounding_score &&
    prevProps.message.source_type === nextProps.message.source_type &&
    prevProps.isStreaming === nextProps.isStreaming
  );
});
