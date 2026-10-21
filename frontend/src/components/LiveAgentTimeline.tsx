"use client";

import { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cpu,
  FileSearch,
  FileText,
  Layers,
  RefreshCw,
  Scale,
  ShieldAlert,
  ShieldCheck,
  Stethoscope,
  Wifi,
  WifiOff,
  Zap,
} from "lucide-react";
import { AgentExecutionEvent, fetchCaseEvents, getCaseWebSocketUrl } from "@/lib/api";

interface LiveAgentTimelineProps {
  caseId: string;
  isComplete?: boolean;
  isRerunning?: boolean;
  onPipelineComplete?: () => void;
}

interface StepDefinition {
  agentName: string;
  displayName: string;
  description: string;
  icon: typeof Activity;
  stageId: string;
}

const CANONICAL_STEPS: StepDefinition[] = [
  {
    agentName: "OCRPreprocessor",
    displayName: "OCR Ingestion & Preprocessing",
    description: "Multi-page intake normalization, deskew, and visual enhancement",
    icon: FileText,
    stageId: "intake",
  },
  {
    agentName: "SplitGroup",
    displayName: "Document Boundary Detection",
    description: "Identifies document logical groupings and packet separation",
    icon: Layers,
    stageId: "intake",
  },
  {
    agentName: "ClassifierAgent",
    displayName: "Document Type Classification",
    description: "Classifies ID, CMS-1500, UB-04, and clinical discharge summaries",
    icon: FileSearch,
    stageId: "intake",
  },
  {
    agentName: "CompletenessGate",
    displayName: "Intake Completeness Gate",
    description: "Verifies mandatory intake documents before specialist routing",
    icon: ShieldCheck,
    stageId: "intake",
  },
  {
    agentName: "KYCAgent",
    displayName: "KYC & Member Verification",
    description: "Validates patient identity against authoritative member database",
    icon: ShieldAlert,
    stageId: "kyc",
  },
  {
    agentName: "ClaimsAgent",
    displayName: "Claims Extraction & Math Audit",
    description: "Extracts line items, verifies header sum vs itemized lines arithmetic",
    icon: Cpu,
    stageId: "claims",
  },
  {
    agentName: "PolicyContextAgent",
    displayName: "Policy Enrollment Retrieval",
    description: "Resolves active insurance policy version and benefit schedule",
    icon: Bot,
    stageId: "policy",
  },
  {
    agentName: "PolicyCoverageAgent",
    displayName: "Policy Coverage & Exclusions RAG",
    description: "Retrieves clauses, pre-auth requirements, and exclusion criteria",
    icon: Scale,
    stageId: "policy",
  },
  {
    agentName: "FraudEngine",
    displayName: "Deterministic Fraud Screening",
    description: "Detects duplicate submissions, billing spikes, and provider anomalies",
    icon: Zap,
    stageId: "risk",
  },
  {
    agentName: "ClinicalAlignmentEngine",
    displayName: "Clinical Necessity Cross-Check",
    description: "Audits ICD-10 diagnosis alignment with CPT surgical/medical procedures",
    icon: Stethoscope,
    stageId: "risk",
  },
  {
    agentName: "FaithfulnessGate",
    displayName: "Faithfulness & Grounding Gate",
    description: "Verifies extracted evidence is anchored in source intake documents",
    icon: CheckCircle2,
    stageId: "decision",
  },
  {
    agentName: "DecisionEngine",
    displayName: "Deterministic Rule Adjudication",
    description: "Evaluates binding priority rules P1–P8 to produce final business decision",
    icon: Scale,
    stageId: "decision",
  },
];

interface PipelineStage {
  id: string;
  label: string;
  shortLabel: string;
  stepNames: string[];
}

const PIPELINE_STAGES: PipelineStage[] = [
  {
    id: "intake",
    label: "Intake & OCR",
    shortLabel: "Intake",
    stepNames: ["OCRPreprocessor", "SplitGroup", "ClassifierAgent", "CompletenessGate"],
  },
  {
    id: "kyc",
    label: "Member KYC",
    shortLabel: "KYC",
    stepNames: ["KYCAgent"],
  },
  {
    id: "claims",
    label: "Claims Math",
    shortLabel: "Claims",
    stepNames: ["ClaimsAgent"],
  },
  {
    id: "policy",
    label: "Policy RAG",
    shortLabel: "Policy",
    stepNames: ["PolicyContextAgent", "PolicyCoverageAgent"],
  },
  {
    id: "risk",
    label: "Fraud & Clinical",
    shortLabel: "Risk",
    stepNames: ["FraudEngine", "ClinicalAlignmentEngine"],
  },
  {
    id: "decision",
    label: "Rule Decision",
    shortLabel: "Decision",
    stepNames: ["FaithfulnessGate", "DecisionEngine"],
  },
];

export function LiveAgentTimeline({
  caseId,
  isComplete,
  isRerunning,
  onPipelineComplete,
}: LiveAgentTimelineProps) {
  const [events, setEvents] = useState<AgentExecutionEvent[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<
    "connecting" | "connected" | "disconnected" | "fallback"
  >("connecting");
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const [isDetailsExpanded, setIsDetailsExpanded] = useState(false);

  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Clear timeline immediately when a workflow rerun is triggered
  useEffect(() => {
    if (isRerunning) {
      setEvents([]);
    }
  }, [isRerunning]);

  // Load initial historical events via REST fallback
  useEffect(() => {
    let isMounted = true;
    if (!isRerunning) {
      fetchCaseEvents(caseId)
        .then((data) => {
          if (isMounted && data && data.length > 0) {
            setEvents(data);
          }
        })
        .catch((err) => {
          console.warn("REST event fallback note:", err);
        });
    }
    return () => {
      isMounted = false;
    };
  }, [caseId, isRerunning]);

  // Establish live WebSocket connection
  useEffect(() => {
    let isMounted = true;

    const connectWebSocket = () => {
      if (!caseId) return;

      const token =
        typeof window !== "undefined"
          ? window.localStorage.getItem("medishield_access_token") || "reviewer-token"
          : "reviewer-token";

      const wsUrl = getCaseWebSocketUrl(caseId, token);

      try {
        setConnectionStatus("connecting");
        const ws = new WebSocket(wsUrl);
        socketRef.current = ws;

        ws.onopen = () => {
          if (!isMounted) return;
          setConnectionStatus("connected");
          setReconnectAttempts(0);
        };

        ws.onmessage = (event) => {
          if (!isMounted) return;
          try {
            const data: AgentExecutionEvent = JSON.parse(event.data);
            if ((data as any).type === "pong") return;

            setEvents((prev) => {
              // If incoming event belongs to a new run_id, reset old run events
              const lastRunId = prev.length > 0 ? prev[prev.length - 1].run_id : null;
              if (lastRunId && data.run_id && data.run_id !== lastRunId) {
                return [data];
              }

              // Deduplicate exact match
              const existingIdx = prev.findIndex(
                (e) => e.agent_name === data.agent_name && e.event_type === data.event_type
              );
              if (existingIdx >= 0) {
                const next = [...prev];
                next[existingIdx] = data;
                return next;
              }
              // Replace older event for same agent (e.g. replace STARTED/RUNNING with COMPLETED/SUCCESS)
              const agentIdx = prev.findIndex((e) => e.agent_name === data.agent_name);
              if (agentIdx >= 0) {
                const next = [...prev];
                next[agentIdx] = data;
                return next;
              }
              return [...prev, data];
            });

            // Signal pipeline completion when final DecisionEngine finishes
            if (
              data.agent_name === "DecisionEngine" &&
              (data.event_type === "COMPLETED" || data.status === "SUCCESS")
            ) {
              if (onPipelineComplete) {
                onPipelineComplete();
              }
            }
          } catch (e) {
            console.warn("Failed to parse WebSocket event:", e);
          }
        };

        ws.onclose = () => {
          if (!isMounted) return;
          setConnectionStatus("disconnected");
          if (!isComplete) {
            const delay = Math.min(1000 * Math.pow(1.5, reconnectAttempts), 10000);
            reconnectTimeoutRef.current = setTimeout(() => {
              setReconnectAttempts((prev) => prev + 1);
              connectWebSocket();
            }, delay);
          } else {
            setConnectionStatus("fallback");
          }
        };

        ws.onerror = () => {
          if (!isMounted) return;
          setConnectionStatus("disconnected");
        };
      } catch (err: unknown) {
        if (!isMounted) return;
        setConnectionStatus("fallback");
      }
    };

    connectWebSocket();

    // Heartbeat ping interval
    const pingInterval = setInterval(() => {
      if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
        socketRef.current.send("ping");
      }
    }, 15000);

    return () => {
      isMounted = false;
      clearInterval(pingInterval);
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [caseId, isComplete, reconnectAttempts, onPipelineComplete]);

  // Map events by agentName for the current active run
  const latestRunId = events.length > 0 ? events[events.length - 1].run_id : null;
  const currentRunEvents = latestRunId ? events.filter((e) => e.run_id === latestRunId) : events;

  const eventMap = new Map<string, AgentExecutionEvent>();
  for (const e of currentRunEvents) {
    const existing = eventMap.get(e.agent_name);
    if (!existing || e.sequence_number >= existing.sequence_number) {
      eventMap.set(e.agent_name, e);
    }
  }

  const completedCount = CANONICAL_STEPS.filter((s) => {
    const ev = eventMap.get(s.agentName);
    return ev?.status === "SUCCESS";
  }).length;

  const totalDurationMs = events.reduce((acc, e) => acc + (e.duration_ms || 0), 0);

  // Identify currently running step (if any)
  const activeRunningStep = CANONICAL_STEPS.find(
    (s) => eventMap.get(s.agentName)?.status === "RUNNING"
  );
  const activeRunningEvent = activeRunningStep ? eventMap.get(activeRunningStep.agentName) : null;

  // Compute status for each of the 6 pipeline stages
  const stageStatuses = PIPELINE_STAGES.map((stage) => {
    const stageEvents = stage.stepNames.map((name) => eventMap.get(name));
    const hasRunning = stageEvents.some((e) => e?.status === "RUNNING");
    const allSuccess =
      stageEvents.length > 0 && stageEvents.every((e) => e?.status === "SUCCESS");
    const hasError = stageEvents.some((e) => e?.status === "ERROR");
    const hasPartial = stageEvents.some((e) => e?.status === "SUCCESS");

    let status: "RUNNING" | "SUCCESS" | "ERROR" | "PARTIAL" | "PENDING" = "PENDING";
    if (hasRunning) status = "RUNNING";
    else if (allSuccess) status = "SUCCESS";
    else if (hasError) status = "ERROR";
    else if (hasPartial) status = "PARTIAL";

    return { ...stage, status };
  });

  return (
    <div className="bg-card border border-border rounded-card p-4 space-y-3 shadow-xs">
      {/* Header and Live Status Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 pb-2.5 border-b border-border">
        <div className="flex items-center space-x-2">
          <Activity className="w-4 h-4 text-accent" />
          <h3 className="text-xs font-bold text-foreground tracking-tight uppercase">
            Adjudication Pipeline Stream
          </h3>
          <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-sky-soft text-sky-deep border border-sky/30">
            Live
          </span>
        </div>

        <div className="flex items-center space-x-2">
          {connectionStatus === "connected" && (
            <span className="inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-full bg-status-success/10 text-status-success border border-status-success/20 text-[10px] font-medium">
              <span className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse" />
              <span>Streaming</span>
            </span>
          )}
          {connectionStatus === "connecting" && (
            <span className="inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-full bg-status-warning/10 text-status-warning border border-status-warning/20 text-[10px] font-medium">
              <RefreshCw className="w-2.5 h-2.5 animate-spin" />
              <span>Connecting</span>
            </span>
          )}
          {connectionStatus === "disconnected" && (
            <span className="inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-full bg-rose-blush text-rose-deep border border-rose-med/30 text-[10px] font-medium">
              <WifiOff className="w-2.5 h-2.5" />
              <span>Offline</span>
            </span>
          )}
          {connectionStatus === "fallback" && (
            <span className="inline-flex items-center space-x-1.5 px-2 py-0.5 rounded-full bg-card-inner text-secondary border border-border text-[10px] font-medium">
              <CheckCircle2 className="w-2.5 h-2.5 text-status-success" />
              <span>Synced</span>
            </span>
          )}

          <span className="px-2 py-0.5 rounded-btn bg-card-inner border border-border text-[10px] font-mono text-muted">
            {completedCount} / {CANONICAL_STEPS.length} Steps
          </span>
        </div>
      </div>

      {/* Horizontal Pipeline Animation Track */}
      <div className="py-1">
        <div className="flex items-center justify-between w-full relative">
          {stageStatuses.map((stg, idx) => {
            const isLast = idx === stageStatuses.length - 1;
            const isRunning = stg.status === "RUNNING";
            const isSuccess = stg.status === "SUCCESS";
            const isError = stg.status === "ERROR";

            return (
              <div key={stg.id} className="flex items-center flex-1 last:flex-none">
                {/* Stage Node */}
                <div className="flex flex-col items-center shrink-0 group relative">
                  <div
                    className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-mono font-bold border transition-all ${
                      isRunning
                        ? "bg-sky-soft text-sky-deep border-sky ring-2 ring-sky/30 shadow-xs animate-pulse"
                        : isSuccess
                        ? "bg-status-success/10 text-status-success border-status-success/30"
                        : isError
                        ? "bg-rose-blush text-rose-deep border-rose-med/40"
                        : "bg-card-inner text-muted border-border"
                    }`}
                  >
                    {isRunning ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    ) : isSuccess ? (
                      <CheckCircle2 className="w-3.5 h-3.5" />
                    ) : isError ? (
                      <AlertCircle className="w-3.5 h-3.5" />
                    ) : (
                      <span className="text-[10px]">{idx + 1}</span>
                    )}
                  </div>
                  <span
                    className={`text-[10px] font-semibold mt-1 tracking-tight truncate max-w-[70px] text-center ${
                      isRunning ? "text-sky-deep font-bold" : isSuccess ? "text-foreground" : "text-muted"
                    }`}
                  >
                    {stg.shortLabel}
                  </span>
                </div>

                {/* Animated Connecting Pipeline Flow Beam */}
                {!isLast && (
                  <div className="flex-1 mx-1.5 mb-4 h-1 rounded relative overflow-hidden bg-border">
                    {/* Beam progress indicator */}
                    {isSuccess && (
                      <div className="absolute inset-0 bg-status-success/60" />
                    )}
                    {isRunning && (
                      <div className="absolute inset-0 bg-gradient-to-r from-sky via-sky/60 to-transparent animate-pulse" />
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Streaming Focus Card: Shows ONLY the currently running agent */}
      {activeRunningEvent ? (
        <div className="bg-sky-pale border border-sky/30 rounded-card p-3 shadow-xs transition-all duration-180">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center space-x-3 min-w-0">
              <div className="relative flex items-center justify-center shrink-0">
                <span className="w-3 h-3 rounded-full bg-sky animate-ping absolute opacity-75" />
                <span className="w-2.5 h-2.5 rounded-full bg-sky relative" />
              </div>
              <div className="min-w-0">
                <div className="flex items-center space-x-2">
                  <span className="text-xs font-bold text-foreground truncate">
                    {activeRunningStep?.displayName || activeRunningEvent.agent_name}
                  </span>
                  <span className="px-1.5 py-0.2 rounded text-[10px] font-semibold bg-sky-soft text-sky-deep uppercase font-mono animate-pulse border border-sky/30">
                    Running
                  </span>
                </div>
                <p className="text-[11px] text-secondary truncate mt-0.5">
                  {activeRunningEvent.safe_summary || activeRunningStep?.description}
                </p>
              </div>
            </div>
            <div className="shrink-0 flex items-center space-x-2 text-right">
              {activeRunningEvent.duration_ms !== null && activeRunningEvent.duration_ms !== undefined && (
                <span className="text-[11px] font-mono text-sky-deep font-semibold tabular-nums">
                  {activeRunningEvent.duration_ms.toFixed(0)} ms
                </span>
              )}
            </div>
          </div>
        </div>
      ) : completedCount === CANONICAL_STEPS.length ? (
        <div className="bg-status-success/10 border border-status-success/20 rounded-card p-2.5 flex items-center justify-between shadow-xs">
          <div className="flex items-center space-x-2.5 text-xs">
            <CheckCircle2 className="w-4 h-4 text-status-success shrink-0" />
            <span className="font-semibold text-foreground">
              All 12 Agents Finished Execution
            </span>
          </div>
          <span className="text-[11px] font-mono font-bold text-status-success">
            {(totalDurationMs / 1000).toFixed(2)}s Total
          </span>
        </div>
      ) : (
        <div className="bg-card-inner/50 border border-border rounded-card p-2.5 flex items-center justify-between text-xs text-muted">
          <div className="flex items-center space-x-2">
            <Activity className="w-3.5 h-3.5 text-muted" />
            <span>Adjudication pipeline idle — ready for execution</span>
          </div>
          <span className="text-[10px] font-mono">
            {completedCount} of {CANONICAL_STEPS.length} complete
          </span>
        </div>
      )}

      {/* Collapsible Toggle for Full Step Breakdown */}
      <div className="flex items-center justify-between pt-0.5">
        <button
          type="button"
          onClick={() => setIsDetailsExpanded(!isDetailsExpanded)}
          className="inline-flex items-center space-x-1 text-[11px] text-muted hover:text-foreground font-medium transition-all duration-180 active:scale-[0.98]"
        >
          {isDetailsExpanded ? (
            <ChevronUp className="w-3.5 h-3.5" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5" />
          )}
          <span>
            {isDetailsExpanded
              ? "Hide step details"
              : `View all ${CANONICAL_STEPS.length} step details`}
          </span>
        </button>
        <span className="text-[10px] font-mono text-muted">
          Latency: {(totalDurationMs / 1000).toFixed(2)}s
        </span>
      </div>

      {/* Expanded Full Step Breakdown (collapsed by default) */}
      {isDetailsExpanded && (
        <div className="space-y-1.5 pt-2 border-t border-border">
          {CANONICAL_STEPS.map((step, idx) => {
            const ev = eventMap.get(step.agentName);
            const isRunning = ev?.status === "RUNNING";
            const isSuccess = ev?.status === "SUCCESS";
            const isFailed = ev?.status === "ERROR";
            const isPending = !ev;

            return (
              <div
                key={step.agentName}
                className={`p-2 rounded-btn border text-xs flex items-center justify-between transition-all duration-180 hover:border-pink-border hover:bg-pink-hover/40 ${
                  isRunning
                    ? "bg-sky-soft text-sky-deep border-sky/40"
                    : isSuccess
                    ? "bg-card-inner/60 border-border"
                    : isFailed
                    ? "bg-rose-blush text-rose-deep border-rose-med/30"
                    : "bg-card-inner/20 border-border/40 opacity-60"
                }`}
              >
                <div className="flex items-center space-x-2.5 truncate">
                  <div
                    className={`w-5 h-5 rounded-full flex items-center justify-center shrink-0 text-[10px] font-bold ${
                      isRunning
                        ? "bg-accent text-accent-fg animate-spin"
                        : isSuccess
                        ? "bg-status-success/20 text-status-success"
                        : isFailed
                        ? "bg-status-danger/20 text-status-danger"
                        : "bg-card text-muted"
                    }`}
                  >
                    {isSuccess ? "✓" : isFailed ? "✗" : idx + 1}
                  </div>
                  <div className="truncate">
                    <span className="font-semibold text-foreground mr-2">
                      {step.displayName}
                    </span>
                    <span className="text-[10px] text-secondary truncate">
                      {ev?.safe_summary || step.description}
                    </span>
                  </div>
                </div>

                <div className="shrink-0 flex items-center space-x-2 font-mono text-[10px] text-muted">
                  {ev?.duration_ms !== null && ev?.duration_ms !== undefined && (
                    <span>{ev.duration_ms.toFixed(0)}ms</span>
                  )}
                  <span
                    className={`px-1.5 py-0.2 rounded font-semibold ${
                      isRunning
                        ? "bg-accent/20 text-accent"
                        : isSuccess
                        ? "bg-status-success/15 text-status-success"
                        : isFailed
                        ? "bg-status-danger/15 text-status-danger"
                        : "text-muted"
                    }`}
                  >
                    {isRunning ? "RUNNING" : isSuccess ? "DONE" : isFailed ? "FAILED" : "WAIT"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
