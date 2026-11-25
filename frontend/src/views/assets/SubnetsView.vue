<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NProgress, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { ipApi, nicApi, subnetApi, type NicItem, type SubnetItem } from '@/api/assets'
import { extractErrorMessage } from '@/api/client'
import { useOptionsStore } from '@/stores/options'
import SubnetIpMatrix from '@/components/SubnetIpMatrix.vue'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'

const options = useOptionsStore()
const message = useMessage()

const loading = ref(false)
const items = ref<SubnetItem[]>([])
const total = ref(0)
const filters = reactive({ q: '', network_zone_id: null as number | null, env: null as string | null, page: 1 })

const modalOpen = ref(false)
const editing = ref<SubnetItem | null>(null)
const form = reactive({
  name: '',
  cidr: '',
  gateway: '',
  vlan_id: null as number | null,
  network_zone_id: null as number | null,
  group_id: null as number | null,
  env: null as string | null,
  status: 'in_use',
  description: '',
  tag_ids: [] as number[],
})

const matrixDrawerOpen = ref(false)
const activeSubnet = ref<SubnetItem | null>(null)
const matrixRefreshKey = ref(0)

// Quick allocate from IP matrix cell
const allocModalOpen = ref(false)
const nics = ref<NicItem[]>([])
const allocForm = reactive({
  ip_address: '',
  ip_type: 'host' as 'host' | 'vip' | 'reserved' | 'floating' | 'gateway',
  nic_id: null as number | null,
  status: 'allocated',
  usage: '',
})

// Panoramic IP drawer
const ipDrawerOpen = ref(false)
const inspectIpId = ref<number | null>(null)

async function loadData() {
  loading.value = true
  try {
    await options.ensureLoaded()
    const res = await subnetApi.list({
      q: filters.q || undefined,
      network_zone_id: filters.network_zone_id || undefined,
      env: filters.env || undefined,
      page: filters.page,
      page_size: 20,
    })
    items.value = res.data.items
    total.value = res.data.total
  } catch (err) {
    message.error(extractErrorMessage(err, '加载网段失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  const defaultZone = options.zones.find((z) => z.is_default)
  form.name = ''
  form.cidr = ''
  form.gateway = ''
  form.vlan_id = null
  form.network_zone_id = defaultZone?.id ?? null
  form.group_id = null
  form.env = null
  form.status = 'in_use'
  form.description = ''
  form.tag_ids = []
  modalOpen.value = true
}

function openEdit(row: SubnetItem) {
  editing.value = row
  form.name = row.name
  form.cidr = row.cidr
  form.gateway = row.gateway || ''
  form.vlan_id = row.vlan_id
  form.network_zone_id = row.network_zone_id
  form.group_id = row.group_id
  form.env = row.env
  form.status = row.status
  form.description = row.description || ''
  form.tag_ids = row.tags.map((t) => t.id)
  modalOpen.value = true
}

async function handleSave() {
  try {
    if (editing.value) {
      await subnetApi.update(editing.value.id, {
        name: form.name,
        gateway: form.gateway,
        vlan_id: form.vlan_id,
        group_id: form.group_id,
        env: form.env,
        status: form.status,
        description: form.description || null,
        tag_ids: form.tag_ids,
      })
      message.success('网段已更新')
    } else {
      await subnetApi.create({
        name: form.name,
        cidr: form.cidr,
        gateway: form.gateway || null,
        vlan_id: form.vlan_id,
        network_zone_id: form.network_zone_id,
        group_id: form.group_id,
        env: form.env,
        status: form.status,
        description: form.description || null,
        tag_ids: form.tag_ids,
      })
      message.success('网段创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存网段失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await subnetApi.remove(id)
    message.success('网段已移入回收站')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

function openMatrix(row: SubnetItem) {
  activeSubnet.value = row
  matrixDrawerOpen.value = true
}

async function handleAllocateFromCell(ip: string) {
  allocForm.ip_address = ip
  allocForm.ip_type = 'host'
  allocForm.nic_id = null
  allocForm.status = 'allocated'
  allocForm.usage = ''
  allocModalOpen.value = true
  try {
    nics.value = (await nicApi.list({ page: 1, page_size: 200 })).data.items
  } catch {
    nics.value = []
  }
}

async function submitQuickAlloc() {
  if (!activeSubnet.value) return
  try {
    await ipApi.create({
      network_zone_id: activeSubnet.value.network_zone_id,
      subnet_id: activeSubnet.value.id,
      group_id: activeSubnet.value.group_id,
      ip_address: allocForm.ip_address,
      ip_type: allocForm.ip_type,
      nic_id: allocForm.nic_id,
      status: allocForm.status,
      usage: allocForm.usage || null,
    })
    message.success(`IP ${allocForm.ip_address} 分配成功`)
    allocModalOpen.value = false
    matrixRefreshKey.value += 1
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '分配 IP 失败'))
  }
}

async function copyNextFreeIp(row: SubnetItem) {
  try {
    const res = await subnetApi.nextFreeIp(row.id)
    if (!res.data.next_free_ip) {
      message.warning('该网段已无空闲 IP')
      return
    }
    await navigator.clipboard.writeText(res.data.next_free_ip)
    message.success(`下一个空闲 IP: ${res.data.next_free_ip}（已复制）`)
  } catch (err) {
    message.error(extractErrorMessage(err, '查询空闲 IP 失败'))
  }
}

const columns: DataTableColumns<SubnetItem> = [
  {
    title: '网段 CIDR',
    key: 'cidr',
    width: 170,
    render: (r) =>
      h(
        NButton,
        { text: true, type: 'primary', onClick: () => openMatrix(r) },
        { default: () => r.cidr },
      ),
  },
  { title: '名称/用途', key: 'name' },
  { title: '网络域', key: 'network_zone_name', width: 120 },
  { title: '网关', key: 'gateway', width: 130, render: (r) => r.gateway || '—' },
  { title: 'VLAN', key: 'vlan_id', width: 80, render: (r) => r.vlan_id ?? '—' },
  {
    title: '使用率',
    key: 'utilization_pct',
    width: 180,
    render: (r) =>
      h('div', { class: 'space-y-0.5' }, [
        h(NProgress, { type: 'line', percentage: r.utilization_pct, showIndicator: false, height: 8 }),
        h('div', { class: 'text-xs text-gray-500' }, `${r.allocated_count} / ${r.total_capacity} (${r.utilization_pct}%)`),
      ]),
  },
  {
    title: '环境',
    key: 'env',
    width: 90,
    render: (r) => (r.env ? h(NTag, { size: 'small' }, { default: () => options.dictLabel('env', r.env) }) : '—'),
  },
  { title: '归属分组', key: 'group_name', width: 120, render: (r) => r.group_name || '公共' },
  {
    title: '操作',
    key: 'actions',
    width: 240,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', type: 'primary', secondary: true, onClick: () => openMatrix(r) }, { default: () => 'IP 矩阵' }),
        h(NButton, { size: 'tiny', onClick: () => copyNextFreeIp(r) }, { default: () => '取空闲IP' }),
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(r.id) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `确认删除网段「${r.cidr}」？`,
          },
        ),
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="网段与 IPAM 管理">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.q" placeholder="搜索 CIDR / 名称" clearable class="w-180px" @keyup.enter="loadData" />
        <n-select
          v-model:value="filters.network_zone_id"
          clearable
          placeholder="网络域"
          class="w-140px"
          :options="options.zones.map((z) => ({ label: z.name, value: z.id }))"
        />
        <n-select
          v-model:value="filters.env"
          clearable
          placeholder="环境"
          class="w-110px"
          :options="options.dictOptions('env')"
        />
        <n-button @click="() => { filters.page = 1; loadData() }">筛选</n-button>
        <n-button type="primary" @click="openCreate">新建网段</n-button>
      </n-space>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="items" :bordered="false" />
    <div class="flex justify-end mt-4">
      <n-pagination v-model:page="filters.page" :page-size="20" :item-count="total" @update:page="loadData" />
    </div>

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑网段' : '新建网段'" class="w-520px">
      <n-form label-placement="left" label-width="85">
        <n-form-item label="网络域" required>
          <n-select v-model:value="form.network_zone_id" :disabled="Boolean(editing)" :options="options.zones.map((z) => ({ label: z.name, value: z.id }))" />
        </n-form-item>
        <n-form-item label="CIDR" required>
          <n-input v-model:value="form.cidr" :disabled="Boolean(editing)" placeholder="如：10.20.1.0/24" />
        </n-form-item>
        <n-form-item label="名称/用途" required>
          <n-input v-model:value="form.name" placeholder="如：生产核心业务网段" />
        </n-form-item>
        <n-form-item label="网关">
          <n-input v-model:value="form.gateway" placeholder="如：10.20.1.1" />
        </n-form-item>
        <n-form-item label="VLAN ID">
          <n-input-number v-model:value="form.vlan_id" :min="1" :max="4094" clearable class="w-full" />
        </n-form-item>
        <n-form-item label="归属分组">
          <n-select v-model:value="form.group_id" clearable placeholder="留空表示公共网段" :options="options.groups.map((g) => ({ label: g.name, value: g.id }))" />
        </n-form-item>
        <n-form-item label="环境">
          <n-select v-model:value="form.env" clearable :options="options.dictOptions('env')" />
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

    <n-drawer v-model:show="matrixDrawerOpen" width="860">
      <n-drawer-content :title="`IPAM 网段视图 · ${activeSubnet?.name || ''} (${activeSubnet?.cidr || ''})`">
        <SubnetIpMatrix
          :subnet-id="activeSubnet?.id ?? null"
          :refresh-key="matrixRefreshKey"
          @allocate-free="handleAllocateFromCell"
          @inspect-ip="(id: number) => { inspectIpId = id; ipDrawerOpen = true }"
        />
      </n-drawer-content>
    </n-drawer>

    <n-modal v-model:show="allocModalOpen" preset="card" :title="`分配 IP · ${allocForm.ip_address}`" class="w-460px">
      <n-form label-placement="left" label-width="85">
        <n-form-item label="IP 地址">
          <n-input :value="allocForm.ip_address" readonly />
        </n-form-item>
        <n-form-item label="类型">
          <n-select
            v-model:value="allocForm.ip_type"
            :options="[
              { label: '主机 IP (host)', value: 'host' },
              { label: 'VIP 漂移地址 (vip)', value: 'vip' },
              { label: '预留 (reserved)', value: 'reserved' },
              { label: '网关 (gateway)', value: 'gateway' },
            ]"
          />
        </n-form-item>
        <n-form-item label="绑定网卡">
          <n-select
            v-model:value="allocForm.nic_id"
            filterable
            clearable
            placeholder="可选（VIP/预留可留空）"
            :options="nics.map((n) => ({ label: `${n.device_hostname}:${n.name}`, value: n.id }))"
          />
        </n-form-item>
        <n-form-item label="状态">
          <n-select v-model:value="allocForm.status" :options="options.dictOptions('ip_status')" />
        </n-form-item>
        <n-form-item label="用途说明">
          <n-input v-model:value="allocForm.usage" placeholder="如：节点主业务口" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="allocModalOpen = false">取消</n-button>
          <n-button type="primary" @click="submitQuickAlloc">确认分配</n-button>
        </div>
      </template>
    </n-modal>

    <AssetDetailDrawer v-model:show="ipDrawerOpen" entity-type="ip" :entity-id="inspectIpId" />
  </n-card>
</template>
