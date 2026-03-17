import type { ReviewDecision, ReviewSession, FeedbackEntry } from '../../utils/types'
import { getReviewSession, saveReviewSession, appendFeedback } from '../../utils/gcs'
import { isDemoMode } from '../../utils/demo'

interface DecideRequest {
  sessionId: string
  reviewFilePath: string
  decisions: ReviewDecision[]
  // Change metadata for feedback entries
  changeMeta?: Record<string, { ruleCategory: string; field: string; old: string; suggested: string; confidence: number }>
}

export default defineEventHandler(async (event) => {
  if (await isDemoMode(event)) {
    throw createError({ statusCode: 403, message: 'Read-only demo mode' })
  }

  const body = await readBody<DecideRequest>(event)
  if (!body?.sessionId || !body?.decisions?.length) {
    throw createError({ statusCode: 400, message: 'Missing sessionId or decisions' })
  }

  // Load or create session
  let session: ReviewSession | null = null
  try {
    session = await getReviewSession(body.sessionId)
  } catch (err) {
    console.error(`[Review] Failed to load session ${body.sessionId}:`, (err as Error).message)
    // Proceed with new session instead of crashing
  }
  if (!session) {
    session = {
      id: body.sessionId,
      reviewFilePath: body.reviewFilePath,
      createdAt: new Date().toISOString(),
      decisions: {},
      stats: { total: 0, approved: 0, rejected: 0, edited: 0, skipped: 0 },
    }
  }

  // Validate decisions
  const validDecisions = ['approved', 'rejected', 'edited', 'skipped']
  for (const d of body.decisions) {
    if (!validDecisions.includes(d.decision)) {
      throw createError({ statusCode: 400, message: `Invalid decision: ${d.decision}` })
    }
  }

  // Apply decisions
  const feedbackEntries: FeedbackEntry[] = []
  for (const d of body.decisions) {
    // Update stats: undo old decision if exists
    const old = session.decisions[d.changeId]
    if (old) {
      session.stats[old.decision]--
    }

    session.decisions[d.changeId] = d
    session.stats[d.decision]++
    session.stats.total = Object.keys(session.decisions).length

    // Build feedback entry
    const meta = body.changeMeta?.[d.changeId]
    if (meta && d.decision !== 'skipped') {
      feedbackEntries.push({
        timestamp: d.decidedAt,
        type: d.decision === 'approved' ? 'approval' : d.decision === 'rejected' ? 'rejection' : 'edit',
        ruleCategory: meta.ruleCategory,
        field: meta.field,
        old: meta.old,
        suggested: meta.suggested,
        finalValue: d.editedValue ?? meta.suggested,
        confidence: meta.confidence,
      })
    }
  }

  // Save session (critical) and feedback (non-fatal)
  try {
    await saveReviewSession(session)
  } catch (err) {
    console.error('[Review] saveReviewSession failed:', (err as Error).message, (err as Error).stack)
    throw createError({ statusCode: 500, message: `Save failed: ${(err as Error).message}` })
  }
  if (feedbackEntries.length) {
    appendFeedback(feedbackEntries).catch((err) => {
      console.error('[Review] Feedback append failed (non-fatal):', (err as Error).message)
    })
  }

  return { session }
})
