import api from './client'
import type { AuditLogItem, PageResponse } from './system'

export interface TagBrief {
  id: number
  name: string
  color: string | null
}

export type AssetEntityType = 'cluster' | 'device' | 'nic' | 'subnet' | 'ip' | 'service' | 'url'

export interface RelatedEntityCard {
  entity_type: AssetEntityType
  id: number
  title: string
  subtitle: string | null
  status: string | null
  group_name: string | null
  role: string | null
  extra_badge: string | null
  is_cross_group_summary: boolean
}

export interface SubnetItem {
  id: number
  network_zone_id: number
  network_zone_name: string | null
  group_id: number | null
  group_name: string | null
  name: string
  cidr: string
  gateway: string | null
  vlan_id: number | null
  env: string | null
  status: string
  description: string | null
  extra_attrs: Record<string, unknown>
  tags: TagBrief[]
  total_capacity: number
  allocated_count: number
  utilization_pct: number
  can_render_matrix: boolean
  created_at: string
  updated_at: string
}

export interface SubnetIpCell {
  ip: string
  status: string
  ip_type: string | null
  ip_id: number | null
  nic_id: number | null
  device_id: number | null
  device_hostname: string | null
  usage: string | null
  is_gateway: boolean
}

export interface SubnetMatrixResponse {
  subnet: SubnetItem
  mode: 'matrix' | 'list'
  stats: Record<string, number>
  cells: SubnetIpCell[]
  allocated_items: IpItem[]
  total_allocated: number
  page: number
  page_size: number
}

export interface ClusterItem {
  id: number
  group_id: number | null
  group_name: string | null
  network_zone_id: number | null
  network_zone_name: string | null
  name: string
  code: string | null
  cluster_type: string | null
  env: string | null
  status: string
  owner: string | null
  description: string | null
  extra_attrs: Record<string, unknown>
  tags: TagBrief[]
  device_count: number
  service_count: number
  created_at: string
  updated_at: string
}

export interface ClusterDetail extends ClusterItem {
  devices: RelatedEntityCard[]
  vips: RelatedEntityCard[]
  services: RelatedEntityCard[]
  recent_audits: AuditLogItem[]
}

export interface ClusterMemberItem {
  acl_id: number
  user_id: number
  username: string
  display_name: string
  email: string | null
  is_active: boolean
  role_id: number
  role_code: string
  role_name: string
}

export interface DeviceItem {
  id: number
  group_id: number | null
  group_name: string | null
  network_zone_id: number | null
  network_zone_name: string | null
  hostname: string
  asset_code: string | null
  serial_number: string | null
  device_type: string
  env: string | null
  status: string
  os_info: string | null
  spec_summary: string | null
  rack_location: string | null
  owner: string | null
  health_status: string
  last_checked_at: string | null
  description: string | null
  extra_attrs: Record<string, unknown>
  tags: TagBrief[]
  cluster_ids: number[]
  cluster_names: string[]
  nic_count: number
  primary_ips: string[]
  warnings: string[]
  created_at: string
  updated_at: string
}

export interface NicItem {
  id: number
  device_id: number
  device_hostname: string | null
  group_id: number | null
  parent_nic_id: number | null
  parent_nic_name: string | null
  name: string
  nic_type: string
  mac_address: string | null
  speed_mbps: number | null
  vlan_id: number | null
  bond_mode: string | null
  switch_port: string | null
  status: string
  description: string | null
  extra_attrs: Record<string, unknown>
  tags: TagBrief[]
  bound_ips: string[]
  warnings: string[]
  created_at: string
  updated_at: string
}

export interface DeviceDetail extends DeviceItem {
  clusters: RelatedEntityCard[]
  nics: NicItem[]
  ips: RelatedEntityCard[]
  services: RelatedEntityCard[]
  recent_audits: AuditLogItem[]
}

export interface NicDetail extends NicItem {
  device: RelatedEntityCard | null
  parent_nic: RelatedEntityCard | null
  child_nics: RelatedEntityCard[]
  ips: RelatedEntityCard[]
  vip_ips: RelatedEntityCard[]
  recent_audits: AuditLogItem[]
}

export interface VipBindingItem {
  id: number
  ip_id: number
  target_type: 'nic' | 'cluster' | 'service'
  target_id: number
  target_label: string
  role: string | null
  description: string | null
  created_at: string
}

export interface IpItem {
  id: number
  network_zone_id: number
  network_zone_name: string | null
  subnet_id: number | null
  subnet_cidr: string | null
  nic_id: number | null
  nic_name: string | null
  device_id: number | null
  device_hostname: string | null
  group_id: number | null
  group_name: string | null
  ip_address: string
  ip_type: 'host' | 'vip' | 'gateway' | 'floating' | 'reserved'
  is_primary: boolean
  status: string
  usage: string | null
  health_status: string
  last_checked_at: string | null
  description: string | null
  extra_attrs: Record<string, unknown>
  tags: TagBrief[]
  vip_binding_count: number
  created_at: string
  updated_at: string
}

export interface IpDetail extends IpItem {
  subnet: RelatedEntityCard | null
  nic: RelatedEntityCard | null
  device: RelatedEntityCard | null
  vip_bindings: VipBindingItem[]
  services: RelatedEntityCard[]
  urls: RelatedEntityCard[]
  recent_audits: AuditLogItem[]
}

export interface ServiceEndpointItem {
  id: number
  service_id: number
  cluster_id: number | null
  cluster_name: string | null
  device_id: number | null
  device_hostname: string | null
  ip_id: number | null
  ip_address: string | null
  listen_ip_text: string | null
  port: number | null
  protocol: string
  endpoint_role: string | null
  description: string | null
  is_cross_group_summary: boolean
}

export interface ServiceItem {
  id: number
  group_id: number | null
  group_name: string | null
  name: string
  code: string | null
  service_type: string
  env: string | null
  status: string
  version: string | null
  owner: string | null
  repo_or_doc_url: string | null
  description: string | null
  extra_attrs: Record<string, unknown>
  tags: TagBrief[]
  endpoint_count: number
  url_count: number
  created_at: string
  updated_at: string
}

export interface ServiceDetail extends ServiceItem {
  endpoints: ServiceEndpointItem[]
  urls: RelatedEntityCard[]
  clusters: RelatedEntityCard[]
  devices: RelatedEntityCard[]
  ips: RelatedEntityCard[]
  recent_audits: AuditLogItem[]
}

export interface UrlItem {
  id: number
  group_id: number | null
  group_name: string | null
  service_id: number | null
  service_name: string | null
  entry_ip_id: number | null
  entry_ip_address: string | null
  name: string
  full_url: string
  protocol: string
  domain: string
  port: number | null
  path: string | null
  access_type: 'internal' | 'external' | 'vpn'
  env: string | null
  status: string
  is_https: boolean
  ssl_expires_at: string | null
  health_status: string
  last_checked_at: string | null
  description: string | null
  extra_attrs: Record<string, unknown>
  tags: TagBrief[]
  created_at: string
  updated_at: string
}

export interface UrlDetail extends UrlItem {
  service: RelatedEntityCard | null
  entry_ip: RelatedEntityCard | null
  clusters: RelatedEntityCard[]
  devices: RelatedEntityCard[]
  nics: RelatedEntityCard[]
  ips: RelatedEntityCard[]
  recent_audits: AuditLogItem[]
}

export const subnetApi = {
  list: (params?: Record<string, unknown>) => api.get<PageResponse<SubnetItem>>('/subnets', { params }),
  get: (id: number) => api.get<SubnetItem>(`/subnets/${id}`),
  create: (payload: Record<string, unknown>) => api.post<SubnetItem>('/subnets', payload),
  update: (id: number, payload: Record<string, unknown>) => api.put<SubnetItem>(`/subnets/${id}`, payload),
  remove: (id: number) => api.delete(`/subnets/${id}`),
  matrix: (id: number, params?: { page?: number; page_size?: number }) =>
    api.get<SubnetMatrixResponse>(`/subnets/${id}/matrix`, { params }),
  nextFreeIp: (id: number) => api.get<{ subnet_id: number; cidr: string; next_free_ip: string | null }>(`/subnets/${id}/next-free-ip`),
}

export const clusterApi = {
  list: (params?: Record<string, unknown>) => api.get<PageResponse<ClusterItem>>('/clusters', { params }),
  detail: (id: number) => api.get<ClusterDetail>(`/clusters/${id}`),
  create: (payload: Record<string, unknown>) => api.post<ClusterItem>('/clusters', payload),
  update: (id: number, payload: Record<string, unknown>) => api.put<ClusterItem>(`/clusters/${id}`, payload),
  remove: (id: number) => api.delete(`/clusters/${id}`),
  attachDevice: (clusterId: number, payload: { device_id: number; node_role?: string | null }) =>
    api.post(`/clusters/${clusterId}/devices`, payload),
  detachDevice: (clusterId: number, deviceId: number) =>
    api.delete(`/clusters/${clusterId}/devices/${deviceId}`),
  listMembers: (clusterId: number) => api.get<ClusterMemberItem[]>(`/clusters/${clusterId}/members`),
  addMember: (clusterId: number, payload: { user_id: number; role_id: number }) =>
    api.post<ClusterMemberItem>(`/clusters/${clusterId}/members`, payload),
  removeMember: (clusterId: number, aclId: number) =>
    api.delete(`/clusters/${clusterId}/members/${aclId}`),
}

export const deviceApi = {
  list: (params?: Record<string, unknown>) => api.get<PageResponse<DeviceItem>>('/devices', { params }),
  detail: (id: number) => api.get<DeviceDetail>(`/devices/${id}`),
  create: (payload: Record<string, unknown>) => api.post<DeviceItem>('/devices', payload),
  update: (id: number, payload: Record<string, unknown>) => api.put<DeviceItem>(`/devices/${id}`, payload),
  remove: (id: number, forceUnbind = false) =>
    api.delete(`/devices/${id}`, { params: { force_unbind: forceUnbind } }),
}

export const nicApi = {
  list: (params?: Record<string, unknown>) => api.get<PageResponse<NicItem>>('/nics', { params }),
  detail: (id: number) => api.get<NicDetail>(`/nics/${id}`),
  create: (payload: Record<string, unknown>) => api.post<NicItem>('/nics', payload),
  update: (id: number, payload: Record<string, unknown>) => api.put<NicItem>(`/nics/${id}`, payload),
  remove: (id: number) => api.delete(`/nics/${id}`),
}

export const ipApi = {
  list: (params?: Record<string, unknown>) => api.get<PageResponse<IpItem>>('/ips', { params }),
  detail: (id: number) => api.get<IpDetail>(`/ips/${id}`),
  create: (payload: Record<string, unknown>) => api.post<IpItem>('/ips', payload),
  update: (id: number, payload: Record<string, unknown>) => api.put<IpItem>(`/ips/${id}`, payload),
  remove: (id: number, forceUnbind = false) =>
    api.delete(`/ips/${id}`, { params: { force_unbind: forceUnbind } }),
  addVipBinding: (ipId: number, payload: { target_type: string; target_id: number; role?: string | null; description?: string | null }) =>
    api.post<VipBindingItem>(`/ips/${ipId}/vip-bindings`, payload),
  removeVipBinding: (ipId: number, bindingId: number) =>
    api.delete(`/ips/${ipId}/vip-bindings/${bindingId}`),
}

export const serviceApi = {
  list: (params?: Record<string, unknown>) => api.get<PageResponse<ServiceItem>>('/services', { params }),
  detail: (id: number) => api.get<ServiceDetail>(`/services/${id}`),
  create: (payload: Record<string, unknown>) => api.post<ServiceItem>('/services', payload),
  update: (id: number, payload: Record<string, unknown>) => api.put<ServiceItem>(`/services/${id}`, payload),
  remove: (id: number) => api.delete(`/services/${id}`),
  addEndpoint: (serviceId: number, payload: Record<string, unknown>) =>
    api.post<ServiceEndpointItem>(`/services/${serviceId}/endpoints`, payload),
  deleteEndpoint: (serviceId: number, endpointId: number) =>
    api.delete(`/services/${serviceId}/endpoints/${endpointId}`),
}

export const urlApi = {
  list: (params?: Record<string, unknown>) => api.get<PageResponse<UrlItem>>('/urls', { params }),
  detail: (id: number) => api.get<UrlDetail>(`/urls/${id}`),
  create: (payload: Record<string, unknown>) => api.post<UrlItem>('/urls', payload),
  update: (id: number, payload: Record<string, unknown>) => api.put<UrlItem>(`/urls/${id}`, payload),
  remove: (id: number) => api.delete(`/urls/${id}`),
}
