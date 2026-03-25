import type { PipelineConfig } from '../../utils/types'
import { isDemoMode } from '../../utils/demo'
import { savePipelineConfig } from '../../utils/gcs'

export default defineEventHandler(async (event) => {
  if (await isDemoMode(event)) {
    throw createError({ statusCode: 403, message: 'Read-only demo mode' })
  }

  const body = await readBody<PipelineConfig>(event)
  if (!body || typeof body !== 'object') {
    throw createError({ statusCode: 400, message: 'Invalid request body' })
  }

  // Validate ranges
  const errors: string[] = []
  if (body.batchSize !== undefined) {
    if (!Number.isInteger(body.batchSize) || body.batchSize < 10 || body.batchSize > 500)
      errors.push('batchSize must be an integer between 10 and 500')
  }
  if (body.confidenceHigh !== undefined) {
    if (typeof body.confidenceHigh !== 'number' || body.confidenceHigh < 0.50 || body.confidenceHigh > 1.00)
      errors.push('confidenceHigh must be between 0.50 and 1.00')
  }
  if (body.confidenceMedium !== undefined) {
    if (typeof body.confidenceMedium !== 'number' || body.confidenceMedium < 0.30 || body.confidenceMedium > 0.99)
      errors.push('confidenceMedium must be between 0.30 and 0.99')
  }
  if (body.confidenceHigh !== undefined && body.confidenceMedium !== undefined) {
    if (body.confidenceMedium >= body.confidenceHigh)
      errors.push('confidenceMedium must be less than confidenceHigh')
  }
  if (body.aiCostLimit !== undefined) {
    if (typeof body.aiCostLimit !== 'number' || body.aiCostLimit < 0.10 || body.aiCostLimit > 50.00)
      errors.push('aiCostLimit must be between 0.10 and 50.00')
  }
  if (body.autoThreshold !== undefined) {
    if (typeof body.autoThreshold !== 'number' || body.autoThreshold < 0.50 || body.autoThreshold > 1.00)
      errors.push('autoThreshold must be between 0.50 and 1.00')
  }
  if (body.autoMaxChanges !== undefined) {
    if (!Number.isInteger(body.autoMaxChanges) || body.autoMaxChanges < 1 || body.autoMaxChanges > 1000)
      errors.push('autoMaxChanges must be an integer between 1 and 1000')
  }

  if (errors.length > 0) {
    throw createError({ statusCode: 400, message: errors.join('; ') })
  }

  // Only save editable fields
  const config: PipelineConfig = {
    batchSize: body.batchSize,
    confidenceHigh: body.confidenceHigh,
    confidenceMedium: body.confidenceMedium,
    aiCostLimit: body.aiCostLimit,
    autoMaxChanges: body.autoMaxChanges,
    autoThreshold: body.autoThreshold,
  }

  try {
    await savePipelineConfig(config)
  } catch (err) {
    console.error('[config/save] GCS write failed:', (err as Error).message)
    throw createError({ statusCode: 500, message: 'Failed to save configuration' })
  }

  return { saved: true }
})
