import type { H3Event } from 'h3'
import type { ChangelogEntry, ReviewChange, LinkedInSignal, FollowUpScore } from './types'

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
 * Mask a full name: "Peter Fusek" → "Peter F."
 */
export function maskFullName(fullName: string): string {
  const parts = fullName.split(' ')
  if (parts.length <= 1) return fullName
  parts[parts.length - 1] = maskLastName(parts[parts.length - 1]!)
  return parts.join(' ')
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

  // Contact field (tobedeleted entries) — the old value is the contact name
  if (f === 'contact') {
    return maskName(value)
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
  // Mask displayName — for single-name contacts (tobedeleted), mask the whole name
  const nameParts = (change.displayName || '').trim().split(/\s+/)
  let maskedName: string
  if (nameParts.length > 1) {
    nameParts[nameParts.length - 1] = maskLastName(nameParts[nameParts.length - 1]!)
    maskedName = nameParts.join(' ')
  } else {
    maskedName = maskName(change.displayName || '')
  }

  return {
    ...change,
    resourceName: '***',
    displayName: maskedName,
    old: maskFieldValue(change.field, change.old),
    new: maskFieldValue(change.field, change.new),
  }
}

/**
 * Mask a generic name/string: show first 3 chars + "***"
 */
function maskName(value: string): string {
  if (!value || value.length <= 3) return '***'
  return value.substring(0, 3) + '***'
}

/**
 * Mask PII in a LinkedIn signal.
 */
export function maskLinkedInSignal(signal: LinkedInSignal): LinkedInSignal {
  const nameParts = (signal.name || '').trim().split(/\s+/)
  if (nameParts.length > 1) {
    nameParts[nameParts.length - 1] = maskLastName(nameParts[nameParts.length - 1]!)
  }
  return {
    ...signal,
    resourceName: '***',
    name: nameParts.join(' '),
    linkedin_url: 'https://www.linkedin.com/in/***',
  }
}

export function maskTopContact<T extends { name: string }>(contact: T): T {
  return { ...contact, name: maskName(contact.name), resourceName: '***' }
}

/**
 * Mask PII in a FollowUp score.
 */
export function maskFollowUpScore(score: FollowUpScore): FollowUpScore {
  const nameParts = (score.name || '').trim().split(/\s+/)
  if (nameParts.length > 1) {
    nameParts[nameParts.length - 1] = maskLastName(nameParts[nameParts.length - 1]!)
  }
  return {
    ...score,
    resourceName: '***',
    name: nameParts.join(' '),
    contact: {
      ...score.contact,
      emails: score.contact.emails.map(maskEmail),
      urls: score.contact.urls.map(u => ({ ...u, url: u.type === 'linkedin' ? 'https://www.linkedin.com/in/***' : '***' })),
    },
    linkedin: score.linkedin ? {
      ...score.linkedin,
      url: 'https://www.linkedin.com/in/***',
      signal_text: score.linkedin.signal_text ? '[LinkedIn signal — hidden in demo]' : null,
      headline: score.linkedin.headline ? '[Headline — hidden in demo]' : null,
    } : null,
    followup_prompt: score.followup_prompt ? '[Reconnect prompt — hidden in demo]' : null,
  }
}
