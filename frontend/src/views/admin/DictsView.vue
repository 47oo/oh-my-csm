<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { dictApi, type DictItem } from '@/api/system'
import { extractErrorMessage } from '@/api/client'

const message = useMessage()
const loading = ref(false)
const items = ref<DictItem[]>([])
const activeCategory = ref('device_type')

const categories = [
  { key: 'device_type', label: '设备类型' },
  { key: 'env', label: '环境' },
  { key: 'asset_status', label: '资产状态' },
  { key: 'nic_type', label: '网卡类型' },
  { key: 'ip_status', label: 'IP 状态' },
  { key: 'service_type', label: '服务类型' },
  { key: 'protocol', label: '协议' },
]

const filteredItems = computed(() => items.value.filter((i) => i.category === activeCategory.value))

const modalOpen = ref(false)
const editing = ref<DictItem | null>(null)
const form = reactive({
  code: '',
  label: '',
  color: '#2080f0',
  sort_order: 10,
  is_enabled: true,
  description: '',
})

async function loadData() {
  loading.value = true
  try {
    items.value = (await dictApi.list()).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载字典失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.code = ''
  form.label = ''
  form.color = '#2080f0'
  form.sort_order = 50
  form.is_enabled = true
  form.description = ''
  modalOpen.value = true
}

function openEdit(row: DictItem) {
  editing.value = row
  form.code = row.code
  form.label = row.label
  form.color = row.color || '#2080f0'
  form.sort_order = row.sort_order
  form.is_enabled = row.is_enabled
  form.description = row.description || ''
  modalOpen.value = true
}

async function handleSave() {
  try {
    if (editing.value) {
      await dictApi.update(editing.value.id, {
        label: form.label,
        color: form.color,
        sort_order: form.sort_order,
        is_enabled: form.is_enabled,
        description: form.description || null,
      })
      message.success('字典项已更新')
    } else {
      await dictApi.create({
        category: activeCategory.value,
        code: form.code,
        label: form.label,
        color: form.color,
        sort_order: form.sort_order,
        is_enabled: form.is_enabled,
        description: form.description || null,
      })
      message.success('字典项创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await dictApi.remove(id)
    message.success('已删除字典项')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

const columns: DataTableColumns<DictItem> = [
  { title: '编码', key: 'code', width: 150 },
  {
    title: '展示名称',
    key: 'label',
    render: (r) =>
      h(
        NTag,
        {
          size: 'small',
          color: r.color ? { color: `${r.color}1a`, textColor: r.color, borderColor: `${r.color}55` } : undefined,
        },
        { default: () => r.label },
      ),
  },
  { title: '排序', key: 'sort_order', width: 80 },
  {
    title: '状态',
    key: 'is_enabled',
    width: 100,
    render: (r) => h(NTag, { size: 'small', type: r.is_enabled ? 'success' : 'default' }, { default: () => (r.is_enabled ? '启用' : '停用') }),
  },
  {
    title: '来源',
    key: 'is_builtin',
    width: 90,
    render: (r) => (r.is_builtin ? '内置' : '自定义'),
  },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        !r.is_builtin
          ? h(
              NPopconfirm,
              { onPositiveClick: () => handleDelete(r.id) },
              {
                trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
                default: () => `确认删除字典项「${r.label}」？`,
              },
            )
          : null,
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="数据字典配置">
    <template #header-extra>
      <n-button type="primary" @click="openCreate">新增字典项</n-button>
    </template>

    <n-tabs v-model:value="activeCategory" type="line" class="mb-4">
      <n-tab v-for="c in categories" :key="c.key" :name="c.key" :tab="c.label" />
    </n-tabs>

    <n-data-table :loading="loading" :columns="columns" :data="filteredItems" :bordered="false" />

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑字典项' : '新增字典项'" class="w-440px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="编码" required>
          <n-input v-model:value="form.code" :disabled="Boolean(editing)" />
        </n-form-item>
        <n-form-item label="展示名称" required>
          <n-input v-model:value="form.label" />
        </n-form-item>
        <n-form-item label="标签色">
          <n-color-picker v-model:value="form.color" :show-alpha="false" />
        </n-form-item>
        <n-form-item label="排序">
          <n-input-number v-model:value="form.sort_order" class="w-full" />
        </n-form-item>
        <n-form-item label="启用">
          <n-switch v-model:value="form.is_enabled" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="modalOpen = false">取消</n-button>
          <n-button type="primary" @click="handleSave">保存</n-button>
        </div>
      </template>
    </n-modal>
  </n-card>
</template>
