import type { StatusResponse } from '../utils/types'
import {
  getCheckpoint,
  getAIReviewCheckpoint,
  getChangelogWithMarkers,
  isBatchMarker,
  getPipelineRuns,
} from '../utils/gcs'
import { isDemoMode } from '../utils/demo'

export default defineEventHandler(async (event): Promise<StatusResponse> => {
  if (await isDemoMode(event)) {
    // Show realistic demo data so visitors see a functioning dashboard
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000)
    const completed = new Date(yesterday.getTime() + 142 * 1000)
    return {
      status: 'completed' as const, phase: 'phase2' as const,
      currentBatch: 11, totalBatches: 11,
      contactsProcessed: 5478, contactsTotal: 5478,
      eta: null,
      lastRun: {
        startedAt: yesterday.toISOString(),
        completedAt: completed.toISOString(),
        duration: 142,
        changesApplied: 37,
        changesFailed: 2,
        cost: 0.018,
      },
      aiReview: { reviewed: 14, total: 14, promoted: 9, demoted: 5 },
    }
  }
  const [checkpoint, aiCheckpoint, changelog, pipelineRuns] = await Promise.all([
    getCheckpoint(),
    getAIReviewCheckpoint(),
    getChangelogWithMarkers(),
    getPipelineRuns(),
  ])

  // Determine phase and status
  let status: StatusResponse['status'] = 'idle'
  let phase: StatusResponse['phase'] = 'idle'

  if (checkpoint) {
    if (checkpoint.status === 'in_progress' || checkpoint.status === 'initialized') {
      status = 'running'
      phase = 'phase1'
    } else if (checkpoint.status === 'completed') {
      status = 'completed'
      phase = 'phase1'
    } else if (checkpoint.status === 'failed') {
      status = 'failed'
      phase = 'phase1'
    }
  }

  if (aiCheckpoint?.status === 'in_progress') {
    status = 'running'
    phase = 'phase2'
  }

  // Count successes/failures from batch markers — latest session only
  // (without session filtering, these sum ALL historical runs → misleading)
  const currentSessionId = checkpoint?.session_id ?? null
  let changesApplied = 0
  let changesFailed = 0
  for (const entry of changelog) {
    if (isBatchMarker(entry) && entry.type === 'batch_end') {
      if (currentSessionId && entry.session_id !== currentSessionId) continue
      changesApplied += entry.success ?? 0
      changesFailed += entry.failed ?? 0
    }
  }

  // Calculate duration
  let duration: number | null = null
  if (checkpoint?.started_at && checkpoint?.completed_at) {
    duration = Math.round(
      (new Date(checkpoint.completed_at).getTime() - new Date(checkpoint.started_at).getTime()) / 1000,
    )
  }

  // Get actual cost from latest pipeline run (sum of all phases)
  const latestRun = pipelineRuns.length ? pipelineRuns[pipelineRuns.length - 1] : null
  let totalCost: number | null = null
  if (latestRun?.phases) {
    let sum = 0
    for (const phaseDetail of Object.values(latestRun.phases)) {
      if (phaseDetail.ai_cost_usd) sum += phaseDetail.ai_cost_usd
    }
    if (sum > 0) totalCost = Math.round(sum * 1000) / 1000
  }

  return {
    status,
    phase,
    currentBatch: checkpoint?.last_completed_batch ?? 0,
    totalBatches: checkpoint?.total_batches ?? 0,
    contactsProcessed: checkpoint?.contacts_processed ?? 0,
    contactsTotal: checkpoint?.contacts_total ?? 0,
    eta: null, // Could calculate from batch timing
    lastRun: {
      startedAt: checkpoint?.started_at ?? null,
      completedAt: checkpoint?.completed_at ?? null,
      duration,
      changesApplied,
      changesFailed,
      cost: totalCost,
    },
    aiReview: aiCheckpoint
      ? {
          reviewed: aiCheckpoint.last_reviewed,
          total: aiCheckpoint.total,
          promoted: aiCheckpoint.promoted,
          demoted: aiCheckpoint.demoted,
        }
      : null,
  }
})
