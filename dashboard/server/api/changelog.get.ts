import type { ChangelogEntry } from '../utils/types'
import { getChangelog, getContactNameMap } from '../utils/gcs'
import { isDemoMode, maskChangelogEntry } from '../utils/demo'

interface ContactGroup {
  resourceName: string
  displayName: string
  latestTimestamp: string
  changeCount: number
  entries: ChangelogEntry[]
}

export interface GroupedChangelogResponse {
  groups: ContactGroup[]
  total: number
  totalGroups: number
  page: number
  pageSize: number
}

export default defineEventHandler(async (event): Promise<GroupedChangelogResponse> => {
  const demo = await isDemoMode(event)
  const query = getQuery(event)
  const page = Math.max(1, Number(query.page) || 1)
  const pageSize = Math.min(50, Math.max(5, Number(query.pageSize) || 20))
  const search = (query.search as string || '').toLowerCase()
  const field = (query.field as string || '').toLowerCase()
  const confidence = (query.confidence as string || '').toLowerCase()
  const sessionId = (query.sessionId as string || '')

  const [allEntries, nameMap] = await Promise.all([
    getChangelog(),
    getContactNameMap(),
  ])

  let entries = allEntries

  // Filter by session ID (for pipeline run drill-down)
  if (sessionId) {
    entries = entries.filter(e => e.session_id === sessionId)
  }

  // Filter
  if (search) {
    entries = entries.filter(e =>
      e.field.toLowerCase().includes(search)
      || (e.old ?? '').toLowerCase().includes(search)
      || (e.new ?? '').toLowerCase().includes(search)
      || (e.reason ?? '').toLowerCase().includes(search)
      || (nameMap.get(e.resourceName) ?? e.resourceName).toLowerCase().includes(search),
    )
  }

  if (field) {
    entries = entries.filter((e) => {
      const f = e.field.toLowerCase()
      return f.startsWith(field) || f.includes(field)
    })
  }

  if (confidence) {
    entries = entries.filter(e => e.confidence === confidence)
  }

  // Enrich nameMap from changelog entries (covers contacts not in workplan/LinkedIn)
  for (const e of entries) {
    if (!nameMap.has(e.resourceName) && e.field === 'names[0].displayName' && e.new) {
      nameMap.set(e.resourceName, e.new)
    }
  }

  // Group by contact
  const groupMap = new Map<string, ContactGroup>()
  for (const e of entries) {
    let group = groupMap.get(e.resourceName)
    if (!group) {
      const resolvedName = nameMap.get(e.resourceName) || e.resourceName.replace('people/', '')
      group = {
        resourceName: e.resourceName,
        displayName: resolvedName,
        latestTimestamp: e.timestamp,
        changeCount: 0,
        entries: [],
      }
      groupMap.set(e.resourceName, group)
    }
    group.entries.push(e)
    group.changeCount++
    if (e.timestamp > group.latestTimestamp) group.latestTimestamp = e.timestamp
  }

  // Sort entries within each group by time (newest first)
  for (const group of groupMap.values()) {
    group.entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp))
  }

  // Sort groups by most recent change (newest first)
  const allGroups = Array.from(groupMap.values())
    .sort((a, b) => b.latestTimestamp.localeCompare(a.latestTimestamp))

  const total = entries.length
  const totalGroups = allGroups.length
  const offset = (page - 1) * pageSize
  const pagedGroups = allGroups.slice(offset, offset + pageSize)

  // Mask in demo mode
  if (demo) {
    for (const group of pagedGroups) {
      group.resourceName = '***'
      const parts = group.displayName.trim().split(/\s+/)
      if (parts.length > 1) {
        parts[parts.length - 1] = parts[parts.length - 1]!.charAt(0) + '.'
      } else if (parts[0] && parts[0].length > 3) {
        parts[0] = parts[0].substring(0, 3) + '***'
      }
      group.displayName = parts.join(' ')
      group.entries = group.entries.map(maskChangelogEntry)
    }
  }

  return { groups: pagedGroups, total, totalGroups, page, pageSize }
})
