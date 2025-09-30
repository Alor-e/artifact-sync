export type ModelProvider = 'OPENAI' | 'GEMINI' | 'ANTHROPIC';

export interface AgentMetadata {
  provider: string;
  model: string;
  root_path: string;
  run_started_at: string;
  elapsed_seconds: number;
}

export interface AgentTokenUsage {
  prompt?: number;
  completion?: number;
  input?: number;
  output?: number;
  total?: number;
}

export interface DetailedImpactReportPayload {
  path: string;
  related: boolean;
  confidence: 'low' | 'medium' | 'high';
  analysis: {
    impact: 'direct' | 'indirect' | 'inderect';
    impact_description: string;
  };
  diagnosis: {
    needs_update: boolean;
    update_rationale: string;
  };
  recommendations: {
    recommended_actions: string[];
  };
}

export interface AgentReportEntry {
  path: string;
  content: string;
  parsed?: DetailedImpactReportPayload;
}

export interface AgentUnsureEntry {
  path: string;
  is_dir: boolean;
  reason: string;
  needed_info: string;
}

export interface AgentAnalysisResponse {
  metadata: AgentMetadata;
  sure: string[];
  report_entries: AgentReportEntry[];
  still_unsure: AgentUnsureEntry[];
  refinement_stats: unknown[];
  token_usage?: AgentTokenUsage;
}

export interface AgentFixResponse {
  path: string;
  impact_report: DetailedImpactReportPayload;
  fixed_content: string;
  usage?: Record<string, unknown> | null;
}

export interface AnalysisOptions {
  rootPath: string;
  provider: ModelProvider;
  model: string;
  maxDepth: number;
  maxRetries: number;
  apiKey: string;
  pythonPathSetting?: string;
}

export interface FixOptions extends AnalysisOptions {
  targetPath: string;
}

export type LogLevel = 'info' | 'warn' | 'error';

export interface BridgeLogEntry {
  timestamp: string;
  level: LogLevel;
  message: string;
}
