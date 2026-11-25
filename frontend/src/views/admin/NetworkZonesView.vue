<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { zoneApi, type NetworkZoneItem } from '@/api/system'
import { extractErrorMessage } from '@/api/client'

const message = useMessage()
const loading = ref(false)
const zones = ref<NetworkZoneItem[]>([])

const modalOpen = ref(false)
const editing = ref<NetworkZoneItem | null>(null)
const form = reactive({
  name: '',
  code: '',
  location: '',
  is_default: false,
  description: '',
})

async function loadData() {
  loading.value = true
  try {
    zones.value = (await zoneApi.list()).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载网络域失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.name = ''
  form.code = ''
  form.location = ''
  form.is_default = false
  form.description = ''
  modalOpen.value = true
}

function openEdit(z: NetworkZoneItem) {
  editing.value = z
  form.name = z.name
  form.code = z.code
  form.location = z.location || ''
  form.is_default = z.is_default
  form.description = z.description || ''
  modalOpen.value = true
}

async function handleSave() {
  try {
    if (editing.value) {
      await zoneApi.update(editing.value.id, {
        name: form.name,
        location: form.location || null,
        is_default: form.is_default,
        description: form.description || null,
      })
      message.success('网络域已更新')
    } else {
      await zoneApi.create({
        name: form.name,
        code: form.code,
        location: form.location || null,
        is_default: form.is_default,
        description: form.description || null,
      })
      message.success('网络域创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await zoneApi.remove(id)
    message.success('网络域已删除')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

const columns: DataTableColumns<NetworkZoneItem> = [
  { title: '名称', key: 'name' },
  {
    title: '编码',
    key: 'code',
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NTag, { size: 'small' }, { default: () => r.code }),
        r.is_default ? h(NTag, { size: 'small', type: 'success' }, { default: () => '默认域' }) : null,
      ]),
  },
  { title: '机房/位置', key: 'location' },
  { title: '说明', key: 'description', ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 140,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        !r.is_default
          ? h(
              NPopconfirm,
              { onPositiveClick: () => handleDelete(r.id) },
              {
                trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
                default: () => `确认删除网络域「${r.name}」？`,
              },
            )
          : null,
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="网络域（隔离网段 IP 唯一性作用域）">
    <template #header-extra>
      <n-button type="primary" @click="openCreate">新建网络域</n-button>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="zones" :bordered="false" />

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑网络域' : '新建网络域'" class="w-460px">
      <n-form label-placement="left" label-width="85">
        <n-form-item label="名称" required>
          <n-input v-model:value="form.name" placeholder="如：上海隔离测试区" />
        </n-form-item>
        <n-form-item label="编码" required>
          <n-input v-model:value="form.code" :disabled="Boolean(editing)" placeholder="如：sh-dmz" />
        </n-form-item>
        <n-form-item label="位置/机房">
          <n-input v-model:value="form.location" />
        </n-form-item>
        <n-form-item label="设为默认域">
          <n-switch v-model:value="form.is_default" :disabled="editing?.is_default" />
        </n-form-item>
        <n-form-item label="描述">
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
