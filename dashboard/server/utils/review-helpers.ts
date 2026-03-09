import { createHash } from 'node:crypto'
import type { ReviewChange } from './types'

// Rule category extraction from reason strings
const RULE_PATTERNS: [string, RegExp][] = [
  ['diacritics', /diakritik/i],
  ['title_case', /veľkosti písmen|Title Case/i],
  ['phone_format', /tel\. čísla|normalizácia tel/i],
  ['phone_type', /typu tel/i],
  ['phone_duplicate', /duplicitné tel/i],
  ['email_normalize', /normalizácia email/i],
  ['email_invalid', /nevalidný.*email/i],
  ['email_duplicate', /duplicitný email/i],
  ['address_zip', /PSČ/i],
  ['address_country', /krajin/i],
  ['address_parse', /parsovanie adres/i],
  ['org_case', /organizáci/i],
  ['name_extract', /extrakcia.*mena|extrakcia.*Name/i],
  ['name_split', /rozdelenie/i],
  ['name_title', /extrakcia titul/i],
  ['company_in_name', /firma.*men|firmu/i],
  ['family_name_fix', /priezvisko/i],
  ['x500_dn', /X\.500 DN/i],
  ['org_from_email', /odhadnutá z email/i],
  ['country_from_zip', /krajiny z PSČ|kódu krajiny/i],
]

export function extractRuleCategory(reason: string): string {
  for (const [category, pattern] of RULE_PATTERNS) {
    if (pattern.test(reason)) return category
  }
  return 'other'
}

export function makeChangeId(resourceName: string, field: string, oldVal: string, newVal: string): string {
  return createHash('sha256')
    .update(`${resourceName}|${field}|${oldVal}|${newVal}`)
    .digest('hex')
    .slice(0, 12)
}

interface ReviewFileItem {
  resourceName: string
  displayName: string
  skipped_changes: Array<{
    field: string
    old: string
    new: string
    confidence: number
    reason: string
    extra?: Record<string, unknown>
  }>
}

interface ReviewFileData {
  generated: string
  total_items: number
  items: ReviewFileItem[]
}

export function parseReviewFile(data: unknown): ReviewChange[] {
  const file = data as ReviewFileData
  if (!file?.items?.length) return []

  const changes: ReviewChange[] = []
  for (const item of file.items) {
    for (const change of item.skipped_changes ?? []) {
      changes.push({
        id: makeChangeId(item.resourceName, change.field, change.old, change.new),
        resourceName: item.resourceName,
        displayName: item.displayName,
        field: change.field,
        old: change.old,
        new: change.new,
        confidence: change.confidence,
        reason: change.reason,
        ruleCategory: extractRuleCategory(change.reason),
        extra: change.extra,
      })
    }
  }
  return changes
}
