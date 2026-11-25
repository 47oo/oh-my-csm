<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { deviceApi, nicApi, type DeviceItem, type NicItem } from '@/api/assets'
import { extractErrorMessage } from '@/api/client'
import { useOptionsStore } from '@/stores/options'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'

const options = useOptionsStore()
const message = useMessage()

const loading = ref(false)
const items = ref<NicItem[]>([])
const devices = ref<DeviceItem[]>([])
const total = ref(0)
const filters = reactive({ q: '', device_id: null as number | null, nic_type: null as string | null, page: 1 })

const modalOpen = ref(false)
const editing = ref<NicItem | null>(null)
const form = reactive({
  device_id: null as number | null,
  parent_nic_id: null as number | null,
  name: '',
  nic_type: 'business',
  mac_address: '',
  speed_mbps: null as number | null,
  vlan_id: null as number | null,
  bond_mode: '',
  switch_port: '',
  status: 'up',
  description: '',
})

const drawerOpen = ref(false)
const inspectId = ref<number | null>(null)

const sameDeviceParentOptions = computed(() =>
  items.value
    .filter((n) => n.device_id === form.device_id && n.id !== editing.value?.id)
    .map((n) => ({ label: `${n.name} (${n.nic_type})`, value: n.id })),
)

async function loadData() {
  loading.value = true
  try {
    await options.ensureLoaded()
    const [nRes, dRes] = await Promise.all([
      nicApi.list({
        q: filters.q || undefined,
        device_id: filters.device_id || undefined,
        nic_type: filters.nic_type || undefined,
        page: filters.page,
        page_size: 20,
      }),
      deviceApi.list({ page: 1, page_size: 200 }),
    ])
    items.value = nRes.data.items
    total.value = nRes.data.total
    devices.value = dRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载网卡失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    device_id: filters.device_id,
    parent_nic_id: null,
    name: '',
    nic_type: 'business',
    mac_address: '',
    speed_mbps: null,
    vlan_id: null,
    bond_mode: '',
    switch_port: '',
    status: 'up',
    description: '',
  })
  modalOpen.value = true
}

function openEdit(row: NicItem) {
  editing.value = row
  Object.assign(form, {
    device_id: row.device_id,
    parent_nic_id: row.parent_nic_id,
    name: row.name,
    nic_type: row.nic_type,
    mac_address: row.mac_address || '',
    speed_mbps: row.speed_mbps,
    vlan_id: row.vlan_id,
    bond_mode: row.bond_mode || '',
    switch_port: row.switch_port || '',
    status: row.status,
    description: row.description || '',
  })
  modalOpen.value = true
}

async function handleSave() {
  if (!form.device_id) {
    message.warning('请选择所属设备')
    return
  }
  const payload = {
    device_id: form.device_id,
    parent_nic_id: form.parent_nic_id,
    name: form.name,
    nic_type: form.nic_type,
    mac_address: form.mac_address || null,
    speed_mbps: form.speed_mbps,
    vlan_id: form.vlan_id,
    bond_mode: form.bond_mode || null,
    switch_port: form.switch_port || null,
    status: form.status,
    description: form.description || null,
  }
  try {
    const res = editing.value
      ? await nicApi.update(editing.value.id, payload)
      : await nicApi.create(payload)
    message.success(editing.value ? '网卡已更新' : '网卡创建成功')
    if (res.data.warnings?.length) {
      message.warning(res.data.warnings.join('；'))
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存网卡失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await nicApi.remove(id)
    message.success('网卡已移入回收站')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

const columns: DataTableColumns<NicItem> = [
  { title: '所属设备', key: 'device_hostname', width: 150 },
  {
    title: '网卡名称',
    key: 'name',
    width: 140,
    render: (r) =>
      h(NSpace, { size: 'small', align: 'center' }, () => [
        h(NButton, { text: true, type: 'primary', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => r.name }),
        r.parent_nic_name ? h(NTag, { size: 'tiny', type: 'warning' }, { default: () => `→ ${r.parent_nic_name}` }) : null,
      ]),
  },
  { title: '类型', key: 'nic_type', width: 130, render: (r) => options.dictLabel('nic_type', r.nic_type) },
  { title: 'MAC 地址', key: 'mac_address', width: 150, render: (r) => r.mac_address || '—' },
  {
    title: '绑定 IP',
    key: 'bound_ips',
    render: (r) =>
      r.bound_ips.length
        ? h(NSpace, { size: 'small', wrap: true }, () => r.bound_ips.map((ip) => h(NTag, { size: 'tiny', type: 'info' }, { default: () => ip })))
        : '—',
  },
  { title: 'VLAN', key: 'vlan_id', width: 80, render: (r) => r.vlan_id ?? '—' },
  { title: '上联交换机口', key: 'switch_port', width: 150, render: (r) => r.switch_port || '—' },
  {
    title: '操作',
    key: 'actions',
    width: 170,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => '全景' }),
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(r.id) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `确认删除网卡「${r.name}」？`,
          },
        ),
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="网卡台账（支持 Bond 聚合层级与 IP 绑定）">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.q" placeholder="搜索网卡/MAC/交换机口/主机名" clearable class="w-210px" @keyup.enter="loadData" />
        <n-select
          v-model:value="filters.device_id"
          clearable
          filterable
          placeholder="按设备筛选"
          class="w-160px"
          :options="devices.map((d) => ({ label: d.hostname, value: d.id }))"
        />
        <n-select v-model:value="filters.nic_type" clearable placeholder="网卡类型" class="w-130px" :options="options.dictOptions('nic_type')" />
        <n-button @click="() => { filters.page = 1; loadData() }">查询</n-button>
        <n-button type="primary" @click="openCreate">新建网卡</n-button>
      </n-space>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="items" :bordered="false" />
    <div class="flex justify-end mt-4">
      <n-pagination v-model:page="filters.page" :page-size="20" :item-count="total" @update:page="loadData" />
    </div>

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑网卡' : '新建网卡'" class="w-540px">
      <n-form label-placement="left" label-width="95">
        <n-form-item label="所属设备" required>
          <n-select
            v-model:value="form.device_id"
            filterable
            :disabled="Boolean(editing)"
            :options="devices.map((d) => ({ label: d.hostname, value: d.id }))"
          />
        </n-form-item>
        <n-form-item label="网卡名称" required>
          <n-input v-model:value="form.name" placeholder="如：eth0 / bond0 / idrac" />
        </n-form-item>
        <n-form-item label="网卡类型">
          <n-select v-model:value="form.nic_type" :options="options.dictOptions('nic_type')" />
        </n-form-item>
        <n-form-item label="上级聚合口">
          <n-select
            v-model:value="form.parent_nic_id"
            clearable
            placeholder="如该口是 Bond 成员，请选择对应的 bond0"
            :options="sameDeviceParentOptions"
          />
        </n-form-item>
        <n-form-item label="MAC 地址">
          <n-input v-model:value="form.mac_address" placeholder="aa:bb:cc:dd:ee:ff" />
        </n-form-item>
        <n-form-item label="速率 (Mbps)">
          <n-input-number v-model:value="form.speed_mbps" :min="10" clearable class="w-full" placeholder="1000 / 10000" />
        </n-form-item>
        <n-form-item label="VLAN ID">
          <n-input-number v-model:value="form.vlan_id" :min="1" :max="4094" clearable class="w-full" />
        </n-form-item>
        <n-form-item label="Bond 模式">
          <n-input v-model:value="form.bond_mode" placeholder="如：mode4-802.3ad / mode1" />
        </n-form-item>
        <n-form-item label="上联交换机口">
          <n-input v-model:value="form.switch_port" placeholder="如：SW-Core-01:GE1/0/24" />
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

    <AssetDetailDrawer v-model:show="drawerOpen" entity-type="nic" :entity-id="inspectId" />
  </n-card>
</template>
