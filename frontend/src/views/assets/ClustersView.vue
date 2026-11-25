<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NSpace, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import {
  clusterApi,
  deviceApi,
  type ClusterItem,
  type ClusterMemberItem,
  type DeviceItem,
} from '@/api/assets'
import { roleApi, userApi, type RoleItem, type UserItem } from '@/api/system'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { useOptionsStore } from '@/stores/options'
import AssetDetailDrawer from '@/components/AssetDetailDrawer.vue'
import BatchActionBar from '@/components/BatchActionBar.vue'

const auth = useAuthStore()
const options = useOptionsStore()
const message = useMessage()

const loading = ref(false)
const items = ref<ClusterItem[]>([])
const selectedIds = ref<number[]>([])
const total = ref(0)
const filters = reactive({ q: '', env: null as string | null, group_id: null as number | null, page: 1 })

const modalOpen = ref(false)
const editing = ref<ClusterItem | null>(null)
const form = reactive({
  name: '',
  code: '',
  cluster_type: '',
  group_id: null as number | null,
  network_zone_id: null as number | null,
  env: null as string | null,
  status: 'in_use',
  owner: '',
  description: '',
  tag_ids: [] as number[],
})

const attachModalOpen = ref(false)
const attachCluster = ref<ClusterItem | null>(null)
const allDevices = ref<DeviceItem[]>([])
const attachForm = reactive({ device_id: null as number | null, node_role: '' })

const drawerOpen = ref(false)
const inspectId = ref<number | null>(null)

// Cluster ACL member management drawer
const memberDrawerOpen = ref(false)
const memberLoading = ref(false)
const activeCluster = ref<ClusterItem | null>(null)
const members = ref<ClusterMemberItem[]>([])
const allUsers = ref<UserItem[]>([])
const allRoles = ref<RoleItem[]>([])
const memberForm = reactive({ user_id: null as number | null, role_id: null as number | null })

async function loadData() {
  loading.value = true
  try {
    await options.ensureLoaded()
    const res = await clusterApi.list({
      q: filters.q || undefined,
      env: filters.env || undefined,
      group_id: filters.group_id || undefined,
      page: filters.page,
      page_size: 20,
    })
    items.value = res.data.items
    total.value = res.data.total
  } catch (err) {
    message.error(extractErrorMessage(err, '加载集群失败'))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  Object.assign(form, {
    name: '',
    code: '',
    cluster_type: '',
    group_id: null,
    network_zone_id: options.zones.find((z) => z.is_default)?.id ?? null,
    env: null,
    status: 'in_use',
    owner: '',
    description: '',
    tag_ids: [],
  })
  modalOpen.value = true
}

function openEdit(row: ClusterItem) {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    code: row.code || '',
    cluster_type: row.cluster_type || '',
    group_id: row.group_id,
    network_zone_id: row.network_zone_id,
    env: row.env,
    status: row.status,
    owner: row.owner || '',
    description: row.description || '',
    tag_ids: row.tags.map((t) => t.id),
  })
  modalOpen.value = true
}

async function handleSave() {
  const payload = {
    name: form.name,
    code: form.code || null,
    cluster_type: form.cluster_type || null,
    group_id: form.group_id,
    network_zone_id: form.network_zone_id,
    env: form.env,
    status: form.status,
    owner: form.owner || null,
    description: form.description || null,
    tag_ids: form.tag_ids,
  }
  try {
    if (editing.value) {
      await clusterApi.update(editing.value.id, payload)
      message.success('集群已更新')
    } else {
      await clusterApi.create(payload)
      message.success('集群创建成功')
    }
    modalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '保存失败'))
  }
}

async function handleDelete(id: number) {
  try {
    await clusterApi.remove(id)
    message.success('集群已移入回收站')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '删除失败'))
  }
}

async function openAttachDevice(row: ClusterItem) {
  attachCluster.value = row
  attachForm.device_id = null
  attachForm.node_role = ''
  attachModalOpen.value = true
  try {
    allDevices.value = (await deviceApi.list({ page: 1, page_size: 200 })).data.items
  } catch {
    allDevices.value = []
  }
}

async function submitAttachDevice() {
  if (!attachCluster.value || !attachForm.device_id) {
    message.warning('请选择设备')
    return
  }
  try {
    await clusterApi.attachDevice(attachCluster.value.id, {
      device_id: attachForm.device_id,
      node_role: attachForm.node_role || null,
    })
    message.success('已挂载设备到集群')
    attachModalOpen.value = false
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '挂载失败'))
  }
}

async function openMembers(row: ClusterItem) {
  activeCluster.value = row
  memberDrawerOpen.value = true
  memberLoading.value = true
  memberForm.user_id = null
  memberForm.role_id = null
  try {
    const [mRes, uRes, rRes] = await Promise.all([
      clusterApi.listMembers(row.id),
      userApi.list({ page: 1, page_size: 200 }),
      roleApi.list(),
    ])
    members.value = mRes.data
    allUsers.value = uRes.data.items
    allRoles.value = rRes.data.filter((r) => r.code !== 'super_admin')
    const viewer = allRoles.value.find((r) => r.code === 'viewer')
    memberForm.role_id = viewer?.id ?? allRoles.value[0]?.id ?? null
  } catch (err) {
    message.error(extractErrorMessage(err, '加载集群访问权限失败'))
  } finally {
    memberLoading.value = false
  }
}

async function handleAddMember() {
  if (!activeCluster.value || !memberForm.user_id || !memberForm.role_id) {
    message.warning('请选择用户和角色')
    return
  }
  try {
    await clusterApi.addMember(activeCluster.value.id, {
      user_id: memberForm.user_id,
      role_id: memberForm.role_id,
    })
    message.success('已授予集群访问权限')
    memberForm.user_id = null
    members.value = (await clusterApi.listMembers(activeCluster.value.id)).data
  } catch (err) {
    message.error(extractErrorMessage(err, '授权失败'))
  }
}

async function handleRemoveMember(aclId: number) {
  if (!activeCluster.value) return
  try {
    await clusterApi.removeMember(activeCluster.value.id, aclId)
    message.success('已移除集群访问权限')
    members.value = (await clusterApi.listMembers(activeCluster.value.id)).data
  } catch (err) {
    message.error(extractErrorMessage(err, '移除权限失败'))
  }
}

const columns: DataTableColumns<ClusterItem> = [
  { type: 'selection' },
  {
    title: '集群名称',
    key: 'name',
    render: (r) =>
      h(
        NButton,
        { text: true, type: 'primary', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } },
        { default: () => r.name },
      ),
  },
  { title: '类型', key: 'cluster_type', width: 120, render: (r) => r.cluster_type || '—' },
  {
    title: '环境',
    key: 'env',
    width: 90,
    render: (r) => (r.env ? h(NTag, { size: 'small' }, { default: () => options.dictLabel('env', r.env) }) : '—'),
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (r) => h(NTag, { size: 'small', type: r.status === 'in_use' ? 'success' : 'default' }, { default: () => options.dictLabel('asset_status', r.status) }),
  },
  { title: '设备数', key: 'device_count', width: 80 },
  { title: '服务数', key: 'service_count', width: 80 },
  { title: '负责人', key: 'owner', width: 100, render: (r) => r.owner || '—' },
  { title: '归属分组', key: 'group_name', width: 120, render: (r) => r.group_name || '公共' },
  {
    title: '操作',
    key: 'actions',
    width: 280,
    render: (r) =>
      h(NSpace, { size: 'small' }, () => [
        h(NButton, { size: 'tiny', onClick: () => { inspectId.value = r.id; drawerOpen.value = true } }, { default: () => '全景' }),
        auth.canManageCluster(r.id, r.group_id)
          ? h(NButton, { size: 'tiny', type: 'success', secondary: true, onClick: () => openMembers(r) }, { default: () => '访问权限' })
          : null,
        h(NButton, { size: 'tiny', onClick: () => openAttachDevice(r) }, { default: () => '加设备' }),
        h(NButton, { size: 'tiny', onClick: () => openEdit(r) }, { default: () => '编辑' }),
        h(
          NPopconfirm,
          { onPositiveClick: () => handleDelete(r.id) },
          {
            trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '删除' }),
            default: () => `确认删除集群「${r.name}」？`,
          },
        ),
      ]),
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="集群管理">
    <template #header-extra>
      <n-space>
        <n-input v-model:value="filters.q" placeholder="搜索集群名/编码/负责人" clearable class="w-190px" @keyup.enter="loadData" />
        <n-select v-model:value="filters.env" clearable placeholder="环境" class="w-110px" :options="options.dictOptions('env')" />
        <n-select v-model:value="filters.group_id" clearable placeholder="运维分组" class="w-140px" :options="options.groups.map((g) => ({ label: g.name, value: g.id }))" />
        <n-button @click="() => { filters.page = 1; loadData() }">查询</n-button>
        <n-button type="primary" @click="openCreate">新建集群</n-button>
      </n-space>
    </template>

    <BatchActionBar
      entity="clusters"
      :selected-ids="selectedIds"
      @clear="selectedIds = []"
      @done="loadData"
    />

    <n-data-table
      :loading="loading"
      :columns="columns"
      :data="items"
      :row-key="(r: ClusterItem) => r.id"
      :checked-row-keys="selectedIds"
      @update:checked-row-keys="(keys: Array<string | number>) => { selectedIds = keys.map(Number) }"
      :bordered="false"
    />
    <div class="flex justify-end mt-4">
      <n-pagination v-model:page="filters.page" :page-size="20" :item-count="total" @update:page="loadData" />
    </div>

    <n-modal v-model:show="modalOpen" preset="card" :title="editing ? '编辑集群' : '新建集群'" class="w-520px">
      <n-form label-placement="left" label-width="85">
        <n-form-item label="集群名称" required>
          <n-input v-model:value="form.name" placeholder="如：生产核心 K8s 集群" />
        </n-form-item>
        <n-form-item label="编码">
          <n-input v-model:value="form.code" placeholder="可选" />
        </n-form-item>
        <n-form-item label="集群类型">
          <n-input v-model:value="form.cluster_type" placeholder="如：K8s / MySQL / Redis / 物理集群" />
        </n-form-item>
        <n-form-item label="归属分组">
          <n-select v-model:value="form.group_id" clearable :options="options.groups.map((g) => ({ label: g.name, value: g.id }))" />
        </n-form-item>
        <n-form-item label="网络域">
          <n-select v-model:value="form.network_zone_id" clearable :options="options.zones.map((z) => ({ label: z.name, value: z.id }))" />
        </n-form-item>
        <n-form-item label="环境">
          <n-select v-model:value="form.env" clearable :options="options.dictOptions('env')" />
        </n-form-item>
        <n-form-item label="状态">
          <n-select v-model:value="form.status" :options="options.dictOptions('asset_status')" />
        </n-form-item>
        <n-form-item label="负责人">
          <n-input v-model:value="form.owner" />
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

    <n-modal v-model:show="attachModalOpen" preset="card" :title="`关联设备 · ${attachCluster?.name || ''}`" class="w-440px">
      <n-form label-placement="left" label-width="80">
        <n-form-item label="选择设备" required>
          <n-select
            v-model:value="attachForm.device_id"
            filterable
            :options="allDevices.map((d) => ({ label: `${d.hostname} (${d.primary_ips[0] || d.device_type})`, value: d.id }))"
          />
        </n-form-item>
        <n-form-item label="节点角色">
          <n-input v-model:value="attachForm.node_role" placeholder="如：master / worker / primary" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="attachModalOpen = false">取消</n-button>
          <n-button type="primary" @click="submitAttachDevice">确定</n-button>
        </div>
      </template>
    </n-modal>

    <n-drawer v-model:show="memberDrawerOpen" width="600">
      <n-drawer-content :title="`集群访问权限 (ACL) · ${activeCluster?.name || ''}`">
        <div class="text-xs text-gray-500 mb-3">
          授予用户此集群的访问角色后，该用户将自动穿透访问此集群下挂载的设备、网卡、IP/VIP、服务及 URL（与运维分组权限取并集）。
        </div>
        <div class="flex gap-2 mb-4">
          <n-select
            v-model:value="memberForm.user_id"
            filterable
            placeholder="选择用户"
            :options="allUsers.map((u) => ({ label: `${u.display_name} (${u.username})`, value: u.id }))"
          />
          <n-select
            v-model:value="memberForm.role_id"
            placeholder="选择角色"
            class="w-160px"
            :options="allRoles.map((r) => ({ label: r.name, value: r.id }))"
          />
          <n-button type="primary" @click="handleAddMember">授权访问</n-button>
        </div>
        <n-data-table
          :loading="memberLoading"
          size="small"
          :data="members"
          :columns="[
            { title: '用户名', key: 'username', width: 120 },
            { title: '显示名', key: 'display_name', width: 120 },
            {
              title: '集群内角色',
              key: 'role_name',
              render: (m: ClusterMemberItem) => h(NTag, { size: 'small', type: 'success' }, { default: () => m.role_name }),
            },
            {
              title: '操作',
              key: 'op',
              width: 80,
              render: (m: ClusterMemberItem) =>
                h(
                  NPopconfirm,
                  { onPositiveClick: () => handleRemoveMember(m.acl_id) },
                  {
                    trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '移除' }),
                    default: () => `确认移除用户「${m.username}」对该集群的访问权限？`,
                  },
                ),
            },
          ]"
        />
      </n-drawer-content>
    </n-drawer>

    <AssetDetailDrawer v-model:show="drawerOpen" entity-type="cluster" :entity-id="inspectId" />
  </n-card>
</template>
