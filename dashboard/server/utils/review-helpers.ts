import { createHash } from 'node:crypto'
import type { ReviewChange } from './types'

// Rule category extraction from reason strings
// NOTE: Order matters — first match wins. More specific patterns must come first.
const RULE_PATTERNS: [string, RegExp][] = [
  ['diacritics_given', /diacritics.*given/i],
  ['diacritics_family', /diacritics.*family/i],
  ['diacritics', /diacritics/i],
  ['org_case', /organization|letter casing \(org/i],
  ['title_case', /letter casing|Title Case/i],
  ['phone_format', /phone.*normalization|international format/i],
  ['phone_type', /phone.*type/i],
  ['phone_duplicate', /duplicate phone/i],
  ['email_normalize', /email.*normalization|email.*lowercase/i],
  ['email_invalid', /invalid.*email/i],
  ['email_duplicate', /duplicate email/i],
  ['address_zip', /postal code/i],
  ['address_country', /country/i],
  ['address_parse', /address.*pars/i],
  ['name_extract', /name.*extract|inferred.*name/i],
  ['name_split', /name.*split|split.*name/i],
  ['name_title', /title.*extract|prefix.*extract/i],
  ['company_in_name', /company.*name|company_in_name/i],
  ['family_name_fix', /family.*name|familyName/i],
  ['x500_dn', /X\.500 DN/i],
  ['org_from_email', /inferred from email|organization.*email/i],
  ['event_from_note', /from notes|extracted from notes/i],
  ['owner_email', /owner email/i],
  ['corporate_url', /corporate.*(LinkedIn|website|directory|social media)/i],
  ['shared_address', /shared HQ|shared.*office.*address/i],
  ['tobedeleted', /low-value contact|deletion candidate/i],
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
