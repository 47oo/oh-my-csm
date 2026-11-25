<script setup lang="ts">
import { h, onMounted, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { recycleBinApi, type RecycleBinItem } from '@/api/m3'
import { useAuthStore } from '@/stores/auth'
import { extractErrorMessage } from '@/api/client'

const auth = useAuthStore()
const message = useMessage()
const loading = ref(false)
const items = ref<RecycleBinItem[]>([])
const activeType = ref<string>('')

const typeOptions = [
  { label: '全部类型', value: '' },
  { label: '集群', value: 'cluster' },
  { label: '设备', value: 'device' },
  { label: '网卡', value: 'nic' },
  { label: '网段', value: 'subnet' },
  { label: 'IP 地址', value: 'ip' },
  { label: '服务', value: 'service' },
  { label: 'URL', value: 'url' },
  { label: '凭据', value: 'credential' },
  { label: '运维分组', value: 'ops_group' },
  { label: '网络域', value: 'network_zone' },
]

async function loadData() {
  loading.value = true
  try {
    items.value = (await recycleBinApi.list(activeType.value || undefined)).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载回收站失败'))
  } finally {
    loading.value = false
  }
}

async function handleRestore(row: RecycleBinItem) {
  try {
    await recycleBinApi.restore(row.entity_type, row.id)
    message.success(`已恢复 ${row.label}`)
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '恢复失败'))
  }
}

async function handlePurge(row: RecycleBinItem) {
  try {
    await recycleBinApi.purge(row.entity_type, row.id)
    message.success(`已彻底清理 ${row.label}`)
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '清理失败'))
  }
}

const columns: DataTableColumns<RecycleBinItem> = [
  {
    title: '类型',
    key: 'entity_type',
    width: 110,
    render: (r) => h(NTag, { size: 'small', type: 'info' }, { default: () => r.entity_type }),
  },
  { title: '对象标识', key: 'label' },
  { title: '补充信息', key: 'subtitle', ellipsis: { tooltip: true }, render: (r) => r.subtitle || '—' },
  { title: '归属分组', key: 'group_name', width: 130, render: (r) => r.group_name || '公共' },
  {
    title: '删除时间',
    key: 'deleted_at',
    width: 185,
    render: (r) => new Date(r.deleted_at).toLocaleString(),
  },
  {
    title: '操作',
    key: 'actions',
    width: 180,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', type: 'primary', secondary: true, onClick: () => handleRestore(r) }, { default: () => '恢复' }),
        auth.hasGlobalPerm('recycle_bin:purge')
          ? h(
              NPopconfirm,
              { onPositiveClick: () => handlePurge(r) },
              {
                trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '彻底清理' }),
                default: () => `警告：彻底物理删除「${r.label}」后无法恢复，确认继续？`,
              },
            )
          : null,
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="回收站（软删除保护与冲突安全恢复）">
    <template #header-extra>
      <n-space>
        <n-select
          v-model:value="activeType"
          class="w-140px"
          :options="typeOptions"
          @update:value="loadData"
        />
        <n-button @click="loadData">刷新</n-button>
      </n-space>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="items" :bordered="false" />
  </n-card>
</template>
