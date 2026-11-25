<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { auditApi, type AuditLogItem } from '@/api/system'
import { extractErrorMessage } from '@/api/client'

const message = useMessage()
const loading = ref(false)
const logs = ref<AuditLogItem[]>([])
const total = ref(0)
const filters = reactive({
  actor_username: '',
  action: '',
  target_type: '',
  page: 1,
  page_size: 20,
})

const diffModalOpen = ref(false)
const activeLog = ref<AuditLogItem | null>(null)

async function loadData() {
  loading.value = true
  try {
    const res = await auditApi.list({
      actor_username: filters.actor_username || undefined,
      action: filters.action || undefined,
      target_type: filters.target_type || undefined,
      page: filters.page,
      page_size: filters.page_size,
    })
    logs.value = res.data.items
    total.value = res.data.total
  } catch (err) {
    message.error(extractErrorMessage(err, '加载审计日志失败'))
  } finally {
    loading.value = false
  }
}

function showDiff(row: AuditLogItem) {
  activeLog.value = row
  diffModalOpen.value = true
}

const columns: DataTableColumns<AuditLogItem> = [
  { title: '时间', key: 'created_at', width: 185, render: (r) => new Date(r.created_at).toLocaleString() },
  { title: '操作人', key: 'actor_username', width: 120 },
  {
    title: '认证方式',
    key: 'auth_method',
    width: 90,
    render: (r) => h(NTag, { size: 'tiny', type: r.auth_method === 'pat' ? 'warning' : 'default' }, { default: () => r.auth_method }),
  },
  { title: '动作', key: 'action', width: 120 },
  { title: '对象类型', key: 'target_type', width: 120 },
  { title: '对象标识', key: 'target_label', ellipsis: { tooltip: true } },
  { title: '来源 IP', key: 'client_ip', width: 130 },
  {
    title: '变更详情',
    key: 'diff',
    width: 100,
    render: (r) =>
      r.diff_before || r.diff_after
        ? h(NButton, { size: 'tiny', quaternary: true, type: 'primary', onClick: () => showDiff(r) }, { default: () => '查看 Diff' })
        : '—',
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="操作审计日志（字段级 Diff 追溯）">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.actor_username" placeholder="操作人" clearable class="w-140px" />
        <n-input v-model:value="filters.action" placeholder="动作 (create/update...)" clearable class="w-160px" />
        <n-input v-model:value="filters.target_type" placeholder="目标类型" clearable class="w-140px" />
        <n-button type="primary" @click="() => { filters.page = 1; loadData() }">筛选</n-button>
      </n-space>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="logs" :bordered="false" />

    <div class="flex justify-end mt-4">
      <n-pagination
        v-model:page="filters.page"
        :page-size="filters.page_size"
        :item-count="total"
        @update:page="loadData"
      />
    </div>

    <n-modal v-model:show="diffModalOpen" preset="card" :title="`字段变更 Diff · ${activeLog?.target_label || ''}`" class="w-680px">
      <div class="grid grid-cols-2 gap-4">
        <div>
          <div class="text-xs font-semibold text-gray-500 mb-2">变更前 (Before)</div>
          <pre class="bg-gray-50 p-3 rounded text-xs overflow-auto max-h-360px">{{ JSON.stringify(activeLog?.diff_before, null, 2) || '无' }}</pre>
        </div>
        <div>
          <div class="text-xs font-semibold text-gray-500 mb-2">变更后 (After)</div>
          <pre class="bg-blue-50 p-3 rounded text-xs overflow-auto max-h-360px">{{ JSON.stringify(activeLog?.diff_after, null, 2) || '无' }}</pre>
        </div>
      </div>
    </n-modal>
  </n-card>
</template>
