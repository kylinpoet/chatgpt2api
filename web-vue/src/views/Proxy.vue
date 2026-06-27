<template>
  <div class="space-y-6">
    <PagePanel class="space-y-5">
      <PanelHeader title="代理管理" align="start">
        <template #copy>
          <p class="mt-1 text-xs text-muted-foreground">
            代理优先级：账号个人代理 > 账号组代理/代理组 > 默认代理。
          </p>
        </template>
        <template #actions>
          <Button size="sm" variant="outline" :disabled="loading" @click="loadData">
            {{ loading ? '刷新中...' : '刷新' }}
          </Button>
          <Button size="sm" variant="primary" :disabled="savingDefaultProxy || loading" @click="saveDefaultProxy">
            {{ savingDefaultProxy ? '保存中...' : '保存默认代理' }}
          </Button>
        </template>
      </PanelHeader>

      <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <FormSection density="roomy">
          <div class="grid grid-cols-1 gap-3 md:grid-cols-[12rem_minmax(0,1fr)]">
            <label class="block text-xs">
              <span class="ui-field-label">默认代理模式</span>
              <GroupedSelectMenu
                :model-value="defaultProxyMode"
                :options="defaultProxyModeOptions"
                aria-label="默认代理模式"
                selected-indicator="none"
                block
                @update:model-value="setDefaultProxyMode"
              />
            </label>

            <label v-if="defaultProxyMode === 'group'" class="block text-xs">
              <span class="ui-field-label">默认代理组</span>
              <GroupedSelectMenu
                :model-value="selectedDefaultProxyGroupId"
                :options="defaultProxyGroupOptions"
                :disabled="loading"
                aria-label="默认代理组"
                selected-indicator="none"
                block
                @update:model-value="selectDefaultProxyGroup"
              />
            </label>

            <label v-else-if="defaultProxyMode === 'custom'" class="block text-xs">
              <span class="ui-field-label">自定义代理 URL</span>
              <Input
                :model-value="defaultCustomProxyInput"
                block
                root-class="font-mono"
                placeholder="http://127.0.0.1:7890 或 socks5://127.0.0.1:7890"
                @update:model-value="setDefaultCustomProxyInput"
              />
            </label>

            <div v-else class="flex min-h-[2.5rem] items-center rounded-lg border border-dashed border-border bg-muted/20 px-3 text-xs text-muted-foreground">
              未指定账号或账号组代理时直连。
            </div>
          </div>
          <ActionRow class="mt-3" gap="tight">
            <Button size="xs" variant="outline" :disabled="testingKey === DEFAULT_TEST_KEY || !canTestDefaultProxy" @click="testDefaultProxy">
              {{ testingKey === DEFAULT_TEST_KEY ? '测试中...' : '测试默认代理' }}
            </Button>
            <Button size="xs" variant="outline" :disabled="savingDefaultProxy || testingKey === DEFAULT_TEST_KEY" @click="setDefaultProxyDirect">
              设为直连
            </Button>
          </ActionRow>
          <p class="mt-3 truncate text-xs text-muted-foreground" :title="defaultProxyPreview">
            当前默认代理：<span class="text-foreground">{{ defaultProxyPreview }}</span>
          </p>
        </FormSection>

        <FormSection density="roomy" surface="background">
          <p class="text-xs text-muted-foreground">默认代理测试结果</p>
          <div v-if="defaultTestResult" class="mt-3 space-y-1 text-xs">
            <p :class="defaultTestResult.ok ? 'text-emerald-600' : 'text-rose-600'">
              {{ defaultTestResult.ok ? '可用' : '不可用' }}
            </p>
            <p class="text-muted-foreground">HTTP {{ defaultTestResult.status || '-' }} · {{ defaultTestResult.latency_ms || 0 }}ms</p>
            <p v-if="defaultTestResult.error" class="break-all text-rose-600">{{ defaultTestResult.error }}</p>
          </div>
          <p v-else class="mt-3 text-xs text-muted-foreground">尚未测试</p>
        </FormSection>
      </div>
    </PagePanel>

    <PagePanel class="space-y-4">
      <PanelHeader title="代理组 / 多出口">
        <template #copy>
          <p class="mt-1 text-xs text-muted-foreground">
            一个代理组就是一组多出口节点；图片请求会从未满的节点里随机选择一个，请求结束前固定该出口。
          </p>
        </template>
        <template #actions>
          <Input
            :model-value="groupKeyword"
            block
            root-class="min-w-[12rem] md:w-80"
            placeholder="搜索代理组 / 节点 / 地址"
            @update:model-value="groupKeyword = $event.trim()"
          />
          <Button size="sm" variant="primary" @click="openCreateGroupModal">新建代理组</Button>
        </template>
      </PanelHeader>
      <PageLoadingState
        v-if="loading && groups.length === 0"
        title="正在加载代理组"
        description="读取代理组、节点和健康状态。"
      />
      <StateBlock v-else-if="filteredGroups.length === 0">
        <EmptyState plain title="暂无代理组" description="新建代理组后，可绑定账号组、账号或默认代理使用。" />
      </StateBlock>
      <TableShell v-else>
        <table class="min-w-[1080px] w-full table-fixed text-left text-sm">
          <colgroup>
            <col class="w-[20%]" />
            <col class="w-[7rem]" />
            <col class="w-[30%]" />
            <col class="w-[15%]" />
            <col class="w-[16%]" />
            <col class="w-[16rem]" />
          </colgroup>
          <thead class="text-xs uppercase tracking-[0.16em] text-muted-foreground">
            <tr>
              <th class="py-3 pr-4">代理组</th>
              <th class="py-3 pr-4">状态</th>
              <th class="py-3 pr-4">节点</th>
              <th class="py-3 pr-4">引用</th>
              <th class="py-3 pr-4">健康</th>
              <th class="py-3 text-right">操作</th>
            </tr>
          </thead>
          <tbody class="text-sm text-foreground">
            <tr
              v-for="group in filteredGroups"
              :key="group.id"
              class="border-t border-border transition-colors hover:bg-muted/20"
              :class="group.enabled ? '' : 'bg-muted/30'"
            >
              <td class="py-3 pr-4 align-top">
                <p class="truncate font-medium">{{ group.name || group.id }}</p>
                <p class="mt-1 text-xs text-muted-foreground">多出口 · {{ group.nodes.length }} 个节点</p>
                <p class="mt-1 truncate font-mono text-[11px] text-muted-foreground" :title="group.id">ID：{{ group.id }}</p>
                <p v-if="group.notes" class="mt-1 truncate text-xs text-muted-foreground" :title="group.notes">{{ group.notes }}</p>
              </td>
              <td class="py-3 pr-4 align-top">
                <StateBadge :tone="group.enabled ? 'success' : 'muted'" size="sm">
                  {{ group.enabled ? '启用' : '停用' }}
                </StateBadge>
              </td>
              <td class="py-3 pr-4 align-top">
                <div class="space-y-2">
                  <ProxyNodeSummaryCard
                    v-for="node in group.nodes"
                    :key="node.id"
                    :node="node"
                  />
                </div>
              </td>
              <td class="py-3 pr-4 align-top">
                <button
                  type="button"
                  class="max-w-full truncate rounded-md border border-border bg-muted/20 px-2 py-1 text-left font-mono text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:bg-primary/5 hover:text-foreground"
                  :title="`点击复制 ${proxyGroupReference(group)}`"
                  @click="copyProxyGroupReference(group)"
                >
                  {{ proxyGroupReference(group) }}
                </button>
              </td>
              <td class="py-3 pr-4 align-top">
                <div class="space-y-1.5">
                  <p
                    v-for="node in group.nodes"
                    :key="`${group.id}-${node.id}-health`"
                    class="truncate text-xs"
                    :class="nodeTestClass(group, node)"
                    :title="node.last_error || node.last_checked_at || ''"
                  >
                    {{ node.name || node.id }} · {{ nodeTestSummary(group, node) }}
                  </p>
                </div>
              </td>
              <td class="py-3 text-right align-top">
                <div class="flex items-center justify-end gap-2">
                  <Button size="xs" variant="outline" root-class="w-14 justify-center" @click="openEditGroupModal(group)">
                    编辑
                  </Button>
                  <FloatingActionMenu
                    label="更多"
                    :items="proxyGroupActionItems(group)"
                    align="right"
                    size="sm"
                    trigger-class="h-7 justify-center px-2 text-[11px]"
                    :trigger-width="64"
                    @select="handleProxyGroupAction(group, $event)"
                  />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </TableShell>
    </PagePanel>

    <ModalShell :open="showGroupModal" max-width="56rem" :z-index="120">
      <ModalHeader
        :title="editingGroupId ? '编辑代理组' : '新建代理组'"
        :close-disabled="savingGroupId === FORM_TEST_KEY"
        :bordered="false"
        compact
        @close="closeGroupModal"
      />

      <ModalBody class="space-y-4">
        <FormSection title="基础信息" surface="plain">
              <div class="grid grid-cols-1 gap-2.5 md:grid-cols-[minmax(0,1fr)_16rem]">
                <label class="text-xs">
                  <span class="ui-field-label">代理组名称</span>
                  <Input
                    :model-value="groupForm.name"
                    block
                    placeholder="香港代理池"
                    @update:model-value="groupForm.name = $event.trim()"
                  />
                </label>
                <label class="text-xs">
                  <span class="ui-field-label">代理组 ID</span>
                  <Input
                    :model-value="groupForm.id"
                    block
                    root-class="font-mono"
                    :disabled="Boolean(editingGroupId)"
                    @update:model-value="groupForm.id = normalizeGroupId($event)"
                  />
                </label>
              </div>
              <div class="grid grid-cols-1 gap-2.5 md:grid-cols-[minmax(0,1fr)_auto]">
                <label class="text-xs">
                  <span class="ui-field-label">备注</span>
                  <Input
                    :model-value="groupForm.notes"
                    block
                    placeholder="可选"
                    @update:model-value="groupForm.notes = $event.trim()"
                  />
                </label>
                <div class="flex items-end">
                  <Checkbox v-model="groupForm.enabled">启用代理组</Checkbox>
                </div>
              </div>
        </FormSection>

              <div class="space-y-3">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <p class="text-xs font-medium text-foreground">代理节点</p>
                  <Button size="xs" variant="outline" @click="addGroupNode">添加节点</Button>
                </div>
                <div class="space-y-3">
                  <FormSection
                    v-for="(node, index) in groupForm.nodes"
                    :key="`${node.id}-${index}`"
                    surface="muted"
                  >
                    <div class="grid grid-cols-1 gap-2 md:grid-cols-[10rem_minmax(0,1fr)_8rem_auto]">
                      <label class="text-xs">
                        <span class="ui-field-label">名称</span>
                        <Input
                          :model-value="node.name"
                          block
                          @update:model-value="node.name = $event.trim()"
                        />
                      </label>
                      <label class="text-xs">
                        <span class="ui-field-label">代理 URL</span>
                        <Input
                          :model-value="node.url"
                          block
                          root-class="font-mono"
                          placeholder="http://user:password@host:port"
                          @update:model-value="node.url = $event.trim()"
                        />
                      </label>
                      <label class="text-xs">
                        <span class="ui-field-label">图片并发</span>
                        <Input
                          :model-value="String(node.image_concurrency_limit ?? 0)"
                          block
                          type="number"
                          min="0"
                          step="1"
                          placeholder="默认 30，0 不限"
                          title="限制该节点同时处理的图片请求数；超出后等待同组节点空位，不会改走直连。0 表示不限制。"
                          @update:model-value="node.image_concurrency_limit = normalizeImageConcurrencyLimit($event)"
                        />
                      </label>
                      <div class="flex items-end gap-2">
                        <Checkbox v-model="node.enabled">启用</Checkbox>
                      </div>
                    </div>
                    <div class="mt-2 flex flex-wrap items-center justify-between gap-2">
                      <label class="min-w-[12rem] flex-1 text-xs">
                        <span class="ui-field-label">备注</span>
                        <Input
                          :model-value="node.notes || ''"
                          block
                          placeholder="可选"
                          @update:model-value="node.notes = $event.trim()"
                        />
                      </label>
                      <div class="flex items-end gap-2 pt-5">
                        <Button
                          size="xs"
                          variant="outline"
                          :disabled="!editingGroupId || !node.url || testingKey === `group:${editingGroupId}:${node.id}`"
                          @click="testProxyGroupNode({ id: editingGroupId, name: groupForm.name }, node)"
                        >
                          {{ testingKey === `group:${editingGroupId}:${node.id}` ? '检测中...' : '检测' }}
                        </Button>
                        <Button size="xs" variant="outline" root-class="text-rose-600" @click="removeGroupNode(index)">
                          删除
                        </Button>
                      </div>
                    </div>
                  </FormSection>
                </div>
              </div>
      </ModalBody>

      <ModalFooter :bordered="false">
        <Button size="xs" variant="outline" root-class="min-w-14 justify-center" :disabled="savingGroupId === FORM_TEST_KEY" @click="closeGroupModal">
          取消
        </Button>
        <Button size="xs" variant="primary" root-class="min-w-14 justify-center" :disabled="savingGroupId === FORM_TEST_KEY" @click="saveProxyGroup">
          {{ savingGroupId === FORM_TEST_KEY ? '保存中...' : editingGroupId ? '更新' : '保存' }}
        </Button>
      </ModalFooter>
    </ModalShell>

  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref } from 'vue'
import { Button, Checkbox, EmptyState, Input } from 'nanocat-ui'
import type { ActionMenuItem } from 'nanocat-ui'
import { prepareSettingsForEdit, proxyApi, settingsApi } from '@/api'
import { parseProxyReference, serializeProxyReference, type ProxyGroup, type ProxyNode, type ProxyTestResult } from '@/api/proxy'
import { ActionRow, FloatingActionMenu, FormSection, ModalBody, ModalFooter, ModalHeader, ModalShell, PageLoadingState, PagePanel, PanelHeader, ProxyNodeSummaryCard, StateBadge, StateBlock, TableShell, actionMenuGroups } from '@/components/ai'
import GroupedSelectMenu from '@/components/ui/GroupedSelectMenu.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useSettingsStore } from '@/stores/settings'
import { useToast } from '@/composables/useToast'
import type { Settings } from '@/types/api'

type DefaultProxyMode = 'direct' | 'group' | 'custom'

type ProxyGroupForm = {
  id: string
  name: string
  enabled: boolean
  notes: string
  nodes: ProxyNode[]
}

const DEFAULT_TEST_KEY = '__default__'
const FORM_TEST_KEY = '__form__'
const DEFAULT_PROXY_NODE_IMAGE_CONCURRENCY = 30

const settingsStore = useSettingsStore()
const toast = useToast()
const confirmDialog = useConfirmDialog()

const loading = ref(false)
const savingDefaultProxy = ref(false)
const savingGroupId = ref('')
const deletingGroupId = ref('')
const testingKey = ref('')
const groupKeyword = ref('')
const showGroupModal = ref(false)
const editingGroupId = ref('')
const defaultProxyMode = ref<DefaultProxyMode>('direct')
const selectedDefaultProxyGroupId = ref('')
const defaultCustomProxyInput = ref('')
const currentSettings = ref<Settings | null>(null)
const defaultTestResult = ref<ProxyTestResult | null>(null)
const groups = ref<ProxyGroup[]>([])
const testResults = reactive<Record<string, ProxyTestResult>>({})
const groupForm = reactive<ProxyGroupForm>(createDefaultGroupForm())
let hasActivatedOnce = false

const defaultProxyModeOptions = [
  { label: '直连', value: 'direct' },
  { label: '代理组', value: 'group' },
  { label: '自定义代理', value: 'custom' },
] as const

const filteredGroups = computed(() => {
  const query = groupKeyword.value.trim().toLowerCase()
  const rows = [...groups.value].sort((left, right) => (
    (left.name || left.id).localeCompare(right.name || right.id, 'zh-Hans-CN')
  ))
  if (!query) return rows
  return rows.filter((item) => [
    item.id,
    item.name,
    item.notes,
    ...item.nodes.flatMap((node) => [node.id, node.name, node.url, node.notes]),
  ].some((value) => String(value || '').toLowerCase().includes(query)))
})

const defaultProxyGroupOptions = computed(() => {
  const rows = groups.value.map((group) => ({
    label: `${group.enabled === false ? '停用 · ' : ''}${group.name || group.id}${Array.isArray(group.nodes) ? ` · ${group.nodes.length} 个节点` : ''}`,
    value: group.id,
  }))
  const selectedId = selectedDefaultProxyGroupId.value
  if (selectedId && !rows.some((item) => item.value === selectedId)) {
    rows.unshift({ label: `未知代理组 · ${selectedId}`, value: selectedId })
  }
  return [
    { label: '选择代理组', value: '' },
    ...rows,
  ]
})

const defaultProxyPreview = computed(() => {
  if (defaultProxyMode.value === 'direct') return '直连'
  if (defaultProxyMode.value === 'group') {
    const group = groups.value.find((item) => item.id === selectedDefaultProxyGroupId.value)
    return selectedDefaultProxyGroupId.value ? `代理组：${group?.name || selectedDefaultProxyGroupId.value}` : '代理组：未选择'
  }
  return defaultCustomProxyInput.value || '自定义代理：未填写'
})

const canTestDefaultProxy = computed(() => {
  if (defaultProxyMode.value === 'group') return Boolean(selectedDefaultProxyGroupId.value)
  if (defaultProxyMode.value === 'custom') return Boolean(defaultCustomProxyInput.value.trim())
  return false
})

const isDefaultProxyDirty = computed(() => {
  const settings = currentSettings.value
  if (!settings) return false
  return normalizeDefaultProxyForCompare(defaultProxyValue()) !== normalizeDefaultProxyForCompare(defaultProxyFromSettings(settings))
})

function createDefaultNode(index = 0): ProxyNode {
  return {
    id: createGeneratedId('node'),
    name: `出口 ${index + 1}`,
    url: '',
    enabled: true,
    image_concurrency_limit: DEFAULT_PROXY_NODE_IMAGE_CONCURRENCY,
    notes: '',
  }
}

function createDefaultGroupForm(): ProxyGroupForm {
  return {
    id: '',
    name: '',
    enabled: true,
    notes: '',
    nodes: [createDefaultNode(0)],
  }
}

function normalizeReferenceId(value: string) {
  return value
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[-._]+|[-._]+$/g, '')
    .slice(0, 64)
}

function normalizeGroupId(value: string) {
  return normalizeReferenceId(value)
}

function proxyGroupReference(group: Pick<ProxyGroup, 'id'>) {
  return serializeProxyReference('group', group.id)
}

async function copyText(value: string, message = '已复制') {
  const text = String(value || '').trim()
  if (!text) return
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const input = document.createElement('textarea')
      input.value = text
      input.setAttribute('readonly', 'readonly')
      input.style.position = 'fixed'
      input.style.opacity = '0'
      document.body.appendChild(input)
      input.select()
      document.execCommand('copy')
      document.body.removeChild(input)
    }
    toast.success(message)
  } catch {
    toast.error('复制失败')
  }
}

function copyProxyGroupReference(group: Pick<ProxyGroup, 'id'>) {
  void copyText(proxyGroupReference(group), '代理组引用已复制')
}

function createGeneratedId(prefix: string) {
  let suffix = ''
  try {
    suffix = globalThis.crypto?.randomUUID?.().replace(/-/g, '').slice(0, 10) || ''
  } catch {
    suffix = ''
  }
  if (!suffix) {
    suffix = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`.slice(0, 10)
  }
  return `${prefix}-${suffix}`
}

function normalizeImageConcurrencyLimit(value: unknown) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 0
  return Math.max(0, Math.min(10000, Math.floor(parsed)))
}

function normalizeGroupNode(item: ProxyNode, index: number): ProxyNode {
  const id = normalizeGroupId(item.id || '') || createGeneratedId('node')
  return {
    id,
    name: String(item.name || `出口 ${index + 1}`).trim(),
    url: String(item.url || '').trim(),
    enabled: item.enabled !== false,
    image_concurrency_limit: normalizeImageConcurrencyLimit(item.image_concurrency_limit ?? DEFAULT_PROXY_NODE_IMAGE_CONCURRENCY),
    last_latency_ms: Number(item.last_latency_ms || 0),
    fail_count: Number(item.fail_count || 0),
    last_error: String(item.last_error || '').trim(),
    last_checked_at: String(item.last_checked_at || '').trim(),
    last_error_at: String(item.last_error_at || '').trim(),
    cooldown_until: String(item.cooldown_until || '').trim(),
    notes: String(item.notes || '').trim(),
  }
}

function normalizeGroup(item: ProxyGroup): ProxyGroup {
  const id = normalizeGroupId(item.id || item.name || '')
  return {
    id,
    name: String(item.name || item.id || '').trim(),
    strategy: item.strategy || 'request_random',
    rotation_interval_minutes: 0,
    enabled: item.enabled !== false,
    notes: String(item.notes || '').trim(),
    nodes: Array.isArray(item.nodes)
      ? item.nodes.map(normalizeGroupNode).filter((node) => node.id)
      : [],
  }
}

function updateGroups(items: ProxyGroup[]) {
  groups.value = Array.isArray(items) ? items.map(normalizeGroup).filter((item) => item.id) : []
}

function proxyActionError(action: string, error: unknown) {
  const message = error instanceof Error ? error.message : String(error || '').trim()
  return message ? `${action}：${message}` : action
}

function defaultProxyFromSettings(settings: Settings) {
  return String(settings.basic?.proxy || settings.proxy || '').trim()
}

function defaultProxyValue() {
  if (defaultProxyMode.value === 'direct') return serializeProxyReference('direct')
  if (defaultProxyMode.value === 'group') return serializeProxyReference('group', selectedDefaultProxyGroupId.value)
  return serializeProxyReference('custom', defaultCustomProxyInput.value)
}

function normalizeDefaultProxyForCompare(value: unknown) {
  const reference = parseProxyReference(value)
  if (reference.mode === 'global' || reference.mode === 'direct') return 'direct'
  if (reference.mode === 'group') return serializeProxyReference('group', reference.value)
  if (reference.mode === 'profile') return String(value || '').trim()
  return reference.value.trim()
}

function syncDefaultProxyControlsFromValue(value: unknown) {
  const reference = parseProxyReference(value)
  selectedDefaultProxyGroupId.value = ''
  defaultCustomProxyInput.value = ''
  defaultTestResult.value = null
  if (reference.mode === 'group') {
    defaultProxyMode.value = 'group'
    selectedDefaultProxyGroupId.value = reference.value
    return
  }
  if (reference.mode === 'custom' || reference.mode === 'profile') {
    defaultProxyMode.value = 'custom'
    defaultCustomProxyInput.value = reference.mode === 'profile' ? String(value || '').trim() : reference.value
    return
  }
  defaultProxyMode.value = 'direct'
}

function setDefaultProxyMode(mode: string | string[]) {
  const value = Array.isArray(mode) ? mode[0] : mode
  defaultProxyMode.value = ['direct', 'group', 'custom'].includes(value)
    ? value as DefaultProxyMode
    : 'direct'
  defaultTestResult.value = null
}

function selectDefaultProxyGroup(groupId: string | string[]) {
  const value = Array.isArray(groupId) ? groupId[0] : groupId
  selectedDefaultProxyGroupId.value = String(value || '').trim()
  defaultProxyMode.value = 'group'
  defaultTestResult.value = null
}

function setDefaultCustomProxyInput(value: string) {
  defaultCustomProxyInput.value = String(value || '').trim()
  defaultProxyMode.value = 'custom'
  defaultTestResult.value = null
}

async function loadData() {
  loading.value = true
  try {
    const [settings, groupResponse] = await Promise.all([
      settingsApi.get(),
      proxyApi.listGroups(),
    ])
    currentSettings.value = prepareSettingsForEdit(settings)
    settingsStore.$patch({ settings })
    updateGroups(groupResponse.groups || [])
    syncDefaultProxyControlsFromValue(defaultProxyFromSettings(settings))
  } catch (error: any) {
    toast.error(error.message || '加载代理配置失败')
  } finally {
    loading.value = false
  }
}

async function saveDefaultProxy() {
  if (!currentSettings.value) {
    toast.warning('配置尚未加载完成')
    return
  }
  if (defaultProxyMode.value === 'group' && !selectedDefaultProxyGroupId.value) {
    toast.warning('请选择默认代理组')
    return
  }
  if (defaultProxyMode.value === 'custom' && !defaultCustomProxyInput.value.trim()) {
    toast.warning('请填写自定义代理 URL')
    return
  }
  const confirmed = await confirmDialog.ask({
    title: '确认保存默认代理',
    message: '即将保存默认代理配置。未单独指定代理的账号会按账号组代理、默认代理顺序回退，是否继续？',
    confirmText: '保存',
    cancelText: '取消',
  })
  if (!confirmed) return

  savingDefaultProxy.value = true
  try {
    const next = prepareSettingsForEdit(currentSettings.value)
    next.proxy = defaultProxyValue()
    const response = await settingsStore.updateSettingsPatch({
      proxy: next.proxy,
    })
    currentSettings.value = prepareSettingsForEdit(response.config || next)
    syncDefaultProxyControlsFromValue(defaultProxyFromSettings(currentSettings.value))
    toast.success('默认代理已保存')
  } catch (error: any) {
    toast.error(proxyActionError('保存默认代理失败', error))
  } finally {
    savingDefaultProxy.value = false
  }
}

function setDefaultProxyDirect() {
  defaultProxyMode.value = 'direct'
  selectedDefaultProxyGroupId.value = ''
  defaultCustomProxyInput.value = ''
  defaultTestResult.value = null
}

async function testDefaultProxy() {
  if (defaultProxyMode.value === 'direct') {
    toast.info('直连模式无需测试代理')
    return
  }
  if (defaultProxyMode.value === 'group' && !selectedDefaultProxyGroupId.value) {
    toast.warning('请选择默认代理组')
    return
  }
  if (defaultProxyMode.value === 'custom' && !defaultCustomProxyInput.value.trim()) {
    toast.warning('请先填写自定义代理 URL')
    return
  }
  const confirmed = await confirmDialog.ask({
    title: '确认测试默认代理',
    message: '即将使用当前默认代理发起外部网络测试请求。请确认当前允许测试该代理连接。',
    confirmText: '开始测试',
    cancelText: '取消',
  })
  if (!confirmed) return

  testingKey.value = DEFAULT_TEST_KEY
  try {
    if (defaultProxyMode.value === 'group') {
      const response = await proxyApi.testGroup({ id: selectedDefaultProxyGroupId.value })
      if (response.groups) updateGroups(response.groups)
      const results = response.results || []
      const failed = results.filter((item) => !item.result.ok)
      const firstResult = results[0]?.result
      const maxLatency = results.reduce((max, item) => Math.max(max, Number(item.result.latency_ms || 0)), 0)
      defaultTestResult.value = {
        ok: results.length > 0 && failed.length === 0,
        status: firstResult?.status || 0,
        latency_ms: maxLatency,
        error: failed.length ? `代理组检测完成，失败 ${failed.length} 个节点` : null,
      }
      if (defaultTestResult.value.ok) toast.success(`默认代理组可用，共 ${results.length} 个节点`)
      else toast.warning(defaultTestResult.value.error || '默认代理组测试失败')
      return
    }
    const response = await proxyApi.test(defaultCustomProxyInput.value.trim())
    defaultTestResult.value = response.result
    if (response.result.ok) toast.success(`默认代理可用，耗时 ${response.result.latency_ms}ms`)
    else toast.warning(response.result.error || '默认代理测试失败')
  } catch (error: any) {
    defaultTestResult.value = {
      ok: false,
      status: 0,
      latency_ms: 0,
      error: error.message || '默认代理测试失败',
    }
    toast.error(error.message || '默认代理测试失败')
  } finally {
    testingKey.value = ''
  }
}

function resetGroupForm() {
  editingGroupId.value = ''
  Object.assign(groupForm, createDefaultGroupForm())
}

function openCreateGroupModal() {
  resetGroupForm()
  showGroupModal.value = true
}

function openEditGroupModal(group: ProxyGroup) {
  editingGroupId.value = group.id
  Object.assign(groupForm, {
    id: group.id,
    name: group.name || group.id,
    enabled: group.enabled !== false,
    notes: group.notes || '',
    nodes: group.nodes.length ? group.nodes.map((node, index) => normalizeGroupNode(node, index)) : [createDefaultNode(0)],
  })
  showGroupModal.value = true
}

function closeGroupModal() {
  if (savingGroupId.value === FORM_TEST_KEY) return
  showGroupModal.value = false
  resetGroupForm()
}

function addGroupNode() {
  groupForm.nodes.push(createDefaultNode(groupForm.nodes.length))
}

function removeGroupNode(index: number) {
  if (groupForm.nodes.length <= 1) {
    groupForm.nodes = [createDefaultNode(0)]
    return
  }
  groupForm.nodes.splice(index, 1)
}

async function saveProxyGroup() {
  const groupName = groupForm.name.trim()
  if (!groupName) {
    toast.warning('请填写代理组名称')
    return
  }
  const id = normalizeGroupId(editingGroupId.value || groupForm.id) || createGeneratedId('pg')
  const nodes = groupForm.nodes
    .map((node, index) => normalizeGroupNode(node, index))
    .filter((node) => node.url)
  if (!nodes.length) {
    toast.warning('请至少填写一个代理节点地址')
    return
  }

  savingGroupId.value = FORM_TEST_KEY
  try {
    const wasEditing = Boolean(editingGroupId.value)
    const response = await proxyApi.saveGroup({
      id,
      name: groupName,
      strategy: 'request_random',
      enabled: groupForm.enabled,
      notes: groupForm.notes.trim(),
      nodes,
      create_only: !editingGroupId.value,
    })
    updateGroups(response.groups || [])
    savingGroupId.value = ''
    closeGroupModal()
    toast.success(wasEditing ? '代理组已更新' : '代理组已创建')
  } catch (error: any) {
    toast.error(proxyActionError('保存代理组失败', error))
  } finally {
    savingGroupId.value = ''
  }
}

async function toggleProxyGroup(group: ProxyGroup) {
  const nextEnabled = !group.enabled
  const confirmed = await confirmDialog.ask({
    title: nextEnabled ? '确认启用代理组' : '确认停用代理组',
    message: `即将${nextEnabled ? '启用' : '停用'}代理组 ${group.name || group.id}。绑定到该组的账号组会受到影响，是否继续？`,
    confirmText: nextEnabled ? '启用' : '停用',
    cancelText: '取消',
  })
  if (!confirmed) return

  savingGroupId.value = group.id
  try {
    const response = await proxyApi.saveGroup({
      ...group,
      enabled: nextEnabled,
    })
    updateGroups(response.groups || [])
    toast.success(`代理组 ${group.name || group.id} 已${group.enabled ? '停用' : '启用'}`)
  } catch (error: any) {
    toast.error(proxyActionError('切换代理组失败', error))
  } finally {
    savingGroupId.value = ''
  }
}

async function deleteProxyGroup(group: ProxyGroup) {
  const confirmed = await confirmDialog.ask({
    title: '删除代理组',
    message: `确认删除代理组 ${group.name || group.id}？账号组里已有的绑定不会自动清空。`,
    confirmText: '确认删除',
    cancelText: '取消',
  })
  if (!confirmed) return

  deletingGroupId.value = group.id
  try {
    const response = await proxyApi.deleteGroup(group.id)
    updateGroups(response.groups || [])
    toast.success('代理组已删除')
  } catch (error: any) {
    toast.error(proxyActionError('删除代理组失败', error))
  } finally {
    deletingGroupId.value = ''
  }
}

function proxyGroupActionItems(group: ProxyGroup): ActionMenuItem[] {
  const allKey = `group:${group.id}:all`
  return actionMenuGroups(
    [
      {
        key: 'test-all',
        label: testingKey.value === allKey ? '检测中...' : '检测全部节点',
        disabled: testingKey.value === allKey || group.nodes.length === 0,
      },
    ],
    [
      {
        key: 'toggle-enabled',
        label: savingGroupId.value === group.id
          ? '处理中...'
          : group.enabled ? '停用代理组' : '启用代理组',
        disabled: savingGroupId.value === group.id,
      },
    ],
    [
      {
        key: 'delete',
        label: deletingGroupId.value === group.id ? '删除中...' : '删除代理组',
        danger: true,
        disabled: deletingGroupId.value === group.id,
      },
    ],
  )
}

function handleProxyGroupAction(group: ProxyGroup, action: string) {
  if (action === 'test-all') void testProxyGroupAll(group)
  if (action === 'toggle-enabled') void toggleProxyGroup(group)
  if (action === 'delete') void deleteProxyGroup(group)
}

async function testProxyGroupNode(group: Pick<ProxyGroup, 'id' | 'name'>, node: ProxyNode) {
  const confirmed = await confirmDialog.ask({
    title: '确认测试代理节点',
    message: `即将使用代理组 ${group.name || group.id} 的节点 ${node.name || node.id} 发起外部网络测试请求。请确认当前允许测试该代理连接。`,
    confirmText: '开始测试',
    cancelText: '取消',
  })
  if (!confirmed) return

  const key = `group:${group.id}:${node.id}`
  testingKey.value = key
  try {
    const response = await proxyApi.testGroup({ id: group.id, node_id: node.id })
    if (response.groups) updateGroups(response.groups)
    const result = response.result || response.results?.[0]?.result
    if (result) testResults[key] = result
    if (result?.ok) toast.success(`节点检测通过，耗时 ${result.latency_ms}ms`)
    else toast.warning(result?.error || '节点检测失败')
  } catch (error: any) {
    testResults[key] = {
      ok: false,
      status: 0,
      latency_ms: 0,
      error: error.message || '节点检测失败',
    }
    toast.error(error.message || '节点检测失败')
  } finally {
    testingKey.value = ''
  }
}

async function testProxyGroupAll(group: ProxyGroup) {
  const confirmed = await confirmDialog.ask({
    title: '确认测试代理组',
    message: `即将测试代理组 ${group.name || group.id} 内的 ${group.nodes.length} 个节点。每个节点都会发起外部网络测试请求，是否继续？`,
    confirmText: '开始测试',
    cancelText: '取消',
  })
  if (!confirmed) return

  const key = `group:${group.id}:all`
  testingKey.value = key
  try {
    const response = await proxyApi.testGroup({ id: group.id })
    if (response.groups) updateGroups(response.groups)
    const results = response.results || []
    for (const item of results) {
      if (item.node_id && item.result) {
        testResults[`group:${group.id}:${item.node_id}`] = item.result
      }
    }
    const failed = results.filter((item) => !item.result.ok)
    if (failed.length) toast.warning(`代理组检测完成，失败 ${failed.length} 个节点`)
    else toast.success(`代理组检测通过，共 ${results.length} 个节点`)
  } catch (error: any) {
    toast.error(error.message || '代理组检测失败')
  } finally {
    testingKey.value = ''
  }
}

function nodeTestSummary(group: ProxyGroup, node: ProxyNode) {
  if (testingKey.value === `group:${group.id}:all` || testingKey.value === `group:${group.id}:${node.id}`) return '检测中...'
  const result = testResults[`group:${group.id}:${node.id}`]
  if (result?.ok) return `HTTP ${result.status || '-'} · ${result.latency_ms || 0}ms`
  if (result && !result.ok) return result.error || '检测失败'
  if (node.last_error) return node.last_error
  if (node.last_checked_at) return `${node.last_latency_ms || 0}ms`
  return '尚未测试'
}

function nodeTestClass(group: ProxyGroup, node: ProxyNode) {
  if (testingKey.value === `group:${group.id}:all` || testingKey.value === `group:${group.id}:${node.id}`) return 'text-sky-600'
  const result = testResults[`group:${group.id}:${node.id}`]
  if (result) return result.ok ? 'text-emerald-600' : 'text-rose-600'
  if (node.last_error) return 'text-rose-600'
  if (node.last_checked_at) return 'text-emerald-600'
  return 'text-muted-foreground'
}

onMounted(() => {
  void loadData()
})

onActivated(() => {
  if (!hasActivatedOnce) {
    hasActivatedOnce = true
    return
  }
  if (showGroupModal.value || savingDefaultProxy.value || savingGroupId.value || testingKey.value || isDefaultProxyDirty.value) return
  void loadData()
})
</script>
