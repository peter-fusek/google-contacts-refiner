import type { AnalyticsResponse } from '../utils/types'
import {
  getChangelog,
  getChangelogWithMarkers,
  getAIReviewCheckpoint,
  isBatchMarker,
} from '../utils/gcs'
import { isDemoMode, maskTopContact } from '../utils/demo'

function fieldCategory(field: string): string {
  if (field.startsWith('names')) return 'names'
  if (field.startsWith('phoneNumbers')) return 'phones'
  if (field.startsWith('emailAddresses')) return 'emails'
  if (field.startsWith('addresses')) return 'addresses'
  if (field.startsWith('organizations')) return 'organizations'
  if (field.startsWith('urls')) return 'urls'
  if (field.startsWith('birthdays') || field.startsWith('events')) return 'dates'
  return 'other'
}

export default defineEventHandler(async (event): Promise<AnalyticsResponse> => {
  const demo = await isDemoMode(event)
  const [entries, fullLog, aiCheckpoint] = await Promise.all([
    getChangelog(),
    getChangelogWithMarkers(),
    getAIReviewCheckpoint(),
  ])

  // By field type
  const byField: Record<string, number> = {}
  for (const e of entries) {
    const cat = fieldCategory(e.field)
    byField[cat] = (byField[cat] ?? 0) + 1
  }

  // By confidence
  const byConfidence = { high: 0, medium: 0, low: 0 }
  for (const e of entries) {
    const c = e.confidence?.toLowerCase()
    if (c === 'high') byConfidence.high++
    else if (c === 'medium') byConfidence.medium++
    else byConfidence.low++
  }

  // Success/failed from batch markers
  let totalChanges = 0
  let totalFailed = 0
  for (const line of fullLog) {
    if (isBatchMarker(line) && line.type === 'batch_end') {
      totalChanges += line.success ?? 0
      totalFailed += line.failed ?? 0
    }
  }

  const successRate = totalChanges + totalFailed > 0
    ? Math.round((totalChanges / (totalChanges + totalFailed)) * 100)
    : 0

  // Daily runs (group by date)
  const dailyMap = new Map<string, { changes: number; failed: number }>()
  for (const line of fullLog) {
    if (isBatchMarker(line) && line.type === 'batch_end') {
      const date = line.timestamp.slice(0, 10)
      const existing = dailyMap.get(date) ?? { changes: 0, failed: 0 }
      existing.changes += line.success ?? 0
      existing.failed += line.failed ?? 0
      dailyMap.set(date, existing)
    }
  }
  const dailyRuns = Array.from(dailyMap.entries())
    .map(([date, stats]) => ({ date, ...stats }))
    .sort((a, b) => a.date.localeCompare(b.date))

  // Top contacts by number of changes
  const contactMap = new Map<string, { name: string; changes: number }>()
  for (const e of entries) {
    const existing = contactMap.get(e.resourceName) ?? { name: e.resourceName, changes: 0 }
    existing.changes++
    contactMap.set(e.resourceName, existing)
  }
  const topContacts = Array.from(contactMap.values())
    .sort((a, b) => b.changes - a.changes)
    .slice(0, 10)

  // Estimated cost
  const aiReviewed = aiCheckpoint?.last_reviewed ?? 0
  const estimatedCost = Math.round(aiReviewed * 500 * (0.80 + 4.0) / 2 / 1_000_000 * 100) / 100

  return {
    byField,
    byConfidence,
    successRate,
    totalChanges,
    totalFailed,
    dailyRuns,
    topContacts: demo ? topContacts.map(maskTopContact) : topContacts,
    estimatedCost,
  }
})
