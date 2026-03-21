import type { FollowUpResponse } from '../utils/types'
import { getFollowUpScores } from '../utils/gcs'
import { isDemoMode, maskFollowUpScore } from '../utils/demo'

export default defineEventHandler(async (event): Promise<FollowUpResponse> => {
  const demo = await isDemoMode(event)
  const { scores, generated, stats } = await getFollowUpScores()

  return {
    scores: demo ? scores.map(maskFollowUpScore) : scores,
    generated,
    stats,
  }
})
