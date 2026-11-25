<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import {
  ipApi,
  serviceApi,
  urlApi,
  type IpItem,
  type ServiceItem,
  type UrlItem,
} from '@/api/assets'
import { extractErrorMessage } from '@/api/client'
import { useOptionsStore } from '@/stores/options'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'
import BatchActionBar from '@/components/BatchActionBar.vue'

const options = useOptionsStore()
const message = useMessage()

const loading = ref(false)
const items = ref<UrlItem[]>([])
const selectedIds = ref<number[]>([])
const services = ref<ServiceItem[]>([])
const ips = ref<IpItem[]>([])
const total = ref(0)

const filters = reactive({
  q: '',
  domain: '',
  access_type: null as string | null,
  env: null as string | null,
  expiring_days: null as number | null,
  page: 1,
})

const modalOpen = ref(false)
const editing = ref<UrlItem | null>(null)
const form = reactive({
  name: '',
  full_url: '',
  service_id: null as number | null,
  entry_ip_id: null as number | null,
  group_id: null as number | null,
  access_type: 'internal' as 'internal' | 'external' | 'vpn',
  env: null as string | null,
  status: 'in_use',
  ssl_expires_at: null as number | null,
  description: '',
  tag_ids: [] as number[],
})

const drawerOpen = ref(false)
const inspectId = ref<number | null>(null)

async function loadData() {
  loading.value = true
  try {
    await options.ensureLoaded()
    const [uRes, sRes, ipRes] = await Promise.all([
      urlApi.list({
        q: filters.q || undefined,
        domain: filters.domain || undefined,
        access_type: filters.access_type || undefined,
        env: filters.env || undefined,
        expiring_days: filters.expiring_days || undefined,
        page: filters.page,
        page_size: 20,
      }),
      serviceApi.list({ page: 1, page_size: 200 }),
      ipApi.list({ page: 1, page_size: 200 }),
    ])
    items.value = uRes.data.items
    total.value = uRes.data.total
    services.value = sRes.data.items
    ips.value = ipRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载 URL 台账失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    name: '',
    full_url: '',
    service_id: null,
    entry_ip_id: null,
    group_id: null,
    access_type: 'internal',
    env: null,
    status: 'in_use',
    ssl_expires_at: null,
    description: '',
    tag_ids: [],
  })
  modalOpen.value = true
}

function openEdit(row: UrlItem) {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    full_url: row.full_url,
    service_id: row.service_id,
    entry_ip_id: row.entry_ip_id,
    group_id: row.group_id,
    access_type: row.access_type,
    env: row.env,
    status: row.status,
    ssl_expires_at: row.ssl_expires_at ? new Date(row.ssl_expires_at).getTime() : null,
    description: row.description || '',
    tag_ids: row.tags.map((t) => t.id),
  })
  modalOpen.value = true
}

async function handleSave() {
  const isoSsl = form.ssl_expires_at ? new Date(form.ssl_expires_at).toISOString() : null
  try {
    if (editing.value) {
      await urlApi.update(editing.value.id, {
        name: form.name,
        full_url: form.full_url,
        service_id: form.service_id,
        clear_service: form.service_id === null,
        entry_ip_id: form.entry_ip_id,
        clear_entry_ip: form.entry_ip_id === null,
        group_id: form.group_id,
        access_type: form.access_type,
        env: form.env,
        status: form.status,
        ssl_expires_at: isoSsl,
        description: form.description || null,
        tag_ids: form.tag_ids,
      })
      message.success('URL 记录已更新')
    } else {
      await urlApi.create({
        name: form.name,
        full_url: form.full_url,
        service_id: form.service_id,
        entry_ip_id: form.entry_ip_id,
        group_id: form.group_id,
        access_type: form.access_type,
        env: form.env,
        status: form.status,
        ssl_expires_at: isoSsl,
        description: form.description || null,
        tag_ids: form.tag_ids,
      })
      message.success('URL 录入成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存 URL 失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await urlApi.remove(id)
    message.success('URL 已移入回收站')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

async function copyUrl(url: string) {
  await navigator.clipboard.writeText(url)
  message.success('已复制 URL')
}

function sslBadge(expiresAt: string | null) {
  if (!expiresAt) return '—'
  const days = Math.ceil((new Date(expiresAt).getTime() - Date.now()) / 86400000)
  const type = days <= 15 ? 'error' : days <= 30 ? 'warning' : 'success'
  return h(NTag, { size: 'tiny', type }, { default: () => (days < 0 ? `已过期 ${-days} 天` : `剩 ${days} 天`) })
}

const columns: DataTableColumns<UrlItem> = [
  { type: 'selection' },
  {
    title: '名称',
    key: 'name',
    width: 150,
    render: (r) =>
      h(
        NButton,
        { text: true, type: 'primary', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } },
        { default: () => r.name },
      ),
  },
  {
    title: '完整 URL',
    key: 'full_url',
    ellipsis: { tooltip: true },
    render: (r) =>
      h(NSpace, { size: 'small', align: 'center', wrap: false }, () => [
        h('span', { class: 'font-mono text-xs text-blue-600' }, r.full_url),
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => copyUrl(r.full_url) }, { default: () => '复制' }),
      ]),
  },
  { title: '域名', key: 'domain', width: 170, ellipsis: { tooltip: true } },
  {
    title: '访问类型',
    key: 'access_type',
    width: 90,
    render: (r) =>
      h(
        NTag,
        { size: 'small', type: r.access_type === 'external' ? 'error' : r.access_type === 'vpn' ? 'warning' : 'info' },
        { default: () => (r.access_type === 'external' ? '公网' : r.access_type === 'vpn' ? 'VPN' : '内网') },
      ),
  },
  { title: '关联服务', key: 'service_name', width: 130, render: (r) => r.service_name || '待补全' },
  { title: '入口 IP', key: 'entry_ip_address', width: 125, render: (r) => r.entry_ip_address || '—' },
  { title: 'SSL 证书', key: 'ssl_expires_at', width: 105, render: (r) => sslBadge(r.ssl_expires_at) },
  {
    title: '操作',
    key: 'actions',
    width: 170,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => '反查链路' }),
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(r.id) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `确认删除 URL「${r.name}」？`,
          },
        ),
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="URL 入口台账（点击任意记录可一键反查服务 / IP / 网卡 / 设备 / 集群）">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.q" placeholder="搜索名称 / URL" clearable class="w-170px" @keyup.enter="loadData" />
        <n-input v-model:value="filters.domain" placeholder="按域名筛选" clearable class="w-150px" @keyup.enter="loadData" />
        <n-select
          v-model:value="filters.access_type"
          clearable
          placeholder="内外网"
          class="w-100px"
          :options="[
            { label: '内网', value: 'internal' },
            { label: '公网', value: 'external' },
            { label: 'VPN', value: 'vpn' },
          ]"
        />
        <n-select
          v-model:value="filters.expiring_days"
          clearable
          placeholder="证书到期"
          class="w-120px"
          :options="[
            { label: '15 天内到期', value: 15 },
            { label: '30 天内到期', value: 30 },
            { label: '90 天内到期', value: 90 },
          ]"
        />
        <n-button @click="() => { filters.page = 1; loadData() }">查询</n-button>
        <n-button type="primary" @click="openCreate">录入 URL</n-button>
      </n-space>
    </template>

    <BatchActionBar
      entity="urls"
      :selected-ids="selectedIds"
      @clear="selectedIds = []"
      @done="loadData"
    />

    <n-data-table
      :loading="loading"
      :columns="columns"
      :data="items"
      :row-key="(r: UrlItem) => r.id"
      :checked-row-keys="selectedIds"
      @update:checked-row-keys="(keys: Array<string | number>) => { selectedIds = keys.map(Number) }"
      :bordered="false"
    />
    <div class="flex justify-end mt-4">
      <n-pagination v-model:page="filters.page" :page-size="20" :item-count="total" @update:page="loadData" />
    </div>

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑 URL' : '录入 URL'" class="w-560px">
      <n-form label-placement="left" label-width="90">
        <n-form-item label="入口名称" required>
          <n-input v-model:value="form.name" placeholder="如：生产订单网关" />
        </n-form-item>
        <n-form-item label="完整 URL" required>
          <n-input v-model:value="form.full_url" placeholder="https://api.example.com:8443/v1（自动解析域名与端口）" />
        </n-form-item>
        <n-form-item label="访问类型">
          <n-radio-group v-model:value="form.access_type">
            <n-space>
              <n-radio value="internal">内网</n-radio>
              <n-radio value="external">公网</n-radio>
              <n-radio value="vpn">VPN</n-radio>
            </n-space>
          </n-radio-group>
        </n-form-item>
        <n-form-item label="关联服务">
          <n-select v-model:value="form.service_id" clearable filterable placeholder="可选（支持渐进补全）" :options="services.map((s) => ({ label: s.name, value: s.id }))" />
        </n-form-item>
        <n-form-item label="入口 IP">
          <n-select v-model:value="form.entry_ip_id" clearable filterable placeholder="如 Nginx / SLB / VIP" :options="ips.map((i) => ({ label: `${i.ip_address} (${i.ip_type})`, value: i.id }))" />
        </n-form-item>
        <n-form-item label="归属分组">
          <n-select v-model:value="form.group_id" clearable :options="options.groups.map((g) => ({ label: g.name, value: g.id }))" />
        </n-form-item>
        <n-form-item label="环境">
          <n-select v-model:value="form.env" clearable :options="options.dictOptions('env')" />
        </n-form-item>
        <n-form-item label="SSL 证书到期">
          <n-date-picker v-model:value="form.ssl_expires_at" type="datetime" clearable class="w-full" />
        </n-form-item>
        <n-form-item label="标签">
          <n-select v-model:value="form.tag_ids" multiple :options="options.tags.map((t) => ({ label: t.name, value: t.id }))" />
        </n-form-item>
        <n-form-item label="备注">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="modalOpen = false">取消</n-button>
          <n-button type="primary" @click="handleSave">保存</n-button>
        </div>
      </template>
    </n-modal>

    <AssetDetailDrawer v-model:show="drawerOpen" entity-type="url" :entity-id="inspectId" />
  </n-card>
</template>
