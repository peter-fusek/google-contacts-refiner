import { getQueueStats } from '../utils/gcs'
import { isDemoMode } from '../utils/demo'

export default defineEventHandler(async (event) => {
  if (await isDemoMode(event)) {
    return []
  }

  return await getQueueStats()
})
