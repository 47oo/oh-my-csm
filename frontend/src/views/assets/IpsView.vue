<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import {
  clusterApi,
  ipApi,
  nicApi,
  serviceApi,
  subnetApi,
  type ClusterItem,
  type IpItem,
  type NicItem,
  type ServiceItem,
  type SubnetItem,
  type VipBindingItem,
} from '@/api/assets'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useOptionsStore } from '@/stores/options'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'
import BatchActionBar from '@/components/BatchActionBar.vue'

const auth = useAuthStore()
const options = useOptionsStore()
const message = useMessage()

const loading = ref(false)
const items = ref<IpItem[]>([])
const selectedIds = ref<number[]>([])
const subnets = ref<SubnetItem[]>([])
const nics = ref<NicItem[]>([])
const clusters = ref<ClusterItem[]>([])
const services = ref<ServiceItem[]>([])
const total = ref(0)

const filters = reactive({
  q: '',
  network_zone_id: null as number | null,
  subnet_id: null as number | null,
  ip_type: null as string | null,
  status: null as string | null,
  page: 1,
})

const modalOpen = ref(false)
const editing = ref<IpItem | null>(null)
const fetchingFreeIp = ref(false)
const form = reactive({
  ip_address: '',
  network_zone_id: null as number | null,
  subnet_id: null as number | null,
  nic_id: null as number | null,
  cluster_id: null as number | null,
  group_id: null as number | null,
  ip_type: 'host' as 'host' | 'vip' | 'gateway' | 'floating' | 'reserved',
  is_primary: false,
  status: 'allocated',
  usage: '',
  description: '',
  tag_ids: [] as number[],
})

// VIP bindings modal
const vipModalOpen = ref(false)
const activeVip = ref<IpItem | null>(null)
const vipBindings = ref<VipBindingItem[]>([])
const vipForm = reactive({
  target_type: 'nic' as 'nic' | 'cluster' | 'service',
  target_id: null as number | null,
  role: 'master',
  description: '',
})

const vipTargetOptions = computed(() => {
  if (vipForm.target_type === 'nic') {
    return nics.value.map((n) => ({ label: `${n.device_hostname}:${n.name}`, value: n.id }))
  }
  if (vipForm.target_type === 'cluster') {
    return clusters.value.map((c) => ({ label: c.name, value: c.id }))
  }
  return services.value.map((s) => ({ label: s.name, value: s.id }))
})

const drawerOpen = ref(false)
const inspectId = ref<number | null>(null)

async function loadData() {
  loading.value = true
  try {
    await options.ensureLoaded()
    const [ipRes, subRes, nicRes, clRes] = await Promise.all([
      ipApi.list({
        q: filters.q || undefined,
        network_zone_id: filters.network_zone_id || undefined,
        subnet_id: filters.subnet_id || undefined,
        ip_type: filters.ip_type || undefined,
        status: filters.status || undefined,
        page: filters.page,
        page_size: 20,
      }),
      subnetApi.list({ page: 1, page_size: 200 }),
      nicApi.list({ page: 1, page_size: 200 }),
      clusterApi.list({ page: 1, page_size: 200 }),
    ])
    items.value = ipRes.data.items
    total.value = ipRes.data.total
    subnets.value = subRes.data.items
    nics.value = nicRes.data.items
    clusters.value = clRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载 IP 台账失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    ip_address: '',
    network_zone_id: options.zones.find((z) => z.is_default)?.id ?? null,
    subnet_id: null,
    nic_id: null,
    cluster_id: !auth.hasWritableGroup && auth.writableClusterIds.length ? auth.writableClusterIds[0] : null,
    group_id: null,
    ip_type: 'host',
    is_primary: false,
    status: 'allocated',
    usage: '',
    description: '',
    tag_ids: [],
  })
  modalOpen.value = true
}

function openEdit(row: IpItem) {
  editing.value = row
  Object.assign(form, {
    ip_address: row.ip_address,
    network_zone_id: row.network_zone_id,
    subnet_id: row.subnet_id,
    nic_id: row.nic_id,
    group_id: row.group_id,
    ip_type: row.ip_type,
    is_primary: row.is_primary,
    status: row.status,
    usage: row.usage || '',
    description: row.description || '',
    tag_ids: row.tags.map((t) => t.id),
  })
  modalOpen.value = true
}

function handleSubnetChange(subnetId: number | null) {
  if (!editing.value && subnetId !== null) {
    const matched = subnets.value.find((s) => s.id === subnetId)
    if (matched) {
      form.network_zone_id = matched.network_zone_id
    }
  }
}

async function handleSuggestIp() {
  if (!form.subnet_id) {
    message.warning('请先选择所属网段')
    return
  }
  fetchingFreeIp.value = true
  try {
    const res = await subnetApi.nextFreeIp(form.subnet_id)
    if (res.data.next_free_ip) {
      form.ip_address = res.data.next_free_ip
      message.success(`已获取可用 IP：${res.data.next_free_ip}`)
    } else {
      message.warning(`网段 ${res.data.cidr} 已无可用空闲 IP`)
    }
  } catch (err) {
    message.error(extractErrorMessage(err, '获取推荐 IP 失败'))
  } finally {
    fetchingFreeIp.value = false
  }
}

async function handleSave() {
  try {
    if (editing.value) {
      await ipApi.update(editing.value.id, {
        subnet_id: form.subnet_id,
        nic_id: form.nic_id,
        clear_nic: form.nic_id === null,
        group_id: form.group_id,
        ip_type: form.ip_type,
        is_primary: form.is_primary,
        status: form.status,
        usage: form.usage || null,
        description: form.description || null,
        tag_ids: form.tag_ids,
      })
      message.success('IP 记录已更新')
    } else {
      const trimmedIp = form.ip_address.trim()
      if (!trimmedIp && !form.subnet_id) {
        message.warning('请填写 IP 地址，或选择所属网段以自动分配')
        return
      }
      const res = await ipApi.create({
        ip_address: trimmedIp || null,
        network_zone_id: form.network_zone_id,
        subnet_id: form.subnet_id,
        nic_id: form.nic_id,
        cluster_id: form.ip_type === 'vip' ? form.cluster_id : null,
        group_id: form.group_id,
        ip_type: form.ip_type,
        is_primary: form.is_primary,
        status: form.status,
        usage: form.usage || null,
        description: form.description || null,
        tag_ids: form.tag_ids,
      })
      message.success(trimmedIp ? 'IP 录入成功' : `已自动分配 IP：${res.data.ip_address}`)
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存 IP 失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await ipApi.remove(id)
    message.success('IP 已移入回收站')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

async function openVipBindings(row: IpItem) {
  activeVip.value = row
  vipModalOpen.value = true
  vipForm.target_id = null
  try {
    const [dRes, cRes, sRes] = await Promise.all([
      ipApi.detail(row.id),
      clusterApi.list({ page: 1, page_size: 200 }),
      serviceApi.list({ page: 1, page_size: 200 }),
    ])
    vipBindings.value = dRes.data.vip_bindings
    clusters.value = cRes.data.items
    services.value = sRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载 VIP 绑定失败'))
  }
}

async function handleAddVipBinding() {
  if (!activeVip.value || !vipForm.target_id) {
    message.warning('请选择承载目标')
    return
  }
  try {
    await ipApi.addVipBinding(activeVip.value.id, {
      target_type: vipForm.target_type,
      target_id: vipForm.target_id,
      role: vipForm.role || null,
      description: vipForm.description || null,
    })
    message.success('VIP 绑定已添加')
    await openVipBindings(activeVip.value)
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '添加 VIP 绑定失败'))
  }
}

async function handleRemoveVipBinding(bindingId: number) {
  if (!activeVip.value) return
  try {
    await ipApi.removeVipBinding(activeVip.value.id, bindingId)
    message.success('已解除 VIP 绑定')
    await openVipBindings(activeVip.value)
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '解除失败'))
  }
}

const columns: DataTableColumns<IpItem> = [
  { type: 'selection' },
  {
    title: 'IP 地址',
    key: 'ip_address',
    width: 150,
    render: (r) =>
      h(NSpace, { size: 'small', align: 'center' }, () => [
        h(NButton, { text: true, type: 'primary', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => r.ip_address }),
        r.is_primary ? h(NTag, { size: 'tiny', type: 'success' }, { default: () => '主' }) : null,
      ]),
  },
  {
    title: '类型',
    key: 'ip_type',
    width: 95,
    render: (r) =>
      h(NTag, { size: 'small', type: r.ip_type === 'vip' ? 'warning' : 'info' }, { default: () => r.ip_type.toUpperCase() }),
  },
  {
    title: '状态',
    key: 'status',
    width: 95,
    render: (r) => h(NTag, { size: 'small' }, { default: () => options.dictLabel('ip_status', r.status) }),
  },
  { title: '所属网段', key: 'subnet_cidr', width: 150, render: (r) => r.subnet_cidr || '—' },
  {
    title: '绑定设备 / 网卡',
    key: 'nic',
    width: 190,
    render: (r) =>
      r.device_hostname
        ? `${r.device_hostname} : ${r.nic_name}`
        : r.vip_binding_count > 0
          ? `VIP 绑定 (${r.vip_binding_count})`
          : '未绑定网卡',
  },
  { title: '网络域', key: 'network_zone_name', width: 110 },
  { title: '用途', key: 'usage', ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 230,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => '全景' }),
        r.ip_type === 'vip'
          ? h(NButton, { size: 'tiny', type: 'warning', secondary: true, onClick: () => openVipBindings(r) }, { default: () => 'VIP绑定' })
          : null,
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(r.id) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `确认删除 IP「${r.ip_address}」？`,
          },
        ),
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="IP 地址台账">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.q" placeholder="搜索 IP / 用途 / 备注" clearable class="w-180px" @keyup.enter="loadData" />
        <n-select
          v-model:value="filters.subnet_id"
          clearable
          filterable
          placeholder="所属网段"
          class="w-160px"
          :options="subnets.map((s) => ({ label: `${s.cidr} (${s.name})`, value: s.id }))"
        />
        <n-select
          v-model:value="filters.ip_type"
          clearable
          placeholder="类型"
          class="w-100px"
          :options="[
            { label: 'HOST', value: 'host' },
            { label: 'VIP', value: 'vip' },
            { label: 'RESERVED', value: 'reserved' },
            { label: 'GATEWAY', value: 'gateway' },
          ]"
        />
        <n-button @click="() => { filters.page = 1; loadData() }">查询</n-button>
        <n-button type="primary" @click="openCreate">录入 IP</n-button>
      </n-space>
    </template>

    <BatchActionBar
      entity="ips"
      :selected-ids="selectedIds"
      :allow-env="false"
      status-dict-category="ip_status"
      @clear="selectedIds = []"
      @done="loadData"
    />

    <n-data-table
      :loading="loading"
      :columns="columns"
      :data="items"
      :row-key="(r: IpItem) => r.id"
      :checked-row-keys="selectedIds"
      @update:checked-row-keys="(keys: Array<string | number>) => { selectedIds = keys.map(Number) }"
      :bordered="false"
    />
    <div class="flex justify-end mt-4">
      <n-pagination v-model:page="filters.page" :page-size="20" :item-count="total" @update:page="loadData" />
    </div>

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑 IP' : '录入 IP'" class="w-540px">
      <n-form label-placement="left" label-width="90">
        <n-form-item label="所属网段">
          <n-select
            v-model:value="form.subnet_id"
            clearable
            filterable
            placeholder="选择网段可留空 IP 自动分配，或留空按最长前缀匹配"
            :options="subnets.map((s) => ({ label: `${s.cidr} (${s.name})`, value: s.id }))"
            @update:value="handleSubnetChange"
          />
        </n-form-item>
        <n-form-item label="IP 地址" :required="!editing && !form.subnet_id">
          <div class="w-full flex gap-2">
            <n-input
              v-model:value="form.ip_address"
              :disabled="Boolean(editing)"
              placeholder="留空则从所选网段自动分配可用 IP"
            />
            <n-button
              v-if="!editing"
              secondary
              type="info"
              :disabled="!form.subnet_id"
              :loading="fetchingFreeIp"
              @click="handleSuggestIp"
            >
              推荐可用 IP
            </n-button>
          </div>
        </n-form-item>
        <n-form-item label="网络域">
          <n-select v-model:value="form.network_zone_id" :disabled="Boolean(editing)" :options="options.zones.map((z) => ({ label: z.name, value: z.id }))" />
        </n-form-item>
        <n-form-item label="IP 类型">
          <n-select
            v-model:value="form.ip_type"
            :options="[
              { label: '主机 IP (host)', value: 'host' },
              { label: 'VIP 漂移地址 (vip)', value: 'vip' },
              { label: '浮动 IP (floating)', value: 'floating' },
              { label: '预留 (reserved)', value: 'reserved' },
              { label: '网关 (gateway)', value: 'gateway' },
            ]"
          />
        </n-form-item>
        <n-form-item v-if="!editing && form.ip_type === 'vip'" label="归属集群">
          <n-select
            v-model:value="form.cluster_id"
            clearable
            filterable
            placeholder="VIP 可直接绑定到目标集群"
            :options="clusters.map((c) => ({ label: c.name, value: c.id }))"
          />
        </n-form-item>
        <n-form-item label="绑定网卡">
          <n-select
            v-model:value="form.nic_id"
            clearable
            filterable
            placeholder="普通主机 IP 建议绑定到具体设备网卡"
            :options="nics.map((n) => ({ label: `${n.device_hostname}:${n.name}`, value: n.id }))"
          />
        </n-form-item>
        <n-form-item label="为主 IP">
          <n-switch v-model:value="form.is_primary" />
        </n-form-item>
        <n-form-item label="状态">
          <n-select v-model:value="form.status" :options="options.dictOptions('ip_status')" />
        </n-form-item>
        <n-form-item label="归属分组">
          <n-select v-model:value="form.group_id" clearable placeholder="留空自动继承网卡/网段分组" :options="options.groups.map((g) => ({ label: g.name, value: g.id }))" />
        </n-form-item>
        <n-form-item label="用途说明">
          <n-input v-model:value="form.usage" />
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

    <n-modal v-model:show="vipModalOpen" preset="card" :title="`VIP 多目标绑定 · ${activeVip?.ip_address || ''}`" class="w-580px">
      <div class="flex gap-2 mb-4">
        <n-select
          v-model:value="vipForm.target_type"
          class="w-110px"
          :options="[
            { label: '承载网卡', value: 'nic' },
            { label: '所属集群', value: 'cluster' },
            { label: '关联服务', value: 'service' },
          ]"
          @update:value="vipForm.target_id = null"
        />
        <n-select v-model:value="vipForm.target_id" filterable placeholder="选择目标对象" :options="vipTargetOptions" />
        <n-input v-model:value="vipForm.role" placeholder="角色(master/backup)" class="w-130px" />
        <n-button type="primary" @click="handleAddVipBinding">添加</n-button>
      </div>
      <n-data-table
        size="small"
        :data="vipBindings"
        :columns="[
          { title: '目标类型', key: 'target_type', width: 90 },
          { title: '目标对象', key: 'target_label' },
          { title: '角色', key: 'role', width: 90 },
          {
            title: '操作',
            key: 'op',
            width: 80,
            render: (r: VipBindingItem) => h(NButton, { size: 'tiny', type: 'error', quaternary: true, onClick: () => handleRemoveVipBinding(r.id) }, { default: () => '解绑' }),
          },
        ]"
      />
    </n-modal>

    <AssetDetailDrawer v-model:show="drawerOpen" entity-type="ip" :entity-id="inspectId" />
  </n-card>
</template>
