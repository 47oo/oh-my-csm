<script setup lang="ts">
import { h, onMounted, reactive, ref } from 'vue'
import { NButton, NPopconfirm, NTag, useMessage, type DataTableColumns } from 'naive-ui'
import { authApi, type ApiTokenItem } from '@/api/system'
import { extractErrorMessage } from '@/api/client'

const message = useMessage()
const loading = ref(false)
const tokens = ref<ApiTokenItem[]>([])

const createModalOpen = ref(false)
const form = reactive<{ name: string; scope: 'read_only' | 'inherit'; expires_in_days: number | null }>({
  name: '',
  scope: 'read_only',
  expires_in_days: 90,
})

const createdSecret = ref<string | null>(null)

async function loadData() {
  loading.value = true
  try {
    tokens.value = (await authApi.listTokens()).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载 API Token 失败'))
  } finally {
    loading.value = false
  }
}

async function handleCreate() {
  if (!form.name.trim()) {
    message.warning('请输入 Token 名称')
    return
  }
  try {
    const res = await authApi.createToken({
      name: form.name.trim(),
      scope: form.scope,
      expires_in_days: form.expires_in_days,
    })
    createdSecret.value = res.data.token
    createModalOpen.value = false
    form.name = ''
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '创建 Token 失败'))
  }
}

async function copySecret() {
  if (!createdSecret.value) return
  await navigator.clipboard.writeText(createdSecret.value)
  message.success('已复制到剪贴板')
}

async function handleRevoke(id: number) {
  try {
    await authApi.revokeToken(id)
    message.success('Token 已吊销')
    await loadData()
  } catch (err) {
    message.error(extractErrorMessage(err, '吊销失败'))
  }
}

const columns: DataTableColumns<ApiTokenItem> = [
  { title: '名称', key: 'name' },
  { title: '前缀', key: 'token_prefix', width: 140, render: (r) => `${r.token_prefix}...` },
  {
    title: '权限范围',
    key: 'scope',
    width: 120,
    render: (r) =>
      h(
        NTag,
        { size: 'small', type: r.scope === 'read_only' ? 'info' : 'warning' },
        { default: () => (r.scope === 'read_only' ? '仅只读' : '继承本人权限') },
      ),
  },
  {
    title: '过期时间',
    key: 'expires_at',
    width: 180,
    render: (r) => (r.expires_at ? new Date(r.expires_at).toLocaleString() : '永不过期'),
  },
  {
    title: '最近使用',
    key: 'last_used_at',
    width: 180,
    render: (r) => (r.last_used_at ? new Date(r.last_used_at).toLocaleString() : '从未使用'),
  },
  {
    title: '状态',
    key: 'revoked_at',
    width: 90,
    render: (r) =>
      h(
        NTag,
        { size: 'small', type: r.revoked_at ? 'default' : 'success' },
        { default: () => (r.revoked_at ? '已吊销' : '有效') },
      ),
  },
  {
    title: '操作',
    key: 'op',
    width: 90,
    render: (r) =>
      !r.revoked_at
        ? h(
            NPopconfirm,
            { onPositiveClick: () => handleRevoke(r.id) },
            {
              trigger: () => h(NButton, { size: 'tiny', type: 'error', quaternary: true }, { default: () => '吊销' }),
              default: () => `确认吊销 Token「${r.name}」？`,
            },
          )
        : null,
  },
]

onMounted(loadData)
</script>

<template>
  <n-card title="个人 API Token (PAT)">
    <template #header-extra>
      <n-button type="primary" @click="createModalOpen = true">生成新 Token</n-button>
    </template>

    <n-alert type="info" class="mb-4">
      可在 Python / Shell / Ansible 脚本中使用 Header <code>Authorization: Bearer csm_xxx</code> 调用本系统 REST API（OpenAPI 文档位于 <code>/api/v1/docs</code>）。
    </n-alert>

    <n-data-table :loading="loading" :columns="columns" :data="tokens" :bordered="false" />

    <n-modal v-model:show="createModalOpen" preset="card" title="生成个人 API Token" class="w-440px">
      <n-form label-placement="left" label-width="90">
        <n-form-item label="名称" required>
          <n-input v-model:value="form.name" placeholder="如：ansible-inventory-sync" />
        </n-form-item>
        <n-form-item label="权限范围">
          <n-radio-group v-model:value="form.scope">
            <n-space>
              <n-radio value="read_only">仅只读（推荐）</n-radio>
              <n-radio value="inherit">继承当前账号权限</n-radio>
            </n-space>
          </n-radio-group>
        </n-form-item>
        <n-form-item label="有效天数">
          <n-input-number v-model:value="form.expires_in_days" :min="1" :max="3650" clearable placeholder="留空表示永不过期" class="w-full" />
        </n-form-item>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="createModalOpen = false">取消</n-button>
          <n-button type="primary" @click="handleCreate">生成</n-button>
        </div>
      </template>
    </n-modal>

    <n-modal :show="Boolean(createdSecret)" preset="card" title="请立即保存你的 API Token" class="w-520px" @update:show="(v: boolean) => { if (!v) createdSecret = null }">
      <n-alert type="warning" class="mb-3">
        Token 明文仅在创建时展示这一次，关闭后无法再次查看！
      </n-alert>
      <n-input :value="createdSecret || ''" readonly />
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button type="primary" @click="copySecret">复制 Token</n-button>
          <n-button @click="createdSecret = null">我已妥善保存</n-button>
        </div>
      </template>
    </n-modal>
  </n-card>
</template>
