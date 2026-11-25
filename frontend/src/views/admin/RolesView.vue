<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { roleApi, type PermissionItem, type RoleItem } from '@/api/system'
import { extractErrorMessage } from '@/api/client'

const message = useMessage()
const loading = ref(false)
const roles = ref<RoleItem[]>([])
const permissions = ref<PermissionItem[]>([])

const modalOpen = ref(false)
const editing = ref<RoleItem | null>(null)
const form = reactive({
  code: '',
  name: '',
  description: '',
  permissions: [] as string[],
})

async function loadData() {
  loading.value = true
  try {
    const [rRes, pRes] = await Promise.all([roleApi.list(), roleApi.permissions()])
    roles.value = rRes.data
    permissions.value = pRes.data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载角色失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  form.code = ''
  form.name = ''
  form.description = ''
  form.permissions = ['asset:read']
  modalOpen.value = true
}

function openEdit(row: RoleItem) {
  editing.value = row
  form.code = row.code
  form.name = row.name
  form.description = row.description || ''
  form.permissions = [...row.permissions]
  modalOpen.value = true
}

async function handleSave() {
  try {
    if (editing.value) {
      await roleApi.update(editing.value.id, {
        name: form.name,
        description: form.description,
        permissions: editing.value.code === 'super_admin' ? undefined : form.permissions,
      })
      message.success('角色已更新')
    } else {
      await roleApi.create({
        code: form.code,
        name: form.name,
        description: form.description,
        permissions: form.permissions,
      })
      message.success('角色创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await roleApi.remove(id)
    message.success('角色已删除')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

const columns: DataTableColumns<RoleItem> = [
  { title: '角色名称', key: 'name', width: 150 },
  {
    title: '编码',
    key: 'code',
    width: 150,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NTag, { size: 'small' }, { default: () => r.code }),
        r.is_builtin ? h(NTag, { size: 'small', type: 'warning' }, { default: () => '内置' }) : null,
      ]),
  },
  {
    title: '权限点',
    key: 'permissions',
    render: (r) =>
      h(
        NSpace,
        { size: 'small', wrap: true },
        () => r.permissions.map((p) => h(NTag, { size: 'tiny', type: 'info' }, { default: () => p })),
      ),
  },
  { title: '说明', key: 'description', width: 220, ellipsis: { tooltip: true } },
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
                default: () => `确认删除自定义角色「${r.name}」？`,
              },
            )
          : null,
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="角色与权限矩阵">
    <template #header-extra>
      <n-button type="primary" @click="openCreate">新建自定义角色</n-button>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="roles" :bordered="false" />

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑角色' : '新建角色'" class="w-560px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="角色编码" required>
          <n-input v-model:value="form.code" :disabled="Boolean(editing)" placeholder="如：network_auditor" />
        </n-form-item>
        <n-form-item label="角色名称" required>
          <n-input v-model:value="form.name" placeholder="如：网络审计员" />
        </n-form-item>
        <n-form-item label="说明">
          <n-input v-model:value="form.description" type="textarea" :rows="2" />
        </n-form-item>
        <n-form-item label="权限点">
          <n-checkbox-group v-model:value="form.permissions" :disabled="editing?.code === 'super_admin'">
            <div class="grid grid-cols-2 gap-2">
              <n-checkbox v-for="p in permissions" :key="p.code" :value="p.code" :label="`${p.label} (${p.code})`" />
            </div>
          </n-checkbox-group>
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
