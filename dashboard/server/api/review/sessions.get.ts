import { getAllReviewSessions, getLatestExport } from '../../utils/gcs'
import { isDemoMode } from '../../utils/demo'

export default defineEventHandler(async (event) => {
  if (await isDemoMode(event)) {
    return { sessions: [], lastExport: null }
  }

  const [sessions, lastExport] = await Promise.all([
    getAllReviewSessions(),
    getLatestExport(),
  ])

  return {
    sessions: sessions.map(s => ({
      id: s.id,
      createdAt: s.createdAt,
      stats: s.stats,
    })),
    lastExport,
  }
})
