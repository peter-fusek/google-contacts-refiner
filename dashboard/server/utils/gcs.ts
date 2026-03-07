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
} from './types'

let storage: Storage | null = null

function getStorage(): Storage {
  if (storage) return storage

  // Try GOOGLE_APPLICATION_CREDENTIALS first (local dev with key file)
  if (process.env.GOOGLE_APPLICATION_CREDENTIALS) {
    storage = new Storage()
    console.log('[GCS] Using GOOGLE_APPLICATION_CREDENTIALS:', process.env.GOOGLE_APPLICATION_CREDENTIALS)
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
      console.log('[GCS] Using credentials from env var - project:', creds.project_id,
        'pk_len:', creds.private_key?.length, 'has_newlines:', creds.private_key?.includes('\n'))
      return storage
    } catch (err) {
      console.error('[GCS] Failed to parse SA credentials:', (err as Error).message)
    }
  }

  // Fallback to ADC
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
const CACHE_TTL = 10_000 // 10 seconds

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
    console.error(`[GCS] readJson(${path}) failed:`, (err as Error).message)
    return null
  }
}

async function findLatestFile(prefix: string, extension: string): Promise<string | null> {
  try {
    const [files] = await getBucket().getFiles({ prefix })
    const matching = files
      .filter(f => f.name.endsWith(extension))
      .sort((a, b) => b.name.localeCompare(a.name))
    return matching[0]?.name ?? null
  } catch (err) {
    console.error(`[GCS] findLatestFile(${prefix}) failed:`, (err as Error).message)
    return null
  }
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
  try {
    const [files] = await getBucket().getFiles({ prefix })
    return files
      .filter(f => f.name.endsWith(extension))
      .sort((a, b) => a.name.localeCompare(b.name))
      .map(f => f.name)
  } catch (err) {
    console.error(`[GCS] findAllFiles(${prefix}) failed:`, (err as Error).message)
    return []
  }
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
        } catch {
          // Skip malformed lines
        }
      }
    } catch {
      // Skip unreadable files
    }
  }
  return all
}

export async function getChangelog(): Promise<ChangelogEntry[]> {
  return cachedRead('changelog', async () => {
    const all = await readAllChangelogs()
    return all.filter((e): e is ChangelogEntry => !('type' in e))
  })
}

export async function getChangelogWithMarkers(): Promise<ChangelogLine[]> {
  return cachedRead('changelog_full', readAllChangelogs)
}

export function isBatchMarker(entry: ChangelogLine): entry is BatchMarker {
  return 'type' in entry
}

// --- Write API ---

async function writeJson(path: string, data: unknown): Promise<void> {
  const content = JSON.stringify(data, null, 2)
  await getBucket().file(path).save(content, {
    contentType: 'application/json',
    resumable: false,
  })
}

async function appendJsonl(path: string, entries: unknown[]): Promise<void> {
  const lines = entries.map(e => JSON.stringify(e)).join('\n') + '\n'

  const file = getBucket().file(path)
  let existing = ''
  try {
    const [content] = await file.download()
    existing = content.toString('utf-8')
  } catch {
    // File doesn't exist yet
  }

  await file.save(existing + lines, {
    contentType: 'application/x-ndjson',
    resumable: false,
  })
}

// --- Review API ---

export async function getLatestReviewFile(): Promise<{ path: string; data: unknown } | null> {
  // Use specific prefix to avoid matching review_sessions/ and review_decisions_
  try {
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
  } catch (err) {
    console.error('[GCS] getLatestReviewFile failed:', (err as Error).message)
    return null
  }
}

export async function getReviewSession(sessionId: string): Promise<ReviewSession | null> {
  return readJson<ReviewSession>(`data/review_sessions/${sessionId}.json`)
}

export async function saveReviewSession(session: ReviewSession): Promise<void> {
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
