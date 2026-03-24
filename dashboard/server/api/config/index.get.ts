import type { ConfigResponse } from '../utils/types'
import { isDemoMode } from '../../utils/demo'

export default defineEventHandler(async (event): Promise<ConfigResponse> => {
  const demo = await isDemoMode(event)

  return {
    batchSize: 50,
    confidenceHigh: 0.90,
    confidenceMedium: 0.60,
    aiModel: demo ? 'claude-haiku' : 'claude-haiku-4-5-20251001',
    aiCostLimit: 3.00,
    autoMaxChanges: 200,
    autoThreshold: 0.90,
    environment: 'cloud',
    schedulerStatus: 'enabled',
  }
})
