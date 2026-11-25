<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import {
  groupApi,
  roleApi,
  userApi,
  type GroupMemberItem,
  type OpsGroupItem,
  type RoleItem,
  type UserItem,
} from '@/api/system'
import { useAuthStore } from '@/stores/auth'
import { extractErrorMessage } from '@/api/client'

const auth = useAuthStore()
const message = useMessage()

const loading = ref(false)
const groups = ref<OpsGroupItem[]>([])
const keyword = ref('')

const modalOpen = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({ name: '', code: '', description: '' })

const memberDrawerOpen = ref(false)
const activeGroup = ref<OpsGroupItem | null>(null)
const members = ref<GroupMemberItem[]>([])
const allUsers = ref<UserItem[]>([])
const allRoles = ref<RoleItem[]>([])
const newMember = reactive<{ user_id: number | null; role_id: number | null }>({
  user_id: null,
  role_id: null,
})

async function loadGroups() {
  loading.value = true
  try {
    const res = await groupApi.list(keyword.value || undefined)
    groups.value = res.data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载分组失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.name = ''
  form.code = ''
  form.description = ''
  modalOpen.value = true
}

function openEdit(row: OpsGroupItem) {
  editingId.value = row.id
  form.name = row.name
  form.code = row.code
  form.description = row.description || ''
  modalOpen.value = true
}

async function handleSave() {
  try {
    if (editingId.value) {
      await groupApi.update(editingId.value, { name: form.name, description: form.description })
      message.success('分组已更新')
    } else {
      await groupApi.create({ name: form.name, code: form.code, description: form.description })
      message.success('分组创建成功')
    }
    modalOpen.value = false
    await loadGroups()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await groupApi.remove(id)
    message.success('已删除分组')
    await loadGroups()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

async function openMembers(group: OpsGroupItem) {
  activeGroup.value = group
  memberDrawerOpen.value = true
  newMember.user_id = null
  newMember.role_id = null
  try {
    const [mRes, rRes] = await Promise.all([groupApi.listMembers(group.id), roleApi.list()])
    members.value = mRes.data
    allRoles.value = rRes.data.filter((r) => auth.isSuperAdmin || r.code !== 'super_admin')
    if (auth.canManageGroup(group.id)) {
      const uRes = await userApi.list({ page: 1, page_size: 200 })
      allUsers.value = uRes.data.items
    }
  } catch (err) {
    message.error(extractErrorMessage(err, '加载成员失败'))
  }
}

async function handleAddMember() {
  if (!activeGroup.value || !newMember.user_id || !newMember.role_id) {
    message.warning('请选择用户和角色')
    return
  }
  try {
    await groupApi.addMember(activeGroup.value.id, {
      user_id: newMember.user_id,
      role_id: newMember.role_id,
    })
    message.success('成员权限已添加')
    await openMembers(activeGroup.value)
    await loadGroups()
  } catch (err) {
    message.error(extractErrorMessage(err, '添加成员失败'))
  }
}

async function handleRemoveMember(assignmentId: number) {
  if (!activeGroup.value) return
  try {
    await groupApi.removeMember(activeGroup.value.id, assignmentId)
    message.success('已移除成员权限')
    await openMembers(activeGroup.value)
    await loadGroups()
  } catch (err) {
    message.error(extractErrorMessage(err, '移除失败'))
  }
}

const columns: DataTableColumns<OpsGroupItem> = [
  { title: 'ID', key: 'id', width: 70 },
  { title: '分组名称', key: 'name' },
  { title: '分组编码', key: 'code', render: (r) => h(NTag, { size: 'small' }, { default: () => r.code }) },
  { title: '成员数', key: 'member_count', width: 90 },
  { title: '说明', key: 'description', ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'actions',
    width: 240,
    render: (row) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => openMembers(row) }, { default: () => '成员' }),
        auth.canManageGroup(row.id)
          ? h(NButton, { size: 'tiny', onClick: () => openEdit(row) }, { default: () => '编辑' })
          : null,
        auth.hasGlobalPerm('system:manage')
          ? h(
              NPopconfirm,
              { onPositiveClick: () => handleDelete(row.id) },
              {
                trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
                default: () => `确认删除分组「${row.name}」？`,
              },
            )
          : null,
      ]),
  },
]

onMounted(loadGroups)
</script>

<template>
  <n-card title="运维分组（数据权限隔离维度）">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="keyword" placeholder="搜索分组名/编码" clearable @keyup.enter="loadGroups" />
        <n-button @click="loadGroups">查询</n-button>
        <n-button v-if="auth.hasGlobalPerm('system:manage')" type="primary" @click="openCreate">
          新建分组
        </n-button>
      </n-space>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="groups" :bordered="false" />

    <n-modal v-model:show="modalOpen" preset="card" :title="editingId ? '编辑分组' : '新建分组'" class="w-460px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="分组名称" required>
          <n-input v-model:value="form.name" placeholder="如：核心交易运维组" />
        </n-form-item>
        <n-form-item label="分组编码" required>
          <n-input v-model:value="form.code" :disabled="Boolean(editingId)" placeholder="如：core-trade" />
        </n-form-item>
        <n-form-item label="描述">
          <n-input v-model:value="form.description" type="textarea" :rows="3" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="modalOpen = false">取消</n-button>
          <n-button type="primary" @click="handleSave">保存</n-button>
        </div>
      </template>
    </n-modal>

    <n-drawer v-model:show="memberDrawerOpen" width="540">
      <n-drawer-content :title="`分组成员管理 · ${activeGroup?.name || ''}`">
        <div v-if="activeGroup && auth.canManageGroup(activeGroup.id)" class="flex gap-2 mb-4">
          <n-select
            v-model:value="newMember.user_id"
            filterable
            placeholder="选择用户"
            :options="allUsers.map((u) => ({ label: `${u.display_name} (${u.username})`, value: u.id }))"
          />
          <n-select
            v-model:value="newMember.role_id"
            placeholder="分配角色"
            :options="allRoles.map((r) => ({ label: r.name, value: r.id }))"
          />
          <n-button type="primary" @click="handleAddMember">添加</n-button>
        </div>

        <n-data-table
          size="small"
          :data="members"
          :columns="[
            { title: '用户名', key: 'username' },
            { title: '姓名', key: 'display_name' },
            { title: '角色', key: 'role_name', render: (r: GroupMemberItem) => h(NTag, { size: 'small', type: 'info' }, { default: () => r.role_name }) },
            {
              title: '操作',
              key: 'op',
              width: 80,
              render: (r: GroupMemberItem) =>
                activeGroup && auth.canManageGroup(activeGroup.id)
                  ? h(NButton, { size: 'tiny', type: 'error', quaternary: true, onClick: () => handleRemoveMember(r.assignment_id) }, { default: () => '移除' })
                  : null,
            },
          ]"
        />
      </n-drawer-content>
    </n-drawer>
  </n-card>
</template>
