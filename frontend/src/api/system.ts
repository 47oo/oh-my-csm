import api from './client'

export interface UserAssignment {
  id: number
  group_id: number | null
  group_name: string | null
  role_id: number
  role_code: string
  role_name: string
}

export interface ClusterAssignment {
  id: number
  cluster_id: number
  cluster_name: string | null
  role_id: number
  role_code: string
  role_name: string
}

export interface CurrentUser {
  id: number
  username: string
  display_name: string
  email: string | null
  auth_source: string
  is_active: boolean
  allow_public_assets: boolean
  must_change_password: boolean
  last_login_at: string | null
  is_superadmin: boolean
  global_permissions: string[]
  group_permissions: Record<number, string[]>
  cluster_permissions: Record<number, string[]>
  assignments: UserAssignment[]
  cluster_assignments: ClusterAssignment[]
}

export interface PageResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface PermissionItem {
  code: string
  label: string
  category: string
}

export interface RoleItem {
  id: number
  code: string
  name: string
  description: string | null
  is_builtin: boolean
  permissions: string[]
  created_at: string
  updated_at: string
}

export interface UserItem {
  id: number
  username: string
  display_name: string
  email: string | null
  auth_source: string
  is_active: boolean
  allow_public_assets: boolean
  must_change_password: boolean
  failed_login_attempts: number
  locked_until: string | null
  last_login_at: string | null
  created_at: string
  assignments: UserAssignment[]
  cluster_assignments: ClusterAssignment[]
}

export interface OpsGroupItem {
  id: number
  name: string
  code: string
  parent_id: number | null
  description: string | null
  extra_attrs: Record<string, unknown>
  member_count: number
  created_at: string
  updated_at: string
}

export interface GroupMemberItem {
  assignment_id: number
  user_id: number
  username: string
  display_name: string
  email: string | null
  is_active: boolean
  role_id: number
  role_code: string
  role_name: string
}

export interface NetworkZoneItem {
  id: number
  name: string
  code: string
  location: string | null
  is_default: boolean
  description: string | null
  extra_attrs: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface DictItem {
  id: number
  category: string
  code: string
  label: string
  color: string | null
  sort_order: number
  is_builtin: boolean
  is_enabled: boolean
  description: string | null
  created_at: string
  updated_at: string
}

export interface TagItem {
  id: number
  name: string
  color: string | null
  description: string | null
  created_at: string
  updated_at: string
}

export interface AuditLogItem {
  id: number
  actor_id: number | null
  actor_username: string
  auth_method: string
  client_ip: string
  action: string
  target_type: string
  target_id: number | null
  target_label: string | null
  group_id: number | null
  import_batch_id: number | null
  diff_before: Record<string, unknown> | null
  diff_after: Record<string, unknown> | null
  created_at: string
}

export interface ApiTokenItem {
  id: number
  name: string
  token_prefix: string
  scope: 'read_only' | 'inherit'
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface ApiTokenCreated extends ApiTokenItem {
  token: string
}

export interface DashboardSummary {
  counts: Record<string, number>
  recent_audits: AuditLogItem[]
}

export const authApi = {
  login: (username: string, password: string) =>
    api.post<{
      access_token: string
      refresh_token: string
      expires_in: number
      must_change_password: boolean
    }>('/auth/login', { username, password }),
  logout: (refresh_token?: string | null) => api.post('/auth/logout', { refresh_token }),
  me: () => api.get<CurrentUser>('/auth/me'),
  changePassword: (old_password: string, new_password: string) =>
    api.post('/auth/change-password', { old_password, new_password }),
  listTokens: () => api.get<ApiTokenItem[]>('/auth/tokens'),
  createToken: (payload: { name: string; scope: 'read_only' | 'inherit'; expires_in_days: number | null }) =>
    api.post<ApiTokenCreated>('/auth/tokens', payload),
  revokeToken: (id: number) => api.delete(`/auth/tokens/${id}`),
}

export const dashboardApi = {
  summary: () => api.get<DashboardSummary>('/dashboard/summary'),
}

export const groupApi = {
  list: (q?: string) => api.get<OpsGroupItem[]>('/groups', { params: { q } }),
  create: (payload: { name: string; code: string; description?: string | null }) =>
    api.post<OpsGroupItem>('/groups', payload),
  update: (id: number, payload: { name?: string; description?: string | null }) =>
    api.put<OpsGroupItem>(`/groups/${id}`, payload),
  remove: (id: number) => api.delete(`/groups/${id}`),
  listMembers: (groupId: number) => api.get<GroupMemberItem[]>(`/groups/${groupId}/members`),
  addMember: (groupId: number, payload: { user_id: number; role_id: number }) =>
    api.post<GroupMemberItem>(`/groups/${groupId}/members`, payload),
  removeMember: (groupId: number, assignmentId: number) =>
    api.delete(`/groups/${groupId}/members/${assignmentId}`),
}

export const userApi = {
  list: (params?: { q?: string; page?: number; page_size?: number }) =>
    api.get<PageResponse<UserItem>>('/users', { params }),
  create: (payload: {
    username: string
    display_name: string
    email?: string | null
    password: string
    is_active: boolean
    allow_public_assets?: boolean
    must_change_password: boolean
    assignments: Array<{ group_id: number | null; role_id: number }>
    cluster_assignments?: Array<{ cluster_id: number; role_id: number }>
  }) => api.post<UserItem>('/users', payload),
  update: (
    id: number,
    payload: {
      display_name?: string
      email?: string | null
      is_active?: boolean
      allow_public_assets?: boolean
      must_change_password?: boolean
      assignments?: Array<{ group_id: number | null; role_id: number }>
      cluster_assignments?: Array<{ cluster_id: number; role_id: number }>
    },
  ) => api.put<UserItem>(`/users/${id}`, payload),
  unlock: (id: number) => api.post(`/users/${id}/unlock`),
  resetPassword: (id: number, new_password: string, must_change_password = true) =>
    api.post(`/users/${id}/reset-password`, { new_password, must_change_password }),
  remove: (id: number) => api.delete(`/users/${id}`),
}

export const roleApi = {
  list: () => api.get<RoleItem[]>('/roles'),
  permissions: () => api.get<PermissionItem[]>('/roles/permissions'),
  create: (payload: { code: string; name: string; description?: string | null; permissions: string[] }) =>
    api.post<RoleItem>('/roles', payload),
  update: (id: number, payload: { name?: string; description?: string | null; permissions?: string[] }) =>
    api.put<RoleItem>(`/roles/${id}`, payload),
  remove: (id: number) => api.delete(`/roles/${id}`),
}

export const dictApi = {
  list: (params?: { category?: string; only_enabled?: boolean }) =>
    api.get<DictItem[]>('/dicts', { params }),
  create: (payload: {
    category: string
    code: string
    label: string
    color?: string | null
    sort_order: number
    is_enabled: boolean
    description?: string | null
  }) => api.post<DictItem>('/dicts', payload),
  update: (
    id: number,
    payload: {
      label?: string
      color?: string | null
      sort_order?: number
      is_enabled?: boolean
      description?: string | null
    },
  ) => api.put<DictItem>(`/dicts/${id}`, payload),
  remove: (id: number) => api.delete(`/dicts/${id}`),
}

export const tagApi = {
  list: (q?: string) => api.get<TagItem[]>('/tags', { params: { q } }),
  create: (payload: { name: string; color?: string | null; description?: string | null }) =>
    api.post<TagItem>('/tags', payload),
  update: (id: number, payload: { name?: string; color?: string | null; description?: string | null }) =>
    api.put<TagItem>(`/tags/${id}`, payload),
  remove: (id: number) => api.delete(`/tags/${id}`),
}

export const zoneApi = {
  list: (q?: string) => api.get<NetworkZoneItem[]>('/network-zones', { params: { q } }),
  create: (payload: {
    name: string
    code: string
    location?: string | null
    is_default: boolean
    description?: string | null
  }) => api.post<NetworkZoneItem>('/network-zones', payload),
  update: (
    id: number,
    payload: {
      name?: string
      location?: string | null
      is_default?: boolean
      description?: string | null
    },
  ) => api.put<NetworkZoneItem>(`/network-zones/${id}`, payload),
  remove: (id: number) => api.delete(`/network-zones/${id}`),
}

export const auditApi = {
  list: (params?: {
    action?: string
    target_type?: string
    actor_username?: string
    page?: number
    page_size?: number
  }) => api.get<PageResponse<AuditLogItem>>('/audit-logs', { params }),
}
