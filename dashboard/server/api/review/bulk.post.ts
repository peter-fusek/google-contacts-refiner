import type { ReviewDecision, ReviewSession, FeedbackEntry } from '../../utils/types'
import { getReviewSession, saveReviewSession, appendFeedback } from '../../utils/gcs'
import { isDemoMode } from '../../utils/demo'

interface BulkRequest {
  sessionId: string
  reviewFilePath: string
  decision: 'approved' | 'rejected'
  changeIds: string[]
  changeMeta?: Record<string, { ruleCategory: string; field: string; old: string; suggested: string; confidence: number }>
}

export default defineEventHandler(async (event) => {
  if (await isDemoMode(event)) {
    throw createError({ statusCode: 403, message: 'Read-only demo mode' })
  }

  const body = await readBody<BulkRequest>(event)
  if (!body?.sessionId || !body?.changeIds?.length) {
    throw createError({ statusCode: 400, message: 'Missing sessionId or changeIds' })
  }
  if (!['approved', 'rejected'].includes(body.decision)) {
    throw createError({ statusCode: 400, message: 'Invalid decision value' })
  }

  let session = await getReviewSession(body.sessionId)
  if (!session) {
    session = {
      id: body.sessionId,
      reviewFilePath: body.reviewFilePath,
      createdAt: new Date().toISOString(),
      decisions: {},
      stats: { total: 0, approved: 0, rejected: 0, edited: 0, skipped: 0 },
    }
  }

  const now = new Date().toISOString()
  const feedbackEntries: FeedbackEntry[] = []

  for (const changeId of body.changeIds) {
    const old = session.decisions[changeId]
    if (old) {
      session.stats[old.decision]--
    }

    const decision: ReviewDecision = {
      changeId,
      decision: body.decision,
      decidedAt: now,
    }
    session.decisions[changeId] = decision
    session.stats[body.decision]++

    const meta = body.changeMeta?.[changeId]
    if (meta) {
      feedbackEntries.push({
        timestamp: now,
        type: body.decision === 'approved' ? 'approval' : 'rejection',
        ruleCategory: meta.ruleCategory,
        field: meta.field,
        old: meta.old,
        suggested: meta.suggested,
        finalValue: meta.suggested,
        confidence: meta.confidence,
      })
    }
  }

  session.stats.total = Object.keys(session.decisions).length

  const saves: Promise<void>[] = [saveReviewSession(session)]
  if (feedbackEntries.length) {
    saves.push(appendFeedback(feedbackEntries))
  }
  await Promise.all(saves)

  return { session }
})
