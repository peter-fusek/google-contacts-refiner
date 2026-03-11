import type { H3Event } from 'h3'
import type { ChangelogEntry, ReviewChange } from './types'

/**
 * Check if the current request is in demo mode (unauthenticated visitor).
 */
export async function isDemoMode(event: H3Event): Promise<boolean> {
  const session = await getUserSession(event)
  return !session?.user
}

/**
 * Mask an email address: "peter.fusek@gmail.com" → "pe***@gmail.com"
 */
function maskEmail(email: string): string {
  const atIdx = email.indexOf('@')
  if (atIdx <= 0) return '***'
  const local = email.substring(0, atIdx)
  const domain = email.substring(atIdx)
  const visible = Math.min(2, local.length)
  return local.substring(0, visible) + '***' + domain
}

/**
 * Mask a phone number: "+421 903 123 456" → "+421 9** *** 456"
 */
function maskPhone(phone: string): string {
  // Keep first 5 and last 3 characters, mask the rest
  const digits = phone.replace(/\D/g, '')
  if (digits.length <= 6) return '***'
  const clean = phone.trim()
  if (clean.length <= 8) return clean.substring(0, 3) + '***'
  return clean.substring(0, 5) + '***' + clean.substring(clean.length - 3)
}

/**
 * Mask a last name: "Fusek" → "F."
 */
function maskLastName(name: string): string {
  if (!name || name.length === 0) return name
  return name.charAt(0).toUpperCase() + '.'
}

/**
 * Mask an address: show only city/country, hide street details.
 */
function maskAddress(address: string): string {
  // Addresses are typically comma-separated; keep last 1-2 parts (city, country)
  const parts = address.split(',').map(p => p.trim())
  if (parts.length <= 1) return '***'
  if (parts.length === 2) return '*** , ' + parts[parts.length - 1]
  return '*** , ' + parts.slice(-2).join(', ')
}

/**
 * Mask a URL/social profile.
 */
function maskUrl(url: string): string {
  try {
    const u = new URL(url)
    return u.origin + '/***'
  }
  catch {
    return '***'
  }
}

/**
 * Determine if a field contains PII that needs masking and mask the value.
 */
function maskFieldValue(field: string, value: string): string {
  if (!value) return value
  const f = field.toLowerCase()

  // Names — mask last name parts
  if (f.includes('familyname') || f.includes('lastname') || f.includes('family_name')) {
    return maskLastName(value)
  }
  // Display name — mask the last word (assumed last name)
  if (f === 'displayname' || f === 'display_name') {
    const parts = value.trim().split(/\s+/)
    if (parts.length > 1) {
      parts[parts.length - 1] = maskLastName(parts[parts.length - 1]!)
    }
    return parts.join(' ')
  }

  // Emails
  if (f.includes('email')) {
    return maskEmail(value)
  }

  // Phones
  if (f.includes('phone')) {
    return maskPhone(value)
  }

  // Addresses
  if (f.includes('address') && !f.includes('email')) {
    return maskAddress(value)
  }

  // URLs / social profiles
  if (f.includes('url') || f.includes('website') || f.includes('profile')) {
    return maskUrl(value)
  }

  return value
}

/**
 * Mask PII in a changelog entry.
 */
export function maskChangelogEntry(entry: ChangelogEntry): ChangelogEntry {
  return {
    ...entry,
    resourceName: '***',
    old: maskFieldValue(entry.field, entry.old),
    new: maskFieldValue(entry.field, entry.new),
  }
}

/**
 * Mask PII in a review change.
 */
export function maskReviewChange(change: ReviewChange): ReviewChange {
  // Mask displayName (last name portion)
  const nameParts = (change.displayName || '').trim().split(/\s+/)
  if (nameParts.length > 1) {
    nameParts[nameParts.length - 1] = maskLastName(nameParts[nameParts.length - 1]!)
  }

  return {
    ...change,
    resourceName: '***',
    displayName: nameParts.join(' '),
    old: maskFieldValue(change.field, change.old),
    new: maskFieldValue(change.field, change.new),
  }
}

/**
 * Mask PII in analytics top contacts.
 */
export function maskTopContact(contact: { name: string; changes: number }): { name: string; changes: number } {
  return { name: '***', changes: contact.changes }
}
