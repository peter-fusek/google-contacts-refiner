import type { ChangelogResponse } from '../utils/types'
import { getChangelog } from '../utils/gcs'
import { isDemoMode, maskChangelogEntry } from '../utils/demo'

export default defineEventHandler(async (event): Promise<ChangelogResponse> => {
  const demo = await isDemoMode(event)
  const query = getQuery(event)
  const page = Math.max(1, Number(query.page) || 1)
  const pageSize = Math.min(100, Math.max(10, Number(query.pageSize) || 50))
  const search = (query.search as string || '').toLowerCase()
  const field = (query.field as string || '').toLowerCase()
  const confidence = (query.confidence as string || '').toLowerCase()

  let entries = await getChangelog()

  // Filter
  if (search) {
    entries = entries.filter(e =>
      e.field.toLowerCase().includes(search)
      || (e.old ?? '').toLowerCase().includes(search)
      || (e.new ?? '').toLowerCase().includes(search)
      || (e.reason ?? '').toLowerCase().includes(search)
      || (e.resourceName ?? '').toLowerCase().includes(search),
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

  // Sort newest first
  entries.sort((a, b) => b.timestamp.localeCompare(a.timestamp))

  const total = entries.length
  const offset = (page - 1) * pageSize
  const paged = entries.slice(offset, offset + pageSize)

  return { entries: demo ? paged.map(maskChangelogEntry) : paged, total, page, pageSize }
})
