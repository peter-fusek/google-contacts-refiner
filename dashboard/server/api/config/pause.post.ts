import { isDemoMode } from '../../utils/demo'
import { writeJson } from '../../utils/gcs'

export default defineEventHandler(async (event) => {
  if (await isDemoMode(event)) {
    throw createError({ statusCode: 403, message: 'Read-only demo mode' })
  }

  await writeJson('data/pipeline_paused.json', {
    paused: true,
    pausedAt: new Date().toISOString(),
    reason: 'Emergency stop from dashboard',
  })

  return { paused: true }
})
