<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useMessage } from 'naive-ui'
import { batchApi, type BatchAction, type BatchEntity } from '@/api/m3'
import { useOptionsStore } from '@/stores/options'
import { extractErrorMessage } from '@/api/client'

const props = defineProps<{
  entity: BatchEntity
  selectedIds: number[]
  allowMoveGroup?: boolean
  allowEnv?: boolean
  statusDictCategory?: string
}>()

const emit = defineEmits<{
  (e: 'done'): void
  (e: 'clear'): void
}>()

const options = useOptionsStore()
const message = useMessage()

const modalOpen = ref(false)
const currentAction = ref<BatchAction>('tag_add')
const submitting = ref(false)
const form = reactive({
  tag_ids: [] as number[],
  group_id: null as number | null,
  status: null as string | null,
  env: null as string | null,
})

function openModal(action: BatchAction) {
  currentAction.value = action
  form.tag_ids = []
  form.group_id = null
  form.status = null
  form.env = null
  modalOpen.value = true
}

async function runAction(action: BatchAction) {
  if (!props.selectedIds.length) return
  submitting.value = true
  try {
    const res = await batchApi.execute(props.entity, {
      ids: props.selectedIds,
      action,
      tag_ids: action === 'tag_add' || action === 'tag_remove' ? form.tag_ids : undefined,
      group_id: action === 'move_group' ? form.group_id : undefined,
      status: action === 'update_status' ? form.status : undefined,
      env: action === 'update_status' ? form.env : undefined,
    })
    const okCount = res.data.succeeded_ids.length
    const failCount = res.data.failed.length
    if (failCount === 0) {
      message.success(`批量操作成功（共处理 ${okCount} 条）`)
    } else {
      const reasons = res.data.failed
        .slice(0, 3)
        .map((f) => `${f.label}: ${f.reason}`)
        .join('；')
      message.warning(`成功 ${okCount} 条，失败 ${failCount} 条（${reasons}）`, { duration: 6000 })
    }
    modalOpen.value = false
    emit('clear')
    emit('done')
  } catch (err) {
    message.error(extractErrorMessage(err, '批量操作失败'))
  } finally {
    submitting.value = false
  }
}

const actionTitles: Record<BatchAction, string> = {
  tag_add: '批量打标签',
  tag_remove: '批量移除标签',
  move_group: '批量迁移归属分组',
  update_status: '批量修改状态 / 环境',
  delete: '批量软删除',
}
</script>

<template>
  <div
    v-if="selectedIds.length > 0"
    class="mb-3 px-3 py-2 rounded bg-blue-50 border border-blue-200 flex flex-wrap items-center justify-between gap-2"
  >
    <div class="text-sm text-blue-800">
      已选中 <span class="font-bold">{{ selectedIds.length }}</span> 条记录
      <n-button text size="tiny" class="ml-2" @click="emit('clear')">取消选择</n-button>
    </div>

    <n-space size="small">
      <n-button size="tiny" secondary type="primary" @click="openModal('tag_add')">批量打标签</n-button>
      <n-button size="tiny" secondary @click="openModal('tag_remove')">移标签</n-button>
      <n-button v-if="allowMoveGroup !== false" size="tiny" secondary type="info" @click="openModal('move_group')">
        迁分组
      </n-button>
      <n-button size="tiny" secondary type="warning" @click="openModal('update_status')">改状态</n-button>
      <n-popconfirm @positive-click="runAction('delete')">
        <template #trigger>
          <n-button size="tiny" type="error" secondary :loading="submitting">批量软删除</n-button>
        </template>
        确认将选中的 {{ selectedIds.length }} 条记录移入回收站？（仍有关联引用的记录会自动跳过并提示原因）
      </n-popconfirm>
    </n-space>

    <n-modal v-model:show="modalOpen" preset="card" :title="`${actionTitles[currentAction]}（${selectedIds.length} 条）`" class="w-440px">
      <n-form label-placement="left" label-width="80">
        <n-form-item v-if="currentAction === 'tag_add' || currentAction === 'tag_remove'" label="选择标签" required>
          <n-select
            v-model:value="form.tag_ids"
            multiple
            filterable
            :options="options.tags.map((t) => ({ label: t.name, value: t.id }))"
          />
        </n-form-item>

        <n-form-item v-if="currentAction === 'move_group'" label="目标分组">
          <n-select
            v-model:value="form.group_id"
            clearable
            placeholder="留空表示迁移至公共/未归属"
            :options="options.groups.map((g) => ({ label: g.name, value: g.id }))"
          />
        </n-form-item>

        <template v-if="currentAction === 'update_status'">
          <n-form-item label="状态">
            <n-select
              v-model:value="form.status"
              clearable
              placeholder="不修改可留空"
              :options="options.dictOptions(statusDictCategory || 'asset_status')"
            />
          </n-form-item>
          <n-form-item v-if="allowEnv !== false" label="环境">
            <n-select
              v-model:value="form.env"
              clearable
              placeholder="不修改可留空"
              :options="options.dictOptions('env')"
            />
          </n-form-item>
        </template>
      </n-form>
      <template #footer>
        <div class="flex justify-end gap-2">
          <n-button @click="modalOpen = false">取消</n-button>
          <n-button type="primary" :loading="submitting" @click="runAction(currentAction)">确认执行</n-button>
        </div>
      </template>
    </n-modal>
  </div>
</template>
