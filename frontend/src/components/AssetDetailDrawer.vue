<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { useMessage } from 'naive-ui'
import {
  clusterApi,
  deviceApi,
  ipApi,
  nicApi,
  serviceApi,
  urlApi,
  type AssetEntityType,
  type RelatedEntityCard,
} from '@/api/assets'
import { credentialApi, type CredentialItem } from '@/api/m3'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import type { AuditLogItem } from '@/api/system'

const props = defineProps<{
  show: boolean
  entityType: AssetEntityType | null
  entityId: number | null
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
}>()

const auth = useAuthStore()
const message = useMessage()
const loading = ref(false)
const detail = ref<Record<string, any> | null>(null)
const historyStack = ref<Array<{ type: AssetEntityType; id: number }>>([])
const currentTarget = ref<{ type: AssetEntityType; id: number } | null>(null)

// Credentials state
const credentials = ref<CredentialItem[]>([])
const credModalOpen = ref(false)
const credForm = reactive({ purpose: '', username: '', secret: '', notes: '' })
const revealedMap = ref<Record<number, { secret: string; secondsLeft: number }>>({})
let maskTimer: ReturnType<typeof setInterval> | null = null

const CREDENTIAL_SUPPORTED: Set<AssetEntityType> = new Set(['cluster', 'device', 'nic', 'service', 'url'])

const typeLabels: Record<AssetEntityType, string> = {
  cluster: '集群',
  device: '设备',
  nic: '网卡',
  subnet: '网段',
  ip: 'IP 地址',
  service: '服务',
  url: 'URL 入口',
}

function startMaskTick() {
  if (maskTimer) return
  maskTimer = setInterval(() => {
    const next: Record<number, { secret: string; secondsLeft: number }> = {}
    let anyActive = false
    for (const [k, v] of Object.entries(revealedMap.value)) {
      if (v.secondsLeft > 1) {
        next[Number(k)] = { secret: v.secret, secondsLeft: v.secondsLeft - 1 }
        anyActive = true
      }
    }
    revealedMap.value = next
    if (!anyActive && maskTimer) {
      clearInterval(maskTimer)
      maskTimer = null
    }
  }, 1000)
}

onUnmounted(() => {
  if (maskTimer) clearInterval(maskTimer)
})

const supportsCredentials = computed(() =>
  currentTarget.value ? CREDENTIAL_SUPPORTED.has(currentTarget.value.type) : false,
)

const canManageCred = computed(() => {
  if (auth.isSuperAdmin) return true
  const gid = detail.value?.group_id ?? null
  if (auth.hasGlobalPerm('credential:manage')) return true
  if (gid !== null && (auth.user?.group_permissions?.[gid] || []).includes('credential:manage')) {
    return true
  }
  return Object.values(auth.user?.cluster_permissions || {}).some((p) => p.includes('credential:manage'))
})

const canRevealCred = computed(() => {
  if (auth.isSuperAdmin) return true
  const gid = detail.value?.group_id ?? null
  if (auth.hasGlobalPerm('credential:reveal')) return true
  if (gid !== null && (auth.user?.group_permissions?.[gid] || []).includes('credential:reveal')) {
    return true
  }
  return Object.values(auth.user?.cluster_permissions || {}).some((p) => p.includes('credential:reveal'))
})

async function fetchCredentials(type: AssetEntityType, id: number) {
  if (!CREDENTIAL_SUPPORTED.has(type)) {
    credentials.value = []
    return
  }
  try {
    credentials.value = (await credentialApi.list(type, id)).data
  } catch {
    credentials.value = []
  }
}

async function fetchDetail(type: AssetEntityType, id: number) {
  loading.value = true
  detail.value = null
  revealedMap.value = {}
  try {
    if (type === 'cluster') detail.value = (await clusterApi.detail(id)).data
    else if (type === 'device') detail.value = (await deviceApi.detail(id)).data
    else if (type === 'nic') detail.value = (await nicApi.detail(id)).data
    else if (type === 'ip') detail.value = (await ipApi.detail(id)).data
    else if (type === 'service') detail.value = (await serviceApi.detail(id)).data
    else if (type === 'url') detail.value = (await urlApi.detail(id)).data
    await fetchCredentials(type, id)
  } catch (err) {
    message.error(extractErrorMessage(err, '加载资产详情失败'))
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.show, props.entityType, props.entityId] as const,
  ([show, type, id]) => {
    if (show && type && id) {
      historyStack.value = []
      currentTarget.value = { type, id }
      fetchDetail(type, id)
    } else if (!show) {
      revealedMap.value = {}
    }
  },
  { immediate: true },
)

async function handleCreateCred() {
  if (!currentTarget.value) return
  if (!credForm.purpose.trim() || !credForm.secret) {
    message.warning('请填写用途和密文内容')
    return
  }
  try {
    await credentialApi.create({
      target_type: currentTarget.value.type,
      target_id: currentTarget.value.id,
      purpose: credForm.purpose.trim(),
      username: credForm.username.trim() || null,
      secret: credForm.secret,
      notes: credForm.notes || null,
    })
    message.success('凭据已加密保存')
    credModalOpen.value = false
    Object.assign(credForm, { purpose: '', username: '', secret: '', notes: '' })
    await fetchCredentials(currentTarget.value.type, currentTarget.value.id)
  } catch (err) {
    message.error(extractErrorMessage(err, '保存凭据失败'))
  }
}

async function handleRevealCred(id: number) {
  try {
    const res = await credentialApi.reveal(id)
    revealedMap.value = {
      ...revealedMap.value,
      [id]: { secret: res.data.secret, secondsLeft: 15 },
    }
    startMaskTick()
    message.info('已记录明文查看审计日志，15 秒后自动隐藏')
  } catch (err) {
    message.error(extractErrorMessage(err, '查看明文失败'))
  }
}

async function handleDeleteCred(id: number) {
  if (!currentTarget.value) return
  try {
    await credentialApi.remove(id)
    message.success('凭据已移入回收站')
    await fetchCredentials(currentTarget.value.type, currentTarget.value.id)
  } catch (err) {
    message.error(extractErrorMessage(err, '删除凭据失败'))
  }
}

function navigateToCard(card: RelatedEntityCard) {
  if (card.is_cross_group_summary) {
    message.info('该节点属于其他运维分组，当前仅展示只读摘要')
    return
  }
  if (card.entity_type === 'subnet') {
    message.info(`所属网段：${card.title}（请前往「网段与 IPAM」查看格子矩阵）`)
    return
  }
  if (currentTarget.value) {
    historyStack.value.push({ ...currentTarget.value })
  }
  currentTarget.value = { type: card.entity_type, id: card.id }
  fetchDetail(card.entity_type, card.id)
}

function goBack() {
  const prev = historyStack.value.pop()
  if (prev) {
    currentTarget.value = prev
    fetchDetail(prev.type, prev.id)
  }
}

async function copyText(text: string | null | undefined) {
  if (!text) return
  await navigator.clipboard.writeText(text)
  message.success('已复制到剪贴板')
}

const drawerTitle = computed(() => {
  if (!currentTarget.value) return '资产全景详情'
  const label = typeLabels[currentTarget.value.type] || '资产'
  const name =
    detail.value?.name ||
    detail.value?.hostname ||
    detail.value?.ip_address ||
    `#${currentTarget.value.id}`
  return `${label} · ${name}`
})

const relationSections = computed<Array<{ title: string; cards: RelatedEntityCard[] }>>(() => {
  const d = detail.value
  const t = currentTarget.value?.type
  if (!d || !t) return []
  const sections: Array<{ title: string; cards: RelatedEntityCard[] }> = []

  if (t === 'url') {
    if (d.entry_ip) sections.push({ title: '入口 IP', cards: [d.entry_ip] })
    if (d.service) sections.push({ title: '关联服务', cards: [d.service] })
    if (d.clusters?.length) sections.push({ title: '承载集群', cards: d.clusters })
    if (d.devices?.length) sections.push({ title: '承载设备', cards: d.devices })
    if (d.nics?.length) sections.push({ title: '承载网卡', cards: d.nics })
    if (d.ips?.length) sections.push({ title: '关联 IP', cards: d.ips })
  } else if (t === 'service') {
    if (d.urls?.length) sections.push({ title: '暴露 URL 入口', cards: d.urls })
    if (d.clusters?.length) sections.push({ title: '部署集群', cards: d.clusters })
    if (d.devices?.length) sections.push({ title: '部署设备', cards: d.devices })
    if (d.ips?.length) sections.push({ title: '监听 IP', cards: d.ips })
  } else if (t === 'ip') {
    if (d.subnet) sections.push({ title: '所属网段', cards: [d.subnet] })
    if (d.device) sections.push({ title: '绑定设备', cards: [d.device] })
    if (d.nic) sections.push({ title: '绑定网卡', cards: [d.nic] })
    if (d.services?.length) sections.push({ title: '运行服务与端口', cards: d.services })
    if (d.urls?.length) sections.push({ title: '引用此 IP 的 URL', cards: d.urls })
  } else if (t === 'nic') {
    if (d.device) sections.push({ title: '所属设备', cards: [d.device] })
    if (d.parent_nic) sections.push({ title: '上级 Bond 聚合口', cards: [d.parent_nic] })
    if (d.child_nics?.length) sections.push({ title: '下属物理成员口', cards: d.child_nics })
    if (d.ips?.length) sections.push({ title: '绑定 IP 地址', cards: d.ips })
    if (d.vip_ips?.length) sections.push({ title: '承载 VIP', cards: d.vip_ips })
  } else if (t === 'device') {
    if (d.clusters?.length) sections.push({ title: '所属集群', cards: d.clusters })
    if (d.ips?.length) sections.push({ title: '已绑定 IP', cards: d.ips })
    if (d.services?.length) sections.push({ title: '运行服务', cards: d.services })
  } else if (t === 'cluster') {
    if (d.devices?.length) sections.push({ title: '成员设备', cards: d.devices })
    if (d.vips?.length) sections.push({ title: '集群 VIP', cards: d.vips })
    if (d.services?.length) sections.push({ title: '承载服务', cards: d.services })
  }
  return sections
})

const audits = computed<AuditLogItem[]>(() => detail.value?.recent_audits || [])
</script>

<template>
  <n-drawer :show="show" :width="660" @update:show="(v: boolean) => emit('update:show', v)">
    <n-drawer-content>
      <template #header>
        <div class="flex items-center gap-2">
          <n-button v-if="historyStack.length" size="tiny" quaternary @click="goBack">← 返回上一节点</n-button>
          <span class="font-semibold">{{ drawerTitle }}</span>
        </div>
      </template>

      <n-spin :show="loading">
        <n-tabs v-if="detail" type="line">
          <n-tab-pane name="overview" tab="全景关联与属性">
            <div class="mb-4 p-3 rounded bg-slate-50 border border-slate-200 space-y-2 text-sm">
              <div class="flex flex-wrap items-center gap-2">
                <n-tag size="small" type="info">{{ detail.group_name || '公共/未归属' }}</n-tag>
                <n-tag v-if="detail.env" size="small" type="warning">{{ detail.env }}</n-tag>
                <n-tag v-if="detail.status" size="small" type="success">{{ detail.status }}</n-tag>
                <n-tag v-for="t in detail.tags || []" :key="t.id" size="small">{{ t.name }}</n-tag>
              </div>
              <div v-if="detail.full_url" class="flex items-center gap-2">
                <span class="text-gray-500">URL:</span>
                <code class="text-blue-600 break-all">{{ detail.full_url }}</code>
                <n-button size="tiny" quaternary @click="copyText(detail.full_url)">复制</n-button>
              </div>
              <div v-if="detail.ip_address" class="flex items-center gap-2">
                <span class="text-gray-500">IP:</span>
                <code class="font-semibold">{{ detail.ip_address }}</code>
                <n-button size="tiny" quaternary @click="copyText(detail.ip_address)">复制</n-button>
              </div>
              <div v-if="detail.mac_address" class="flex items-center gap-2">
                <span class="text-gray-500">MAC:</span>
                <code>{{ detail.mac_address }}</code>
                <n-button size="tiny" quaternary @click="copyText(detail.mac_address)">复制</n-button>
              </div>
              <div v-if="detail.description" class="text-gray-600 text-xs">{{ detail.description }}</div>
            </div>

            <div v-if="currentTarget?.type === 'device' && detail.nics?.length" class="mb-4">
              <div class="text-xs font-semibold text-gray-500 mb-2">网卡与绑定 IP（含 Bond 层级）</div>
              <div class="space-y-2">
                <div
                  v-for="n in detail.nics"
                  :key="n.id"
                  class="p-2.5 rounded border border-gray-200 bg-white flex items-center justify-between cursor-pointer hover:border-blue-400"
                  @click="navigateToCard({ entity_type: 'nic', id: n.id, title: n.name, subtitle: null, status: n.status, group_name: null, role: null, extra_badge: null, is_cross_group_summary: false })"
                >
                  <div>
                    <span class="font-medium">{{ n.name }}</span>
                    <n-tag v-if="n.parent_nic_name" size="tiny" type="warning" class="ml-2">成员 → {{ n.parent_nic_name }}</n-tag>
                    <span v-if="n.mac_address" class="text-xs text-gray-500 ml-2">{{ n.mac_address }}</span>
                  </div>
                  <div class="flex gap-1 flex-wrap">
                    <n-tag v-for="ip in n.bound_ips" :key="ip" size="tiny" type="info">{{ ip }}</n-tag>
                    <span v-if="!n.bound_ips?.length" class="text-xs text-gray-400">无绑定 IP</span>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="currentTarget?.type === 'service' && detail.endpoints?.length" class="mb-4">
              <div class="text-xs font-semibold text-gray-500 mb-2">运行端点 (Endpoints)</div>
              <n-data-table
                size="small"
                :data="detail.endpoints"
                :columns="[
                  { title: '协议/端口', key: 'port', width: 110, render: (r: any) => `${String(r.protocol || '').toUpperCase()}:${r.port || '-'}` },
                  { title: 'IP', key: 'ip', render: (r: any) => r.ip_address || r.listen_ip_text || '—' },
                  { title: '设备/集群', key: 'host', render: (r: any) => r.device_hostname || r.cluster_name || '—' },
                  { title: '角色', key: 'endpoint_role', width: 90 },
                ]"
              />
            </div>

            <div v-if="currentTarget?.type === 'ip' && detail.vip_bindings?.length" class="mb-4">
              <div class="text-xs font-semibold text-gray-500 mb-2">VIP 承载目标绑定</div>
              <div class="space-y-1.5">
                <div
                  v-for="b in detail.vip_bindings"
                  :key="b.id"
                  class="p-2 rounded border border-purple-200 bg-purple-50/40 flex items-center justify-between text-xs"
                >
                  <span><n-tag size="tiny" type="warning" class="mr-2">{{ b.target_type }}</n-tag>{{ b.target_label }}</span>
                  <n-tag v-if="b.role" size="tiny">{{ b.role }}</n-tag>
                </div>
              </div>
            </div>

            <div v-for="sec in relationSections" :key="sec.title" class="mb-4">
              <div class="text-xs font-semibold text-gray-500 mb-2">{{ sec.title }} ({{ sec.cards.length }})</div>
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div
                  v-for="card in sec.cards"
                  :key="`${card.entity_type}-${card.id}-${card.subtitle}`"
                  class="p-2.5 rounded border border-gray-200 bg-white hover:border-blue-400 cursor-pointer transition"
                  @click="navigateToCard(card)"
                >
                  <div class="flex items-center justify-between gap-1">
                    <span class="font-medium text-sm truncate">{{ card.title }}</span>
                    <div class="flex gap-1 shrink-0">
                      <n-tag v-if="card.is_cross_group_summary" size="tiny" type="warning">跨组摘要</n-tag>
                      <n-tag v-if="card.role" size="tiny" type="info">{{ card.role }}</n-tag>
                      <n-tag v-if="card.extra_badge" size="tiny">{{ card.extra_badge }}</n-tag>
                    </div>
                  </div>
                  <div class="text-xs text-gray-500 mt-1 flex items-center justify-between">
                    <span class="truncate">{{ card.subtitle || (card.is_cross_group_summary ? '受权限隔离隐藏详情' : '—') }}</span>
                    <n-button size="tiny" quaternary @click.stop="copyText(card.title)">复制</n-button>
                  </div>
                </div>
              </div>
            </div>

            <div v-if="detail.extra_attrs && Object.keys(detail.extra_attrs).length" class="mt-4">
              <div class="text-xs font-semibold text-gray-500 mb-2">扩展属性 (KV)</div>
              <n-descriptions bordered :column="2" size="small">
                <n-descriptions-item v-for="(val, k) in detail.extra_attrs" :key="String(k)" :label="String(k)">
                  {{ val }}
                </n-descriptions-item>
              </n-descriptions>
            </div>
          </n-tab-pane>

          <n-tab-pane v-if="supportsCredentials" name="credentials" :tab="`关联凭据 (${credentials.length})`">
            <div class="flex items-center justify-between mb-3">
              <span class="text-xs text-gray-500">AES-256-GCM 密文存储 · 每次查看明文均记入审计日志</span>
              <n-button v-if="canManageCred" size="tiny" type="primary" @click="credModalOpen = true">
                新增凭据
              </n-button>
            </div>

            <div v-if="credentials.length" class="space-y-2.5">
              <div v-for="c in credentials" :key="c.id" class="p-3 rounded border border-gray-200 bg-white space-y-2">
                <div class="flex items-center justify-between">
                  <div>
                    <span class="font-medium text-sm">{{ c.purpose }}</span>
                    <n-tag size="tiny" class="ml-2">v{{ c.key_version }}</n-tag>
                  </div>
                  <n-space size="small">
                    <n-button
                      v-if="canRevealCred && !revealedMap[c.id]"
                      size="tiny"
                      type="warning"
                      secondary
                      @click="handleRevealCred(c.id)"
                    >
                      查看明文
                    </n-button>
                    <n-popconfirm v-if="canManageCred" @positive-click="handleDeleteCred(c.id)">
                      <template #trigger>
                        <n-button size="tiny" type="error" quaternary>删除</n-button>
                      </template>
                      确认将凭据「{{ c.purpose }}」移入回收站？
                    </n-popconfirm>
                  </n-space>
                </div>

                <div class="text-xs text-gray-600 flex items-center gap-2">
                  <span>账号: <code>{{ c.username || '—' }}</code></span>
                  <n-button v-if="c.username" size="tiny" quaternary @click="copyText(c.username)">复制</n-button>
                </div>

                <div class="text-xs flex items-center justify-between bg-slate-50 px-2.5 py-1.5 rounded">
                  <template v-if="revealedMap[c.id]">
                    <code class="text-rose-600 break-all">{{ revealedMap[c.id].secret }}</code>
                    <div class="flex items-center gap-2 shrink-0 ml-2">
                      <span class="text-amber-600">{{ revealedMap[c.id].secondsLeft }}s 后隐藏</span>
                      <n-button size="tiny" type="primary" quaternary @click="copyText(revealedMap[c.id].secret)">复制</n-button>
                    </div>
                  </template>
                  <template v-else>
                    <span class="text-gray-400">••••••••••••（已加密）</span>
                    <span v-if="!canRevealCred" class="text-gray-400">无明文查看权限</span>
                  </template>
                </div>
                <div v-if="c.notes" class="text-xs text-gray-500">{{ c.notes }}</div>
              </div>
            </div>
            <n-empty v-else description="暂未挂载敏感凭据" class="py-6" />
          </n-tab-pane>

          <n-tab-pane name="audit" :tab="`变更时间线 (${audits.length})`">
            <n-timeline v-if="audits.length">
              <n-timeline-item
                v-for="a in audits"
                :key="a.id"
                type="info"
                :title="`${a.actor_username} · ${a.action}`"
                :time="new Date(a.created_at).toLocaleString()"
              >
                <div class="text-xs text-gray-500">IP: {{ a.client_ip }} ({{ a.auth_method }})</div>
                <pre v-if="a.diff_after" class="mt-1 bg-slate-50 p-2 rounded text-xs overflow-auto max-h-140px">{{ JSON.stringify(a.diff_after, null, 2) }}</pre>
              </n-timeline-item>
            </n-timeline>
            <n-empty v-else description="暂无变更记录" />
          </n-tab-pane>
        </n-tabs>
      </n-spin>
    </n-drawer-content>
  </n-drawer>

  <n-modal v-model:show="credModalOpen" preset="card" title="新增加密凭据" class="w-460px">
    <n-form label-placement="left" label-width="80">
      <n-form-item label="用途" required>
        <n-input v-model:value="credForm.purpose" placeholder="如：IPMI 管理口 / 控制台管理员 / API Token" />
      </n-form-item>
      <n-form-item label="账号">
        <n-input v-model:value="credForm.username" placeholder="可选" />
      </n-form-item>
      <n-form-item label="密码/密钥" required>
        <n-input v-model:value="credForm.secret" type="password" show-password-on="click" />
      </n-form-item>
      <n-form-item label="备注">
        <n-input v-model:value="credForm.notes" type="textarea" :rows="2" />
      </n-form-item>
    </n-form>
    <template #footer>
      <div class="flex justify-end gap-2">
        <n-button @click="credModalOpen = false">取消</n-button>
        <n-button type="primary" @click="handleCreateCred">加密保存</n-button>
      </div>
    </template>
  </n-modal>
</template>
