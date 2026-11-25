<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { NButton, NTag, useMessage, type DataTableColumns, type UploadCustomRequestOptions } from 'naive-ui'
import {
  excelApi,
  type ConflictStrategy,
  type ErrorMode,
  type ExcelCommitResponse,
  type ExcelPreviewResponse,
  type ExcelRowResult,
  type ExcelUploadResponse,
  type ImportBatchItem,
  type ImportMode,
} from '@/api/excel'
import { extractErrorMessage } from '@/api/client'
import { useOptionsStore } from '@/stores/options'

const options = useOptionsStore()
const message = useMessage()

const activeTab = ref('import')
const step = ref(1)

// Step 1-4 Import state
const uploading = ref(false)
const previewing = ref(false)
const committing = ref(false)
const uploadInfo = ref<ExcelUploadResponse | null>(null)
const previewResult = ref<ExcelPreviewResponse | null>(null)
const commitResult = ref<ExcelCommitResponse | null>(null)

const importCfg = reactive({
  mode: 'multi_sheet' as ImportMode,
  conflict_strategy: 'merge_non_empty' as ConflictStrategy,
  error_mode: 'all_or_nothing' as ErrorMode,
  default_group_id: null as number | null,
  sheet_name: '' as string,
  column_mapping: {} as Record<string, string>,
  store_unmapped_to_extra: true,
})

const MAPPING_PRESET_KEY = 'csm_wide_mapping_preset'

// Export state
const exporting = ref(false)
const exportCfg = reactive({
  mode: 'multi_sheet' as ImportMode,
  group_id: null as number | null,
  env: null as string | null,
})

// History state
const batchesLoading = ref(false)
const batches = ref<ImportBatchItem[]>([])

const activeWideSheet = computed(() => {
  if (!uploadInfo.value) return null
  return (
    uploadInfo.value.sheets.find((s) => s.name === importCfg.sheet_name) ||
    uploadInfo.value.sheets[0] ||
    null
  )
})

const sourceHeaderOptions = computed(() =>
  (activeWideSheet.value?.headers || []).map((hdr) => ({ label: hdr, value: hdr })),
)

async function loadInit() {
  await options.ensureLoaded()
  await loadBatches()
}

async function loadBatches() {
  batchesLoading.value = true
  try {
    batches.value = (await excelApi.listBatches()).data
  } catch (err) {
    message.error(extractErrorMessage(err, '加载导入批次历史失败'))
  } finally {
    batchesLoading.value = false
  }
}

async function handleDownloadTemplate() {
  try {
    await excelApi.downloadTemplate()
    message.success('标准 6-Sheet 导入模板已开始下载')
  } catch (err) {
    message.error(extractErrorMessage(err, '模板下载失败'))
  }
}

async function customUploadRequest({ file, onFinish, onError }: UploadCustomRequestOptions) {
  if (!file.file) return
  uploading.value = true
  try {
    const res = await excelApi.upload(file.file)
    uploadInfo.value = res.data
    importCfg.mode = res.data.detected_mode
    importCfg.sheet_name = res.data.sheets[0]?.name || ''
    importCfg.column_mapping = { ...res.data.suggested_mapping }
    previewResult.value = null
    commitResult.value = null
    step.value = 2
    message.success(`文件解析成功，已自动识别为「${res.data.detected_mode === 'multi_sheet' ? '标准多 Sheet 模板' : '单 Sheet 宽表'}」`)
    onFinish()
  } catch (err) {
    message.error(extractErrorMessage(err, '上传解析失败'))
    onError()
  } finally {
    uploading.value = false
  }
}

function saveMappingPreset() {
  localStorage.setItem(MAPPING_PRESET_KEY, JSON.stringify(importCfg.column_mapping))
  message.success('已保存列映射预设到本地浏览器')
}

function loadMappingPreset() {
  const raw = localStorage.getItem(MAPPING_PRESET_KEY)
  if (!raw) {
    message.info('暂无已保存的列映射预设')
    return
  }
  try {
    const parsed = JSON.parse(raw) as Record<string, string>
    const validHeaders = new Set(activeWideSheet.value?.headers || [])
    const applied: Record<string, string> = {}
    for (const [k, v] of Object.entries(parsed)) {
      if (validHeaders.has(v)) applied[k] = v
    }
    importCfg.column_mapping = applied
    message.success(`已套用 ${Object.keys(applied).length} 项匹配列映射`)
  } catch {
    message.error('映射预设解析失败')
  }
}

async function runDryRunPreview() {
  if (!uploadInfo.value) return
  previewing.value = true
  try {
    const res = await excelApi.preview({
      upload_id: uploadInfo.value.upload_id,
      mode: importCfg.mode,
      conflict_strategy: importCfg.conflict_strategy,
      error_mode: importCfg.error_mode,
      default_group_id: importCfg.default_group_id,
      sheet_name: importCfg.mode === 'wide_table' ? importCfg.sheet_name : null,
      column_mapping: importCfg.mode === 'wide_table' ? importCfg.column_mapping : {},
      store_unmapped_to_extra: importCfg.store_unmapped_to_extra,
    })
    previewResult.value = res.data
    step.value = 3
  } catch (err) {
    message.error(extractErrorMessage(err, 'Dry-run 预校验失败'))
  } finally {
    previewing.value = false
  }
}

async function runCommitImport() {
  if (!uploadInfo.value) return
  committing.value = true
  try {
    const res = await excelApi.commit({
      upload_id: uploadInfo.value.upload_id,
      mode: importCfg.mode,
      conflict_strategy: importCfg.conflict_strategy,
      error_mode: importCfg.error_mode,
      default_group_id: importCfg.default_group_id,
      sheet_name: importCfg.mode === 'wide_table' ? importCfg.sheet_name : null,
      column_mapping: importCfg.mode === 'wide_table' ? importCfg.column_mapping : {},
      store_unmapped_to_extra: importCfg.store_unmapped_to_extra,
    })
    commitResult.value = res.data
    step.value = 4
    if (res.data.status === 'completed') {
      message.success(`导入完成：新增 ${res.data.created_count} 条，更新 ${res.data.updated_count} 条`)
    } else if (res.data.status === 'completed_with_errors') {
      message.warning(`部分导入完成：新增 ${res.data.created_count}，失败 ${res.data.failed_count}（可下载错误清单）`)
    } else {
      message.error(`因存在 ${res.data.failed_count} 条错误行，整批已安全回滚`)
    }
    await loadBatches()
  } catch (err) {
    message.error(extractErrorMessage(err, '执行导入失败'))
  } finally {
    committing.value = false
  }
}

function resetWizard() {
  step.value = 1
  uploadInfo.value = null
  previewResult.value = null
  commitResult.value = null
}

async function handleExport() {
  exporting.value = true
  try {
    await excelApi.exportWorkbook({
      mode: exportCfg.mode,
      group_id: exportCfg.group_id,
      env: exportCfg.env,
    })
    message.success('导出文件已开始下载（已自动排除敏感凭据）')
  } catch (err) {
    message.error(extractErrorMessage(err, '导出失败'))
  } finally {
    exporting.value = false
  }
}

const previewColumns: DataTableColumns<ExcelRowResult> = [
  { title: 'Sheet', key: 'sheet', width: 120 },
  { title: '行号', key: 'row_num', width: 70 },
  { title: '资产类型', key: 'entity_type', width: 100 },
  { title: '标识键', key: 'natural_key', width: 190, ellipsis: { tooltip: true } },
  {
    title: '操作',
    key: 'operation',
    width: 90,
    render: (r) => {
      const map: Record<string, { type: 'success' | 'info' | 'default' | 'error'; label: string }> = {
        create: { type: 'success', label: '新增' },
        update: { type: 'info', label: '更新' },
        skip: { type: 'default', label: '跳过' },
        error: { type: 'error', label: '错误' },
      }
      const cfg = map[r.operation] || { type: 'default', label: r.operation }
      return h(NTag, { size: 'small', type: cfg.type }, { default: () => cfg.label })
    },
  },
  { title: '说明 / 错误原因', key: 'reason', render: (r) => r.reason || '—' },
]

const batchColumns: DataTableColumns<ImportBatchItem> = [
  { title: '批次 #', key: 'id', width: 80 },
  { title: '文件名', key: 'filename', ellipsis: { tooltip: true } },
  { title: '模式', key: 'mode', width: 110, render: (r) => (r.mode === 'multi_sheet' ? '6-Sheet 模板' : '宽表映射') },
  { title: '新增', key: 'created_count', width: 70 },
  { title: '更新', key: 'updated_count', width: 70 },
  { title: '跳过', key: 'skipped_count', width: 70 },
  { title: '失败', key: 'failed_count', width: 70 },
  {
    title: '状态',
    key: 'status',
    width: 120,
    render: (r) =>
      h(
        NTag,
        {
          size: 'small',
          type: r.status === 'completed' ? 'success' : r.status === 'rolled_back' ? 'error' : 'warning',
        },
        { default: () => r.status },
      ),
  },
  { title: '时间', key: 'created_at', width: 180, render: (r) => new Date(r.created_at).toLocaleString() },
  {
    title: '错误清单',
    key: 'err',
    width: 110,
    render: (r) =>
      r.failed_count > 0
        ? h(
            NButton,
            { size: 'tiny', type: 'error', secondary: true, onClick: () => excelApi.downloadBatchErrors(r.id) },
            { default: () => '下载错误表' },
          )
        : '—',
  },
]

onMounted(loadInit)
</script>

<template>
  <n-card title="Excel 双向批量导入与导出中心">
    <n-tabs v-model:value="activeTab" type="line">
      <!-- Tab 1: Import Wizard -->
      <n-tab-pane name="import" tab="批量导入向导">
        <div class="mb-6 mt-2">
          <n-steps :current="step" size="small">
            <n-step title="上传 Excel" description="支持 6-Sheet 标准模板或任意单 Sheet 宽表" />
            <n-step title="映射与策略" description="配置列映射、重复合并与错误处理方式" />
            <n-step title="Dry-run 预览" description="核对新增/更新/错误明细（未落库）" />
            <n-step title="执行结果" description="确认入库并追溯批次号" />
          </n-steps>
        </div>

        <!-- Step 1 -->
        <div v-if="step === 1" class="space-y-4">
          <n-alert type="info">
            推荐下载标准 6-Sheet 模板（包含 集群 / 设备 / 网卡 / IP / 服务与端点 / URL），也支持直接上传你现有的零散单 Sheet 宽表并在下一步映射列名。
          </n-alert>

          <div class="flex gap-3">
            <n-button type="primary" secondary @click="handleDownloadTemplate">
              下载标准 6-Sheet Excel 模板
            </n-button>
          </div>

          <n-upload
            accept=".xlsx"
            :max="1"
            :show-file-list="false"
            :custom-request="customUploadRequest"
          >
            <n-upload-dragger>
              <div class="py-6">
                <div class="text-base font-medium text-gray-700">点击或拖拽 `.xlsx` 文件到此处上传</div>
                <div class="text-xs text-gray-400 mt-1">系统将自动识别工作表结构，不会立即修改数据库</div>
              </div>
            </n-upload-dragger>
          </n-upload>
        </div>

        <!-- Step 2 -->
        <div v-if="step === 2 && uploadInfo" class="space-y-4">
          <n-card size="small" class="bg-slate-50">
            <div class="flex flex-wrap items-center justify-between gap-2 text-sm">
              <div>
                已上传文件：<span class="font-semibold">{{ uploadInfo.filename }}</span>
                <n-tag size="small" type="info" class="ml-2">共 {{ uploadInfo.sheets.length }} 个 Sheet</n-tag>
              </div>
              <n-button size="tiny" quaternary @click="resetWizard">重新选择文件</n-button>
            </div>
          </n-card>

          <n-form label-placement="left" label-width="110">
            <div class="grid grid-cols-1 md:grid-cols-2 gap-x-6">
              <n-form-item label="导入模式">
                <n-radio-group v-model:value="importCfg.mode">
                  <n-space>
                    <n-radio value="multi_sheet">标准 6-Sheet 模板</n-radio>
                    <n-radio value="wide_table">单 Sheet 宽表列映射</n-radio>
                  </n-space>
                </n-radio-group>
              </n-form-item>

              <n-form-item label="默认归属分组">
                <n-select
                  v-model:value="importCfg.default_group_id"
                  clearable
                  placeholder="表格未填 group_code 时的默认分组"
                  :options="options.groups.map((g) => ({ label: `${g.name} (${g.code})`, value: g.id }))"
                />
              </n-form-item>

              <n-form-item label="重复记录策略">
                <n-select
                  v-model:value="importCfg.conflict_strategy"
                  :options="[
                    { label: '仅更新非空字段（推荐 merge_non_empty）', value: 'merge_non_empty' },
                    { label: '跳过已存在记录（skip）', value: 'skip' },
                    { label: '全量覆盖映射字段（overwrite）', value: 'overwrite' },
                  ]"
                />
              </n-form-item>

              <n-form-item label="错误行处理">
                <n-select
                  v-model:value="importCfg.error_mode"
                  :options="[
                    { label: '任一错误整批回滚（all_or_nothing）', value: 'all_or_nothing' },
                    { label: '跳过错误行并导出错误清单（skip_errors）', value: 'skip_errors' },
                  ]"
                />
              </n-form-item>
            </div>

            <!-- Wide table mapping panel -->
            <template v-if="importCfg.mode === 'wide_table'">
              <n-divider>宽表列映射配置</n-divider>
              <div class="flex flex-wrap items-center justify-between gap-3 mb-3">
                <div class="flex items-center gap-3">
                  <span class="text-sm text-gray-600">目标 Sheet:</span>
                  <n-select
                    v-model:value="importCfg.sheet_name"
                    class="w-200px"
                    :options="uploadInfo.sheets.map((s) => ({ label: `${s.name} (${s.row_count} 行)`, value: s.name }))"
                  />
                  <n-checkbox v-model:checked="importCfg.store_unmapped_to_extra">
                    未映射列自动存入设备扩展属性 (extra_attrs)
                  </n-checkbox>
                </div>
                <n-space size="small">
                  <n-button size="tiny" @click="loadMappingPreset">加载上次预设</n-button>
                  <n-button size="tiny" @click="saveMappingPreset">保存为预设</n-button>
                </n-space>
              </div>

              <div class="grid grid-cols-1 md:grid-cols-3 gap-3 p-3 rounded border border-gray-200 bg-white">
                <div v-for="field in uploadInfo.wide_fields" :key="field.key" class="flex items-center gap-2">
                  <span class="text-xs text-gray-600 w-96px shrink-0">{{ field.label }}</span>
                  <n-select
                    v-model:value="importCfg.column_mapping[field.key]"
                    size="small"
                    clearable
                    placeholder="不映射"
                    :options="sourceHeaderOptions"
                  />
                </div>
              </div>
            </template>
          </n-form>

          <div class="flex justify-end gap-3 pt-2">
            <n-button @click="resetWizard">上一步</n-button>
            <n-button type="primary" :loading="previewing" @click="runDryRunPreview">
              执行 Dry-run 差异预览
            </n-button>
          </div>
        </div>

        <!-- Step 3: Dry-run preview -->
        <div v-if="step === 3 && previewResult" class="space-y-4">
          <n-grid cols="2 s:5" responsive="screen" :x-gap="12" :y-gap="12">
            <n-gi><n-card size="small"><n-statistic label="总操作条数" :value="previewResult.total_rows" /></n-card></n-gi>
            <n-gi><n-card size="small"><n-statistic label="预计新增" :value="previewResult.create_count" /></n-card></n-gi>
            <n-gi><n-card size="small"><n-statistic label="预计更新" :value="previewResult.update_count" /></n-card></n-gi>
            <n-gi><n-card size="small"><n-statistic label="预计跳过" :value="previewResult.skip_count" /></n-card></n-gi>
            <n-gi><n-card size="small"><n-statistic label="校验错误" :value="previewResult.error_count" /></n-card></n-gi>
          </n-grid>

          <n-alert v-if="previewResult.error_count > 0 && importCfg.error_mode === 'all_or_nothing'" type="error">
            检测到 {{ previewResult.error_count }} 条错误，当前模式为「整批回滚」，请修正表格后再导入，或返回上一步切换为「跳过错误行并导出错误清单」。
          </n-alert>
          <n-alert v-else-if="previewResult.error_count > 0" type="warning">
            检测到 {{ previewResult.error_count }} 条错误，执行导入时将跳过这些错误行，并在完成后提供错误清单 Excel 下载。
          </n-alert>

          <n-data-table
            size="small"
            :max-height="360"
            :columns="previewColumns"
            :data="previewResult.rows"
            :bordered="false"
          />

          <div class="flex justify-end gap-3">
            <n-button @click="step = 2">返回修改策略</n-button>
            <n-button
              type="primary"
              :disabled="!previewResult.can_commit"
              :loading="committing"
              @click="runCommitImport"
            >
              确认写入数据库
            </n-button>
          </div>
        </div>

        <!-- Step 4: Commit result -->
        <div v-if="step === 4 && commitResult" class="space-y-4">
          <n-result
            :status="commitResult.status === 'completed' ? 'success' : commitResult.status === 'rolled_back' ? 'error' : 'warning'"
            :title="`导入批次 #${commitResult.batch_id} · ${commitResult.status}`"
            :description="`新增 ${commitResult.created_count} 条 · 更新 ${commitResult.updated_count} 条 · 跳过 ${commitResult.skipped_count} 条 · 失败 ${commitResult.failed_count} 条`"
          >
            <template #footer>
              <n-space justify="center">
                <n-button
                  v-if="commitResult.failed_count > 0"
                  type="error"
                  secondary
                  @click="excelApi.downloadBatchErrors(commitResult.batch_id)"
                >
                  下载错误原因 Excel 清单
                </n-button>
                <n-button type="primary" @click="resetWizard">继续导入新表格</n-button>
              </n-space>
            </template>
          </n-result>
        </div>
      </n-tab-pane>

      <!-- Tab 2: Export Center -->
      <n-tab-pane name="export" tab="条件导出中心">
        <div class="max-w-520px space-y-4 mt-2">
          <n-alert type="info">
            导出将严格遵循你的分组可见范围，且<strong>绝不会包含任何加密凭据或密码明文</strong>。
          </n-alert>
          <n-form label-placement="left" label-width="100">
            <n-form-item label="导出格式">
              <n-radio-group v-model:value="exportCfg.mode">
                <n-space vertical>
                  <n-radio value="multi_sheet">标准 6-Sheet 完整台账（可直接修改后二次导入）</n-radio>
                  <n-radio value="wide_table">单 Sheet 扁平宽表（适合汇报与盘点）</n-radio>
                </n-space>
              </n-radio-group>
            </n-form-item>
            <n-form-item label="筛选运维分组">
              <n-select
                v-model:value="exportCfg.group_id"
                clearable
                placeholder="全部可见分组"
                :options="options.groups.map((g) => ({ label: g.name, value: g.id }))"
              />
            </n-form-item>
            <n-form-item label="筛选环境">
              <n-select
                v-model:value="exportCfg.env"
                clearable
                placeholder="全部环境"
                :options="options.dictOptions('env')"
              />
            </n-form-item>
            <n-form-item>
              <n-button type="primary" :loading="exporting" @click="handleExport">
                一键导出 Excel
              </n-button>
            </n-form-item>
          </n-form>
        </div>
      </n-tab-pane>

      <!-- Tab 3: Batch History -->
      <n-tab-pane name="history" tab="导入批次历史">
        <div class="flex justify-end mb-3">
          <n-button size="small" @click="loadBatches">刷新</n-button>
        </div>
        <n-data-table
          size="small"
          :loading="batchesLoading"
          :columns="batchColumns"
          :data="batches"
          :bordered="false"
        />
      </n-tab-pane>
    </n-tabs>
  </n-card>
</template>
