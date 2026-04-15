import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { LICRMData, LICRMResponse } from '../utils/types'
import { isDemoMode } from '../utils/demo'

function maskLICRMData(data: LICRMData): LICRMData {
  return {
    ...data,
    contacts: data.contacts.map(c => ({
      ...c,
      name: c.name.split(' ').map((p, i, a) => i === a.length - 1 && a.length > 1 ? p.charAt(0) + '.' : p).join(' '),
      linkedinUrl: c.linkedinUrl ? 'https://www.linkedin.com/in/***' : '',
      notes: c.notes ? '[hidden in demo]' : '',
    })),
    dmLog: data.dmLog.map(d => ({
      ...d,
      contactName: d.contactName.split(' ').map((p, i, a) => i === a.length - 1 && a.length > 1 ? p.charAt(0) + '.' : p).join(' '),
    })),
    institutions: data.institutions.map(inst => ({
      ...inst,
      contactStrategy: inst.contactStrategy ? '[hidden in demo]' : '',
      notes: inst.notes ? '[hidden in demo]' : '',
    })),
  }
}

export default defineEventHandler(async (event): Promise<LICRMResponse> => {
  const demo = await isDemoMode(event)

  const filePath = resolve(process.cwd(), 'server/data/linkedin-crm.json')
  const raw = readFileSync(filePath, 'utf-8')
  const data: LICRMData = JSON.parse(raw)

  const finalData = demo ? maskLICRMData(data) : data

  const contacts = finalData.contacts
  const connected = contacts.filter(c => c.status === 'CONNECTED').length
  const pending = contacts.filter(c => c.status === 'PENDING').length
  const creatorMode = contacts.filter(c => c.status === 'CREATOR_MODE').length
  const dmsSent = contacts.filter(c => c.status === 'DM_SENT').length
  const dmsSkipped = contacts.filter(c => c.status === 'DM_SKIPPED').length
  const responded = contacts.filter(c => c.status === 'RESPONDED').length
  const latestSnapshot = finalData.followerSnapshots[finalData.followerSnapshots.length - 1]
  const totalSent = finalData.miningRuns.reduce((sum, r) => sum + r.sent, 0)
  const accepted = Math.round(totalSent * 0.88)

  return {
    data: finalData,
    stats: {
      totalContacts: contacts.length,
      connected,
      pending,
      creatorMode,
      dmsSent,
      dmsSkipped,
      responded,
      followers: latestSnapshot?.followers ?? 0,
      followerDelta: finalData.followerSnapshots.reduce((sum, s) => sum + (s.delta ?? 0), 0),
      acceptanceRate: `~${Math.round((accepted / totalSent) * 100)}%`,
    },
  }
})
