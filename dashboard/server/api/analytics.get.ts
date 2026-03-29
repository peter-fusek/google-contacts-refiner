import type { AnalyticsResponse, ChangelogEntry } from '../utils/types'
import {
  getChangelogWithMarkers,
  getAIReviewCheckpoint,
  getContactNameMap,
  isBatchMarker,
  getPipelineRuns,
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
  const [fullLog, aiCheckpoint, nameMap, pipelineRuns] = await Promise.all([
    getChangelogWithMarkers(),
    getAIReviewCheckpoint(),
    getContactNameMap(),
    getPipelineRuns(),
  ])

  // Derive deduplicated entries from full log (avoids duplicate GCS read)
  const seen = new Set<string>()
  const entries: ChangelogEntry[] = []
  for (const line of fullLog) {
    if (isBatchMarker(line)) continue
    const e = line as ChangelogEntry
    const key = `${e.resourceName}|${e.field}|${e.old}|${e.new}`
    if (!seen.has(key)) {
      seen.add(key)
      entries.push(e)
    }
  }

  // By field type + confidence + reason drill-down in a single pass
  const byField: Record<string, number> = {}
  const byConfidence = { high: 0, medium: 0, low: 0 }
  const reasonMap = new Map<string, Map<string, number>>() // cat -> reason -> count
  for (const e of entries) {
    const cat = fieldCategory(e.field)
    byField[cat] = (byField[cat] ?? 0) + 1
    const c = e.confidence?.toLowerCase()
    if (c === 'high') byConfidence.high++
    else if (c === 'medium') byConfidence.medium++
    else byConfidence.low++
    // Aggregate reasons per field category
    if (!reasonMap.has(cat)) reasonMap.set(cat, new Map())
    const reasons = reasonMap.get(cat)!
    const reason = e.reason || 'unknown'
    reasons.set(reason, (reasons.get(reason) ?? 0) + 1)
  }
  // Build drill-down detail
  const byFieldDetail: Record<string, { count: number; reasons: Array<{ text: string; count: number }> }> = {}
  for (const [cat, reasons] of reasonMap) {
    byFieldDetail[cat] = {
      count: byField[cat] ?? 0,
      reasons: Array.from(reasons.entries())
        .map(([text, count]) => ({ text, count }))
        .sort((a, b) => b.count - a.count)
        .slice(0, 10),
    }
  }

  // Aggregate batch markers in a single pass — build daily breakdown + find latest day
  const dailyMap = new Map<string, { changes: number; failed: number }>()
  let latestDate = ''
  for (const line of fullLog) {
    if (isBatchMarker(line) && line.type === 'batch_end') {
      const date = line.timestamp.slice(0, 10)
      if (date > latestDate) latestDate = date
      const existing = dailyMap.get(date) ?? { changes: 0, failed: 0 }
      existing.changes += line.success ?? 0
      existing.failed += line.failed ?? 0
      dailyMap.set(date, existing)
    }
  }

  // totalChanges/totalFailed = latest day only (not cumulative across all history)
  const latestDay = dailyMap.get(latestDate)
  const totalChanges = latestDay?.changes ?? 0
  const totalFailed = latestDay?.failed ?? 0
  const successRate = totalChanges + totalFailed > 0
    ? Math.round((totalChanges / (totalChanges + totalFailed)) * 100)
    : 0

  const dailyRuns = Array.from(dailyMap.entries())
    .map(([date, stats]) => ({ date, ...stats }))
    .sort((a, b) => a.date.localeCompare(b.date))

  // Top contacts by number of changes — resolve to display names
  const contactMap = new Map<string, { resourceName: string; name: string; changes: number; lastChanged: string }>()
  for (const e of entries) {
    const existing = contactMap.get(e.resourceName) ?? {
      resourceName: e.resourceName,
      name: nameMap.get(e.resourceName) || e.resourceName.replace('people/', ''),
      changes: 0,
      lastChanged: '',
    }
    existing.changes++
    if (e.timestamp > existing.lastChanged) existing.lastChanged = e.timestamp
    contactMap.set(e.resourceName, existing)
  }
  const topContacts = Array.from(contactMap.values())
    .sort((a, b) => b.changes - a.changes)
    .slice(0, 10)

  // Recently changed contacts (last 10 by timestamp)
  const recentlyChanged = Array.from(contactMap.values())
    .sort((a, b) => b.lastChanged.localeCompare(a.lastChanged))
    .slice(0, 10)

  // Actual cost from latest pipeline run (sum of all phases)
  const latestRun = pipelineRuns.length ? pipelineRuns[pipelineRuns.length - 1] : null
  let estimatedCost = 0
  if (latestRun?.phases) {
    for (const phaseDetail of Object.values(latestRun.phases)) {
      if (phaseDetail.ai_cost_usd) estimatedCost += phaseDetail.ai_cost_usd
    }
    estimatedCost = Math.round(estimatedCost * 1000) / 1000
  }

  return {
    byField,
    byFieldDetail: demo ? {} : byFieldDetail,
    byConfidence,
    successRate,
    totalChanges,
    totalFailed,
    dailyRuns,
    topContacts: demo ? topContacts.map(maskTopContact) : topContacts,
    recentlyChanged: demo ? recentlyChanged.map(maskTopContact) : recentlyChanged,
    estimatedCost,
  }
})
