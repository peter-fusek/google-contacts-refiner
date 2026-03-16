import { getPipelineRuns } from '../utils/gcs'

export default defineEventHandler(async () => {
  const runs = await getPipelineRuns()
  // Return latest first
  return [...runs].reverse()
})
