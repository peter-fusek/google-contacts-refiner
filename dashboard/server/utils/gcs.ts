import { Storage } from '@google-cloud/storage'
import type {
  Workplan,
  Checkpoint,
  AIReviewCheckpoint,
  ChangelogEntry,
  ChangelogLine,
  BatchMarker,
  ReviewSession,
  FeedbackEntry,
  FollowUpScore,
  FollowUpStats,
  FollowUpScoresFile,
  LinkedInSignal,
  LinkedInSignalsFile,
} from './types'

let storage: Storage | null = null

function getStorage(): Storage {
  if (storage) return storage

  // Try GOOGLE_APPLICATION_CREDENTIALS first (local dev with key file)
  if (process.env.GOOGLE_APPLICATION_CREDENTIALS) {
    storage = new Storage()
    console.log('[GCS] Using file-based credentials')
    return storage
  }

  // On Render: parse SA key from env var and pass credentials directly
  // This avoids file-based auth which has OpenSSL PEM parsing issues
  const raw = process.env.NUXT_GCS_SERVICE_ACCOUNT || process.env.GCS_SERVICE_ACCOUNT
  if (raw) {
    try {
      const creds = typeof raw === 'string' ? JSON.parse(raw) : raw
      // Always normalize private_key newlines — env vars may have literal \n
      if (creds.private_key) {
        // Replace literal backslash-n with real newlines
        creds.private_key = creds.private_key.replace(/\\n/g, '\n')
      }
      storage = new Storage({ credentials: creds })
      console.log('[GCS] Using credentials from env var - project:', creds.project_id)
      return storage
    } catch (err) {
      // SA key was provided but is malformed — fail loudly, don't silently fall through to ADC
      throw new Error(`[GCS] Failed to parse SA credentials: ${(err as Error).message}`)
    }
  }

  // Fallback to ADC (only when no explicit SA key was provided)
  storage = new Storage()
  console.log('[GCS] Using ADC fallback')
  return storage
}

function getBucket() {
  const config = useRuntimeConfig()
  const bucket = String(config.gcsBucket)
  return getStorage().bucket(bucket)
}

// In-memory cache with TTL
const cache = new Map<string, { data: unknown; expires: number }>()
const CACHE_TTL = 60_000 // 60 seconds — data changes at most once per pipeline run

async function cachedRead<T>(key: string, reader: () => Promise<T>): Promise<T> {
  const cached = cache.get(key)
  if (cached && cached.expires > Date.now()) {
    return cached.data as T
  }

  const data = await reader()
  cache.set(key, { data, expires: Date.now() + CACHE_TTL })
  return data
}

async function readJson<T>(path: string): Promise<T | null> {
  try {
    const [content] = await getBucket().file(path).download()
    return JSON.parse(content.toString('utf-8')) as T
  } catch (err) {
    const code = (err as { code?: number }).code
    // 404 = file not found — legitimate "no data" case
    if (code === 404) return null
    // All other errors (auth, network, permissions) should propagate
    console.error(`[GCS] readJson(${path}) failed (code=${code}):`, (err as Error).message)
    throw err
  }
}

async function findLatestFile(prefix: string, extension: string): Promise<string | null> {
  const [files] = await getBucket().getFiles({ prefix })
  const matching = files
    .filter(f => f.name.endsWith(extension))
    .sort((a, b) => b.name.localeCompare(a.name))
  return matching[0]?.name ?? null
}

// --- Public API ---

export async function getLatestWorkplan(): Promise<Workplan | null> {
  return cachedRead('workplan', async () => {
    const path = await findLatestFile('data/workplan_', '.json')
    if (!path) return null
    return readJson<Workplan>(path)
  })
}

export async function getCheckpoint(): Promise<Checkpoint | null> {
  return cachedRead('checkpoint', () => readJson<Checkpoint>('data/checkpoint.json'))
}

export async function getAIReviewCheckpoint(): Promise<AIReviewCheckpoint | null> {
  return cachedRead('ai_review_checkpoint', () =>
    readJson<AIReviewCheckpoint>('data/ai_review_checkpoint.json'),
  )
}

async function findAllFiles(prefix: string, extension: string): Promise<string[]> {
  const [files] = await getBucket().getFiles({ prefix })
  return files
    .filter(f => f.name.endsWith(extension))
    .sort((a, b) => a.name.localeCompare(b.name))
    .map(f => f.name)
}

async function readAllChangelogs(): Promise<ChangelogLine[]> {
  const paths = await findAllFiles('data/changelog_', '.jsonl')
  if (!paths.length) return []

  const all: ChangelogLine[] = []
  for (const path of paths) {
    try {
      const [content] = await getBucket().file(path).download()
      const text = content.toString('utf-8').trim()
      if (!text) continue
      for (const line of text.split('\n')) {
        try {
          all.push(JSON.parse(line) as ChangelogLine)
        } catch (e) {
          console.warn(`[GCS] Malformed JSON in ${path}: ${(e as Error).message}`)
        }
      }
    } catch (e) {
      console.warn(`[GCS] Skipping changelog ${path} (will return partial data): ${(e as Error).message}`)
    }
  }
  return all
}

export async function getChangelog(): Promise<ChangelogEntry[]> {
  return cachedRead('changelog', async () => {
    const all = await readAllChangelogs()
    const entries = all.filter((e): e is ChangelogEntry => !('type' in e))

    // Deduplicate: same (resourceName, field, old, new) keeps only the first occurrence
    const seen = new Set<string>()
    return entries.filter((e) => {
      const key = `${e.resourceName}|${e.field}|${e.old}|${e.new}`
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  })
}

export async function getChangelogWithMarkers(): Promise<ChangelogLine[]> {
  return cachedRead('changelog_full', readAllChangelogs)
}

export function isBatchMarker(entry: ChangelogLine): entry is BatchMarker {
  return 'type' in entry
}

// --- Write API ---

export async function writeJson(path: string, data: unknown): Promise<void> {
  const content = JSON.stringify(data, null, 2)
  try {
    await getBucket().file(path).save(content, {
      contentType: 'application/json',
      resumable: false,
    })
  } catch (err) {
    // Retry once — GCS can fail on overwrite with precondition errors
    console.warn(`[GCS] writeJson(${path}) first attempt failed: ${(err as Error).message}, retrying...`)
    await getBucket().file(path).save(content, {
      contentType: 'application/json',
      resumable: false,
      metadata: { cacheControl: 'no-cache' },
    })
  }
}

async function appendJsonl(path: string, entries: unknown[]): Promise<void> {
  const lines = entries.map(e => JSON.stringify(e)).join('\n') + '\n'

  const file = getBucket().file(path)
  let existing = ''
  try {
    const [content] = await file.download()
    existing = content.toString('utf-8')
  } catch (err: unknown) {
    // File likely doesn't exist yet — log non-404 errors but proceed to create
    const code = (err as { code?: number })?.code
    if (code !== 404) {
      console.warn(`[GCS] appendJsonl(${path}) read returned code ${code}:`, (err as Error).message)
    }
  }

  await file.save(existing + lines, {
    contentType: 'application/x-ndjson',
    resumable: false,
  })
}

// --- Review API ---

export async function getLatestReviewFile(): Promise<{ path: string; data: unknown } | null> {
  // Use specific prefix to avoid matching review_sessions/ and review_decisions_
  const [files] = await getBucket().getFiles({ prefix: 'data/review_' })
  const matching = files
    .filter(f => f.name.endsWith('.json'))
    // Exclude subdirectories (review_sessions/) and decision files (review_decisions_)
    .filter(f => !f.name.includes('/review_sessions/') && !f.name.includes('review_decisions_'))
    .sort((a, b) => b.name.localeCompare(a.name))
  const path = matching[0]?.name
  if (!path) return null
  const data = await readJson(path)
  return data ? { path, data } : null
}

export async function getReviewFile(path: string): Promise<{ path: string; data: unknown } | null> {
  const data = await readJson(path)
  return data ? { path, data } : null
}

export async function getSessionForReviewFile(reviewFilePath: string): Promise<ReviewSession | null> {
  // Reuse getAllReviewSessions (cached, parallel reads) instead of N+1 sequential GCS reads
  const sessions = await getAllReviewSessions()
  return sessions.find(
    s => s.reviewFilePath === reviewFilePath && Object.keys(s.decisions || {}).length > 0,
  ) ?? null
}

export async function getReviewSession(sessionId: string): Promise<ReviewSession | null> {
  if (!/^[a-zA-Z0-9_-]+$/.test(sessionId)) {
    throw createError({ statusCode: 400, message: 'Invalid sessionId format' })
  }
  return readJson<ReviewSession>(`data/review_sessions/${sessionId}.json`)
}

export async function saveReviewSession(session: ReviewSession): Promise<void> {
  if (!/^[a-zA-Z0-9_-]+$/.test(session.id)) {
    throw createError({ statusCode: 400, message: 'Invalid session id format' })
  }
  await writeJson(`data/review_sessions/${session.id}.json`, session)
}

export async function getLatestReviewSession(): Promise<ReviewSession | null> {
  const path = await findLatestFile('data/review_sessions/', '.json')
  if (!path) return null
  return readJson<ReviewSession>(path)
}

export async function saveReviewDecisions(sessionId: string, changes: Array<Record<string, unknown>>, reviewFilePath: string | null): Promise<void> {
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  await writeJson(`data/review_decisions_${timestamp}.json`, {
    sessionId,
    exportedAt: new Date().toISOString(),
    reviewFilePath,
    changes,
  })
}

export async function appendFeedback(entries: FeedbackEntry[]): Promise<void> {
  await appendJsonl('data/feedback.jsonl', entries)
}

// --- Review Sessions List ---

export async function getAllReviewSessions(): Promise<ReviewSession[]> {
  return cachedRead('all_review_sessions', async () => {
    const paths = await findAllFiles('data/review_sessions/', '.json')
    const results = await Promise.all(paths.map(p => readJson<ReviewSession>(p)))
    const sessions = results.filter((s): s is ReviewSession => s !== null)
    // Sort newest first
    sessions.sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    return sessions
  })
}

export interface ExportRecord {
  sessionId: string
  exportedAt: string
  changes: unknown[]
}

export async function getLatestExport(): Promise<{ exportedAt: string; count: number } | null> {
  return cachedRead('latest_export', async () => {
    const path = await findLatestFile('data/review_decisions_', '.json')
    if (!path) return null
    const data = await readJson<ExportRecord>(path)
    if (!data) return null
    return {
      exportedAt: data.exportedAt,
      count: Array.isArray(data.changes) ? data.changes.length : 0,
    }
  })
}

// --- Queue Stats API ---

export interface QueueStatsEntry {
  date: string
  totalChanges: number
  byCategory?: Record<string, number>
}

export async function getQueueStats(): Promise<QueueStatsEntry[]> {
  return cachedRead('queue_stats', async () => {
    const data = await readJson<QueueStatsEntry[]>('data/queue_stats.json')
    return data ?? []
  })
}

// --- Pipeline Runs API ---

export interface PhaseDetail {
  elapsed_s: number
  changes_applied?: number
  changes_failed?: number
  changes_skipped?: number
  promoted?: number
  demoted?: number
  ai_cost_usd?: number
  ai_tokens?: number
  backup_elapsed_s?: number
  analyze_elapsed_s?: number
  fix_elapsed_s?: number
  fix_changes_applied?: number
}

export interface PipelineRun {
  date: string
  duration_seconds: number
  phases_completed: string[]
  queue_size: number
  errors: string[]
  changes_applied?: number
  changes_failed?: number
  phases?: Record<string, PhaseDetail>
}

export async function getPipelineRuns(): Promise<PipelineRun[]> {
  return cachedRead('pipeline_runs', async () => {
    const data = await readJson<PipelineRun[]>('data/pipeline_runs.json')
    return data ?? []
  })
}

// --- LinkedIn Signals API ---

export async function getLinkedInSignals(): Promise<{ signals: LinkedInSignal[]; generated: string | null }> {
  return cachedRead('linkedin_signals', async () => {
    const data = await readJson<LinkedInSignalsFile>('data/linkedin_signals.json')
    if (!data) return { signals: [], generated: null }
    return {
      signals: Object.values(data.signals),
      generated: data.generated,
    }
  })
}

// --- FollowUp Scores API ---

export async function getFollowUpScores(): Promise<{ scores: FollowUpScore[]; generated: string | null; stats: FollowUpStats | null }> {
  return cachedRead('followup_scores', async () => {
    const data = await readJson<FollowUpScoresFile>('data/followup_scores.json')
    if (!data) return { scores: [], generated: null, stats: null }
    return {
      scores: Object.values(data.scores),
      generated: data.generated,
      stats: data.stats,
    }
  })
}

// --- Contact Name Resolution ---

/**
 * Build a map of resourceName -> displayName from workplan and review files.
 * Used by analytics and changelog to show human-readable contact names.
 */
export async function getContactNameMap(): Promise<Map<string, string>> {
  return cachedRead('contact_names', async () => {
    const map = new Map<string, string>()

    // Source 1: workplan batches (most comprehensive)
    const workplan = await getLatestWorkplan()
    if (workplan?.batches) {
      for (const batch of workplan.batches) {
        for (const contact of batch.contacts) {
          if (contact.resourceName && contact.displayName) {
            map.set(contact.resourceName, contact.displayName)
          }
        }
      }
    }

    // Source 2: LinkedIn signals (has name field)
    const { signals } = await getLinkedInSignals()
    for (const signal of signals) {
      if (signal.resourceName && signal.name && !map.has(signal.resourceName)) {
        map.set(signal.resourceName, signal.name)
      }
    }

    return map
  }) as Promise<Map<string, string>>
}

// --- Pipeline Config ---

import type { PipelineConfig } from './types'

export async function getPipelineConfig(): Promise<PipelineConfig | null> {
  return readJson<PipelineConfig>('data/pipeline_config.json')
}

export async function savePipelineConfig(config: PipelineConfig): Promise<void> {
  await writeJson('data/pipeline_config.json', { ...config, updatedAt: new Date().toISOString() })
  clearCache()
}

// --- Cache Control ---

/** Clear all cached data to force fresh reads from GCS */
export function clearCache(): void {
  cache.clear()
}

// --- Cost Estimation ---

/** Estimate AI review cost (Haiku: ~$0.80/1M input, $4/1M output, ~500 tokens/review) */
export function estimateAICost(reviewedCount: number): number {
  return Math.round(reviewedCount * 500 * (0.80 + 4.0) / 2 / 1_000_000 * 100) / 100
}
