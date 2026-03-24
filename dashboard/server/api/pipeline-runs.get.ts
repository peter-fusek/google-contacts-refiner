import { getPipelineRuns } from '../utils/gcs'
import { isDemoMode } from '../utils/demo'

export default defineEventHandler(async (event) => {
  if (await isDemoMode(event)) {
    return []
  }

  const runs = await getPipelineRuns()
  return [...runs].reverse()
})
