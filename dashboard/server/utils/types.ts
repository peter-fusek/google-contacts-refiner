// --- Workplan ---

export interface WorkplanMetadata {
  created_at: string
  version: string
}

export interface ChangeStats {
  high: number
  medium: number
  low: number
  total_changes: number
  contacts: number
}

export interface Change {
  field: string
  old: string
  new: string
  confidence: number
  reason: string
  extra?: Record<string, unknown>
}

export interface ContactAnalysis {
  resourceName: string
  displayName: string
  etag: string
  changes: Change[]
  info: Array<{ field: string; old?: string; new: string; reason: string }>
  stats: { high: number; medium: number; low: number; total: number }
}

export interface Batch {
  batch_num: number
  contacts: ContactAnalysis[]
  stats: ChangeStats
  status: 'pending' | 'completed' | 'failed'
}

export interface WorkplanSummary {
  total_contacts_with_changes: number
  total_changes: number
  by_confidence: { high: number; medium: number; low: number }
  by_field_type: Record<string, number>
  info_items: { duplicates?: number; invalid?: number }
  total_batches: number
  batch_size: number
}

export interface Workplan {
  metadata: WorkplanMetadata
  summary: WorkplanSummary
  batches: Batch[]
  duplicates: unknown[]
  labels: unknown
}

// --- Checkpoint ---

export interface Checkpoint {
  session_id: string
  started_at: string
  last_completed_batch: number
  total_batches: number
  contacts_processed: number
  contacts_total: number
  status: 'initialized' | 'in_progress' | 'completed' | 'failed'
  workplan_path: string
  changelog_path: string
  backup_path: string
  last_checkpoint_at?: string
  completed_at?: string
  failed_at?: string
  error?: string
}

// --- AI Review Checkpoint ---

export interface AIReviewCheckpoint {
  status: string
  workplan_path: string
  last_reviewed: number
  total: number
  promoted: number
  demoted: number
}

// --- Changelog ---

export interface ChangelogEntry {
  timestamp: string
  resourceName: string
  field: string
  old: string
  new: string
  reason: string
  confidence: string
  confidence_value: number
  batch: number
  session_id: string
}

export interface BatchMarker {
  timestamp: string
  type: 'batch_start' | 'batch_end'
  batch: number
  contact_count?: number
  success?: number
  failed?: number
  session_id: string
}

export type ChangelogLine = ChangelogEntry | BatchMarker

// --- Review ---

export interface ReviewChange {
  id: string // hash(resourceName + field + old + new)
  resourceName: string
  displayName: string
  field: string
  old: string
  new: string
  confidence: number
  reason: string
  ruleCategory: string
  extra?: Record<string, unknown>
}

export interface ReviewDecision {
  changeId: string
  decision: 'approved' | 'rejected' | 'edited' | 'skipped'
  editedValue?: string
  decidedAt: string
}

export interface ReviewSession {
  id: string
  reviewFilePath: string
  createdAt: string
  decisions: Record<string, ReviewDecision> // changeId -> decision
  stats: { total: number; approved: number; rejected: number; edited: number; skipped: number }
}

export interface FeedbackEntry {
  timestamp: string
  type: 'approval' | 'rejection' | 'edit'
  ruleCategory: string
  field: string
  old: string
  suggested: string
  finalValue: string
  confidence: number
}

// --- API Responses ---

export interface StatusResponse {
  status: 'running' | 'completed' | 'failed' | 'idle'
  phase: 'phase1' | 'phase2' | 'idle'
  currentBatch: number
  totalBatches: number
  contactsProcessed: number
  contactsTotal: number
  eta: string | null
  lastRun: {
    startedAt: string | null
    completedAt: string | null
    duration: number | null
    changesApplied: number
    changesFailed: number
    cost: number | null
  }
  aiReview: {
    reviewed: number
    total: number
    promoted: number
    demoted: number
  } | null
}

export interface ChangelogResponse {
  entries: ChangelogEntry[]
  total: number
  page: number
  pageSize: number
}

export interface ContactSummary {
  resourceName: string
  name: string
  changes: number
  lastChanged: string
}

export interface FieldDrillDown {
  count: number
  reasons: Array<{ text: string; count: number }>
}

export interface AnalyticsResponse {
  byField: Record<string, number>
  byFieldDetail: Record<string, FieldDrillDown>
  byConfidence: { high: number; medium: number; low: number }
  successRate: number
  totalChanges: number
  totalFailed: number
  dailyRuns: Array<{ date: string; changes: number; failed: number }>
  topContacts: ContactSummary[]
  recentlyChanged: ContactSummary[]
  estimatedCost: number
}

// --- LinkedIn Social Signals ---

export interface LinkedInSignal {
  resourceName: string
  name: string
  linkedin_url: string
  scanned_at: string
  headline: string
  current_role: string
  recent_activity: string[]
  signal_type: 'job_change' | 'active' | 'profile' | 'no_activity'
  signal_text: string
}

export interface LinkedInSignalsFile {
  generated: string
  count: number
  signals: Record<string, LinkedInSignal>
}

export interface LinkedInSignalsResponse {
  signals: LinkedInSignal[]
  stats: {
    total: number
    jobChanges: number
    active: number
    profiles: number
    generated: string | null
  }
}

// --- FollowUp Scores ---

export interface FollowUpScore {
  resourceName: string
  name: string
  score_total: number
  rank: number
  score_breakdown: {
    interaction: number
    linkedin: number
    completeness: number
  }
  interaction: {
    last_date: string | null
    months_gap: number
    count: number
  }
  linkedin: {
    signal_type: string
    signal_text: string | null
    headline: string | null
    current_role: string | null
    scanned_at: string | null
    url: string | null
  } | null
  contact: {
    org: string
    title: string
    has_email: boolean
    has_phone: boolean
    has_org: boolean
    has_linkedin_url: boolean
    completeness: number
    emails: string[]
    urls: Array<{ url: string; type: string }>
  }
  followup_prompt: string | null
}

export interface FollowUpStats {
  job_change: number
  active: number
  profile_only: number
  no_activity: number
  no_linkedin: number
  avg_completeness: number
}

export interface FollowUpScoresFile {
  generated: string
  count: number
  scores: Record<string, FollowUpScore>
  stats: FollowUpStats
}

export interface FollowUpResponse {
  scores: FollowUpScore[]
  generated: string | null
  stats: FollowUpStats | null
}

// --- Config ---

export interface PipelineConfig {
  batchSize?: number
  confidenceHigh?: number
  confidenceMedium?: number
  aiCostLimit?: number
  autoMaxChanges?: number
  autoThreshold?: number
  updatedAt?: string
}

export interface ConfigResponse {
  batchSize: number
  confidenceHigh: number
  confidenceMedium: number
  aiModel: string
  aiCostLimit: number
  autoMaxChanges: number
  autoThreshold: number
  environment: string
  schedulerStatus: string
}

// --- CRM ---

export type CRMStage = 'inbox' | 'reached_out' | 'in_conversation' | 'opportunity' | 'converted' | 'dormant' | 'unknown' | 'ready_to_delete'

export interface CRMContactState {
  stage: CRMStage
  stageChangedAt: string
  notes: string
  tags: string[]
  name?: string
}

export interface CRMState {
  version: 1
  updatedAt: string
  contacts: Record<string, CRMContactState>
}

export interface CRMContact {
  resourceName: string
  name: string
  stage: CRMStage
  stageChangedAt: string
  notes: string
  tags: string[]
  score_total: number
  score_breakdown: FollowUpScore['score_breakdown']
  interaction: FollowUpScore['interaction']
  linkedin: FollowUpScore['linkedin']
  contact: FollowUpScore['contact']
  followup_prompt: string | null
}

export interface CRMResponse {
  contacts: CRMContact[]
  stages: Record<CRMStage, number>
}

// --- LinkedIn CRM ---

export type LIContactStatus = 'PENDING' | 'REQUEST_SENT' | 'CREATOR_MODE' | 'CONNECTED' | 'DM_SENT' | 'DM_SKIPPED' | 'RESPONDED'
export type LITier = 'T0' | 'T1' | 'T2' | 'T3'
export type LIInstitutionTier = 'A' | 'B' | 'C'

export interface LIContact {
  id: string
  name: string
  role: string
  linkedinUrl: string
  tier: LITier
  source: string
  status: LIContactStatus
  notes: string
  dmSentDate?: string
  dmTemplate?: string
  dmResponse?: string
  skipReason?: string
}

export interface LIInstitution {
  id: string
  name: string
  city?: string
  tier: LIInstitutionTier
  category: string
  contactStrategy: string
  status: string
  notes: string
}

export interface LIPost {
  date: string
  description: string
  language: string
  reactions: number
  comments?: number
  reposts?: number
  impressions?: number
  activityUrn: string
}

export interface LIMiningRun {
  date: string
  run: number
  post: string
  reactions: number
  nonFirst: number
  sent: number
  accepted?: number
  rate?: string
}

export interface LIDMLog {
  date: string
  contactName: string
  contactId: string
  tier: LITier
  template: string
  status: 'SENT' | 'SKIPPED'
  skipReason?: string
  response?: string
  followUpDate?: string
  outcome?: string
}

export interface LIFollowerSnapshot {
  date: string
  followers: number
  delta?: number
  notes?: string
}

export interface LIMilestone {
  name: string
  targetDate: string
  metric: string
  status: 'DONE' | 'EXCEEDED' | 'TODO' | 'IN_PROGRESS'
}

export interface LICRMData {
  contacts: LIContact[]
  institutions: LIInstitution[]
  posts: LIPost[]
  miningRuns: LIMiningRun[]
  dmLog: LIDMLog[]
  followerSnapshots: LIFollowerSnapshot[]
  milestones: LIMilestone[]
  updatedAt: string
}

export interface LICRMResponse {
  data: LICRMData
  stats: {
    totalContacts: number
    connected: number
    pending: number
    creatorMode: number
    dmsSent: number
    dmsSkipped: number
    responded: number
    followers: number
    followerDelta: number
    acceptanceRate: string
  }
}
