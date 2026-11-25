<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import {
  groupApi,
  roleApi,
  userApi,
  type OpsGroupItem,
  type RoleItem,
  type UserItem,
} from '@/api/system'
import { clusterApi, type ClusterItem } from '@/api/assets'
import { useAuthStore } from '@/stores/auth'
import { extractErrorMessage } from '@/api/client'

const auth = useAuthStore()
const message = useMessage()

const loading = ref(false)
const users = ref<UserItem[]>([])
const total = ref(0)
const page = ref(1)
const keyword = ref('')

const roles = ref<RoleItem[]>([])
const groups = ref<OpsGroupItem[]>([])
const clusters = ref<ClusterItem[]>([])

const modalOpen = ref(false)
const editingUser = ref<UserItem | null>(null)
const form = reactive({
  username: '',
  display_name: '',
  email: '',
  password: '',
  is_active: true,
  allow_public_assets: false,
  must_change_password: true,
  assignments: [] as Array<{ group_id: number | null; role_id: number | null }>,
  cluster_assignments: [] as Array<{ cluster_id: number | null; role_id: number | null }>,
})

const resetModalOpen = ref(false)
const resetTarget = ref<UserItem | null>(null)
const resetPassword = ref('')

async function loadData() {
  loading.value = true
  try {
    const [uRes, rRes, gRes, cRes] = await Promise.all([
      userApi.list({ q: keyword.value || undefined, page: page.value, page_size: 20 }),
      roleApi.list(),
      groupApi.list(),
      clusterApi.list({ page: 1, page_size: 200 }).catch(() => ({ data: { items: [] as ClusterItem[] } })),
    ])
    users.value = uRes.data.items
    total.value = uRes.data.total
    roles.value = rRes.data
    groups.value = gRes.data
    clusters.value = cRes.data.items
  } catch (err) {
    message.error(extractErrorMessage(err, '加载用户失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingUser.value = null
  form.username = ''
  form.display_name = ''
  form.email = ''
  form.password = ''
  form.is_active = true
  form.allow_public_assets = false
  form.must_change_password = true
  form.assignments = []
  form.cluster_assignments = []
  modalOpen.value = true
}

function openEdit(u: UserItem) {
  editingUser.value = u
  form.username = u.username
  form.display_name = u.display_name
  form.email = u.email || ''
  form.password = ''
  form.is_active = u.is_active
  form.allow_public_assets = u.allow_public_assets
  form.must_change_password = u.must_change_password
  form.assignments = u.assignments.map((a) => ({ group_id: a.group_id, role_id: a.role_id }))
  form.cluster_assignments = (u.cluster_assignments || []).map((c) => ({
    cluster_id: c.cluster_id,
    role_id: c.role_id,
  }))
  modalOpen.value = true
}

function addAssignmentRow() {
  form.assignments.push({ group_id: null, role_id: roles.value[0]?.id ?? null })
}

function addClusterAssignmentRow() {
  const defaultRole = roles.value.find((r) => r.code === 'viewer') || roles.value.find((r) => r.code !== 'super_admin')
  form.cluster_assignments.push({
    cluster_id: clusters.value[0]?.id ?? null,
    role_id: defaultRole?.id ?? null,
  })
}

async function handleSave() {
  const validAssignments = form.assignments
    .filter((a) => a.role_id !== null)
    .map((a) => ({ group_id: a.group_id, role_id: a.role_id as number }))
  const validClusterAssignments = form.cluster_assignments
    .filter((c) => c.cluster_id !== null && c.role_id !== null)
    .map((c) => ({ cluster_id: c.cluster_id as number, role_id: c.role_id as number }))

  try {
    if (editingUser.value) {
      await userApi.update(editingUser.value.id, {
        display_name: form.display_name,
        email: form.email || null,
        is_active: form.is_active,
        allow_public_assets: form.allow_public_assets,
        must_change_password: form.must_change_password,
        assignments: validAssignments,
        cluster_assignments: validClusterAssignments,
      })
      message.success('用户已更新')
    } else {
      await userApi.create({
        username: form.username,
        display_name: form.display_name,
        email: form.email || null,
        password: form.password,
        is_active: form.is_active,
        allow_public_assets: form.allow_public_assets,
        must_change_password: form.must_change_password,
        assignments: validAssignments,
        cluster_assignments: validClusterAssignments,
      })
      message.success('用户创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存用户失败'))
  }
}

async function handleUnlock(u: UserItem) {
  try {
    await userApi.unlock(u.id)
    message.success('账号已解锁')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '解锁失败'))
  }
}

function openResetPassword(u: UserItem) {
  resetTarget.value = u
  resetPassword.value = ''
  resetModalOpen.value = true
}

async function handleResetPassword() {
  if (!resetTarget.value) return
  try {
    await userApi.resetPassword(resetTarget.value.id, resetPassword.value, true)
    message.success('密码已重置，下次登录将要求修改密码')
    resetModalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '重置密码失败'))
  }
}

async function handleDelete(u: UserItem) {
  try {
    await userApi.remove(u.id)
    message.success('用户已删除')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

const columns: DataTableColumns<UserItem> = [
  { title: '用户名', key: 'username', width: 130 },
  { title: '显示名', key: 'display_name', width: 120 },
  { title: '邮箱', key: 'email', width: 180 },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (u) => {
      const tags = [
        h(NTag, { size: 'small', type: u.is_active ? 'success' : 'default' }, { default: () => (u.is_active ? '启用' : '禁用') }),
      ]
      if (u.locked_until && new Date(u.locked_until) > new Date()) {
        tags.push(h(NTag, { size: 'small', type: 'error', class: 'ml-1' }, { default: () => '已锁定' }))
      }
      return h('div', tags)
    },
  },
  {
    title: '授权范围（分组 / 集群）',
    key: 'assignments',
    render: (u) =>
      h(
        NSpace,
        { size: 'small', wrap: true },
        () => [
          ...u.assignments.map((a) =>
            h(
              NTag,
              { size: 'small', type: a.group_id === null ? 'warning' : 'info' },
              { default: () => `${a.group_name || '全局'}: ${a.role_name}` },
            ),
          ),
          ...(u.cluster_assignments || []).map((c) =>
            h(
              NTag,
              { size: 'small', type: 'success' },
              { default: () => `集群:${c.cluster_name || `#${c.cluster_id}`} · ${c.role_name}` },
            ),
          ),
          u.allow_public_assets
            ? h(NTag, { size: 'small', bordered: false }, { default: () => '公共只读' })
            : null,
        ],
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 240,
    render: (u) =>
      auth.hasGlobalPerm('system:manage')
        ? h(NSpace, { size: 'small' }, () => [
            h(NButton, { size: 'tiny', onClick: () => openEdit(u) }, { default: () => '编辑' }),
            h(NButton, { size: 'tiny', onClick: () => openResetPassword(u) }, { default: () => '重置密码' }),
            u.locked_until && new Date(u.locked_until) > new Date()
              ? h(NButton, { size: 'tiny', type: 'warning', onClick: () => handleUnlock(u) }, { default: () => '解锁' })
              : null,
            u.id !== auth.user?.id
              ? h(
                  NPopconfirm,
                  { onPositiveClick: () => handleDelete(u) },
                  {
                    trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
                    default: () => `确认删除用户「${u.username}」？`,
                  },
                )
              : null,
          ])
        : null,
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="用户管理">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="keyword" placeholder="搜索用户名/姓名/邮箱" clearable @keyup.enter="loadData" />
        <n-button @click="loadData">查询</n-button>
        <n-button v-if="auth.hasGlobalPerm('system:manage')" type="primary" @click="openCreate">
          新建用户
        </n-button>
      </n-space>
    </template>

    <n-data-table :loading="loading" :columns="columns" :data="users" :bordered="false" />

    <n-modal v-model:show="modalOpen" preset="card" :title="editingUser ? '编辑用户' : '新建用户'" class="w-620px">
      <n-form label-placement="left" label-width="115">
        <n-form-item label="用户名" required>
          <n-input v-model:value="form.username" :disabled="Boolean(editingUser)" />
        </n-form-item>
        <n-form-item label="显示名称" required>
          <n-input v-model:value="form.display_name" />
        </n-form-item>
        <n-form-item label="邮箱">
          <n-input v-model:value="form.email" />
        </n-form-item>
        <n-form-item v-if="!editingUser" label="初始密码" required>
          <n-input v-model:value="form.password" type="password" show-password-on="click" placeholder="至少 8 位含字母数字" />
        </n-form-item>
        <n-form-item label="账号启用">
          <n-switch v-model:value="form.is_active" />
        </n-form-item>
        <n-form-item label="首次登录改密">
          <n-switch v-model:value="form.must_change_password" />
        </n-form-item>
        <n-form-item label="公共资产只读">
          <div class="flex items-center gap-2">
            <n-switch v-model:value="form.allow_public_assets" />
            <span class="text-xs text-gray-500">开启后可只读查看未归属任何分组的公共资产（默认严格隔离）</span>
          </div>
        </n-form-item>
        <n-form-item label="分组/全局角色">
          <div class="w-full space-y-2">
            <div v-for="(item, idx) in form.assignments" :key="`g-${idx}`" class="flex gap-2">
              <n-select
                v-model:value="item.group_id"
                clearable
                placeholder="全局（不选分组）"
                :options="groups.map((g) => ({ label: g.name, value: g.id }))"
              />
              <n-select
                v-model:value="item.role_id"
                placeholder="选择角色"
                :options="roles.map((r) => ({ label: r.name, value: r.id }))"
              />
              <n-button type="error" quaternary @click="form.assignments.splice(idx, 1)">删</n-button>
            </div>
            <n-button size="small" dashed block @click="addAssignmentRow">+ 添加分组/全局角色</n-button>
          </div>
        </n-form-item>
        <n-form-item label="集群级授权">
          <div class="w-full space-y-2">
            <div v-for="(item, idx) in form.cluster_assignments" :key="`c-${idx}`" class="flex gap-2">
              <n-select
                v-model:value="item.cluster_id"
                filterable
                placeholder="选择目标集群"
                :options="clusters.map((c) => ({ label: c.name, value: c.id }))"
              />
              <n-select
                v-model:value="item.role_id"
                placeholder="选择集群内角色"
                :options="roles.filter((r) => r.code !== 'super_admin').map((r) => ({ label: r.name, value: r.id }))"
              />
              <n-button type="error" quaternary @click="form.cluster_assignments.splice(idx, 1)">删</n-button>
            </div>
            <n-button size="small" dashed block @click="addClusterAssignmentRow">+ 添加集群访问权限</n-button>
            <div class="text-xs text-gray-400">
              集群授权与分组权限取并集，并自动穿透该集群下的设备、网卡、IP/VIP、服务与 URL。
            </div>
          </div>
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="modalOpen = false">取消</n-button>
          <n-button type="primary" @click="handleSave">保存</n-button>
        </div>
      </template>
    </n-modal>

    <n-modal v-model:show="resetModalOpen" preset="card" :title="`重置密码 · ${resetTarget?.username || ''}`" class="w-420px">
      <n-form-item label="新初始密码（至少 8 位含字母与数字）">
        <n-input v-model:value="resetPassword" type="password" show-password-on="click" />
      </n-form-item>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="resetModalOpen = false">取消</n-button>
          <n-button type="primary" @click="handleResetPassword">确认重置</n-button>
        </div>
      </template>
    </n-modal>
  </n-card>
</template>
