/** Flexible drafts are transient. Persisted API response contracts are explicit below. */
export type Item = Record<string, any>;
export type Json =
  null | string | number | boolean | Json[] | { [key: string]: Json };
export interface Utterance {
  id?: string;
  event_id?: string;
  speaker: string;
  text: string;
  ms?: number;
  at?: string;
  timestamp?: string;
}
export interface TimelineEvent {
  id?: string;
  at?: string;
  timestamp?: string;
  created_at?: string;
  ms?: number;
  title?: string;
  kind?: string;
  type?: string;
  state?: string;
  text?: string;
  message?: string;
  description?: string;
  tool?: string;
}
export interface StructuredResult {
  label?: string;
  title?: string;
  value?: Json;
  provenance?: string;
  source_event_id?: string;
  source?: string;
  [key: string]: Json | undefined;
}
export interface Call {
  id: string;
  contact: string;
  number_masked: string;
  direction: string;
  agent_id: string | null;
  agent_name: string;
  provider: string;
  state: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  outcome: string | null;
  task_success: boolean | null;
  cost: number | null;
  cost_kind: string | null;
  transcript: Utterance[];
  timeline: TimelineEvent[];
  results: StructuredResult[] | StructuredResult;
  controls: string[] | Record<string, boolean>;
  heartbeat_at?: number;
  updated_at?: string;
  transcript_enabled?: boolean;
}
export interface Agent {
  id: string;
  name: string;
  description: string;
  prompt: string;
  voice: string;
  model: string;
  enabled: boolean;
  extension?: string;
  tools: string[];
  mode: "normal" | "business";
  max_duration_seconds: number;
}
export interface Contact {
  id: string;
  name: string;
  number: string;
  company: string;
  notes: string;
  allowed: boolean;
  tags: string[];
}
export interface Provider {
  id: string;
  name: string;
  kind:
    | "sip"
    | "telnyx"
    | "twilio"
    | "signalwire"
    | "generic"
    | "worker"
    | "openai";
  config: Record<string, string | number | boolean>;
  credential_presence: Record<string, boolean>;
  enabled: boolean;
  validation?: Record<string, Json>;
}
export interface Task {
  id: string;
  name: string;
  description: string;
  contact_id: string | null;
  agent_id: string | null;
  status: "draft" | "pending" | "completed" | "cancelled";
  prompt: string;
}
export interface WorkspaceEvent {
  id: string;
  call_id?: string;
  kind?: string;
  type?: string;
  message?: string;
  text?: string;
  at?: string;
  timestamp?: string;
  created_at?: string;
  level?: string;
  severity?: string;
  source?: string;
  component?: string;
  state?: string;
  code?: string;
  title?: string;
  description?: string;
  detail?: string;
}
export interface Health {
  dashboard?: string;
  gateway?: "connected" | "unknown" | "offline";
  gateway_last_seen?: number | null;
  vault?: string;
  demo?: boolean;
  sip_transport?: string;
  gpt_live?: string;
  transcript_storage?: boolean | string | null;
}
export interface Stats {
  total_calls?: number;
  active_calls?: number;
  completed_calls?: number;
  success_rate?: number | null;
  total_cost?: number | null;
  confirmed_cost?: number | null;
  total_confirmed_cost?: number | null;
  estimated_cost?: number | null;
  known_outcomes?: number;
  known_cost_calls?: number;
  confirmed_cost_calls?: number;
  estimated_cost_calls?: number;
  unknown_cost_calls?: number;
  average_duration?: number | null;
  total_duration?: number;
  days?: number;
  daily?: { date: string; calls: number }[];
}
export interface Workspace {
  health: Health;
  stats: Stats;
  calls: Call[];
  agents: Agent[];
  contacts: Contact[];
  providers: Provider[];
  tasks: Task[];
  logs: WorkspaceEvent[];
  activity: WorkspaceEvent[];
  capabilities: Record<string, boolean>;
}
export interface VaultProfile {
  id: string;
  name: string;
  created_at: number;
  field_count: number;
}
export interface VaultField {
  id: string;
  profile_id: string;
  key: string;
  label: string;
  sensitivity: "ordinary" | "sensitive";
  category?: string;
  value_masked: string;
  has_value: boolean;
  created_at: number;
  updated_at: number;
}
export interface VaultGrant {
  id: string;
  field_id: string;
  call_id: string;
  recipient: string;
  purpose: string;
  expires_at: number;
  revoked_at: number | null;
  created_at: number;
  active: boolean;
}
export interface VaultAudit {
  id: number;
  timestamp: number;
  event: string;
  decision: string;
  field_id: string | null;
  grant_id: string | null;
}
export interface Session {
  authenticated: boolean;
  csrf?: string;
  reauth_until?: number | string;
}
export type Page =
  | "Overview"
  | "Live Calls"
  | "Call History"
  | "Transcripts"
  | "Agents"
  | "Personal Vault"
  | "Contacts"
  | "Providers & SIP"
  | "Messages"
  | "Analytics"
  | "Live Logs"
  | "Automations"
  | "Settings";
