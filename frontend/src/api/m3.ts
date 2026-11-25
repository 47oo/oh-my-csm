import api from './client'
import type { AssetEntityType } from './assets'
import type { AuditLogItem } from './system'

export interface CredentialItem {
  id: number
  target_type: 'cluster' | 'device' | 'nic' | 'service' | 'url'
  target_id: number
  group_id: number | null
  purpose: string
  username: string | null
  key_version: string
  notes: string | null
  has_secret: boolean
  created_at: string
  updated_at: string
}

export interface SearchHit {
  entity_type: AssetEntityType
  id: number
  title: string
  subtitle: string | null
  badge: string | null
  status: string | null
  group_name: string | null
}

export interface SearchGroup {
  entity_type: AssetEntityType
  label: string
  items: SearchHit[]
}

export interface GlobalSearchResponse {
  query: string
  total: number
  groups: SearchGroup[]
}

export type BatchEntity = 'clusters' | 'devices' | 'nics' | 'subnets' | 'ips' | 'services' | 'urls'
export type BatchAction = 'tag_add' | 'tag_remove' | 'move_group' | 'update_status' | 'delete'

export interface BatchOperationResponse {
  succeeded_ids: number[]
  failed: Array<{ id: number; label: string; reason: string }>
}

export interface RecycleBinItem {
  entity_type: string
  id: number
  label: string
  subtitle: string | null
  group_id: number | null
  group_name: string | null
  deleted_at: string
}

export interface DashboardEnhanced {
  counts: Record<string, number>
  top_subnets: Array<{
    id: number
    name: string
    cidr: string
    allocated_count: number
    total_capacity: number
    utilization_pct: number
  }>
  expiring_urls: Array<{
    id: number
    name: string
    domain: string
    full_url: string
    ssl_expires_at: string
    days_remaining: number
  }>
  incomplete_counts: {
    urls_without_service: number
    host_ips_without_nic: number
    devices_without_group: number
  }
  duplicate_hostnames: Array<{ hostname: string; count: number }>
  recent_audits: AuditLogItem[]
}

export const credentialApi = {
  list: (target_type: string, target_id: number) =>
    api.get<CredentialItem[]>('/credentials', { params: { target_type, target_id } }),
  create: (payload: {
    target_type: string
    target_id: number
    purpose: string
    username?: string | null
    secret: string
    notes?: string | null
  }) => api.post<CredentialItem>('/credentials', payload),
  update: (
    id: number,
    payload: { purpose?: string; username?: string | null; secret?: string | null; notes?: string | null },
  ) => api.put<CredentialItem>(`/credentials/${id}`, payload),
  remove: (id: number) => api.delete(`/credentials/${id}`),
  reveal: (id: number) =>
    api.post<{ id: number; purpose: string; username: string | null; secret: string }>(
      `/credentials/${id}/reveal`,
    ),
}

export const searchApi = {
  query: (q: string) => api.get<GlobalSearchResponse>('/search', { params: { q } }),
}

export const batchApi = {
  execute: (
    entity: BatchEntity,
    payload: {
      ids: number[]
      action: BatchAction
      tag_ids?: number[]
      group_id?: number | null
      status?: string | null
      env?: string | null
    },
  ) => api.post<BatchOperationResponse>(`/batch/${entity}`, payload),
}

export const recycleBinApi = {
  list: (entity_type?: string) =>
    api.get<RecycleBinItem[]>('/recycle-bin', { params: { entity_type } }),
  restore: (entity_type: string, id: number) =>
    api.post(`/recycle-bin/${entity_type}/${id}/restore`),
  purge: (entity_type: string, id: number) =>
    api.delete(`/recycle-bin/${entity_type}/${id}`),
}

export const enhancedDashboardApi = {
  summary: () => api.get<DashboardEnhanced>('/dashboard/summary'),
}
