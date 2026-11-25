<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { tagApi, type TagItem } from '@/api/system'
import { useAuthStore } from '@/stores/auth'
import { extractErrorMessage } from '@/api/client'

const auth = useAuthStore()
const message = useMessage()
const loading = ref(false)
const tags = ref<TagItem[]>([])
const keyword = ref('')

const modalOpen = ref(false)
const editing = ref<TagItem | null>(null)
const form = reactive({ name: '', color: '#2080f0', description: '' })

async function loadData() {
  loading.value = true
  try {
    tags.value = (await tagApi.list(keyword.value || undefined)).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载标签失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.name = ''
  form.color = '#2080f0'
  form.description = ''
  modalOpen.value = true
}

function openEdit(t: TagItem) {
  editing.value = t
  form.name = t.name
  form.color = t.color || '#2080f0'
  form.description = t.description || ''
  modalOpen.value = true
}

async function handleSave() {
  try {
    if (editing.value) {
      await tagApi.update(editing.value.id, {
        name: form.name,
        color: form.color,
        description: form.description || null,
      })
      message.success('标签已更新')
    } else {
      await tagApi.create({
        name: form.name,
        color: form.color,
        description: form.description || null,
      })
      message.success('标签创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await tagApi.remove(id)
    message.success('标签已删除')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

const columns: DataTableColumns<TagItem> = [
  {
    title: '标签',
    key: 'name',
    width: 180,
    render: (r) =>
      h(
        NTag,
        {
          size: 'small',
          color: r.color ? { color: `${r.color}1a`, textColor: r.color, borderColor: `${r.color}55` } : undefined,
        },
        { default: () => r.name },
      ),
  },
  { title: '说明', key: 'description' },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (r) =>
      auth.hasGlobalPerm('system:manage')
        ? h(NSpace, { size: 'small' }, () => [
            h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
            h(
              NPopconfirm,
              { onPositiveClick: () => handleDelete(r.id) },
              {
                trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
                default: () => `确认删除标签「${r.name}」？`,
              },
            ),
          ])
        : null,
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="资产标签管理">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="keyword" placeholder="搜索标签" clearable @keyup.enter="loadData" />
        <n-button @click="loadData">查询</n-button>
        <n-button
          v-if="auth.hasAnyPerm('asset:write') || auth.hasGlobalPerm('system:manage')"
          type="primary"
          @click="openCreate"
        >
          新建标签
        </n-button>
      </n-space>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="tags" :bordered="false" />

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑标签' : '新建标签'" class="w-420px">
      <n-form label-placement="left" label-width="70">
        <n-form-item label="名称" required>
          <n-input v-model:value="form.name" placeholder="如：核心链路 / 等保三级" />
        </n-form-item>
        <n-form-item label="颜色">
          <n-color-picker v-model:value="form.color" :show-alpha="false" />
        </n-form-item>
        <n-form-item label="说明">
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
  </n-card>
</template>
