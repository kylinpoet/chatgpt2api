<template>
  <div class="studio-workspace" :class="{ 'is-fullscreen': isFullscreen }" :style="workspaceStyle">
    <div class="studio-sidebar-wrap">
      <StudioHistoryPanel
        :conversations="conversations"
        :active-conversation-id="activeConversationId"
        :badges="conversationBadges"
        @create="createConversation"
        @select="selectConversation"
        @rename="renameConversation"
        @delete="deleteConversation"
        @clear="confirmClearHistory"
        @reorder="reorderConversation"
      />

      <div
        class="studio-history-resizer"
        role="separator"
        aria-orientation="vertical"
        title="拖动调整历史栏宽度"
        @pointerdown="startSidebarResize"
      ></div>
    </div>

    <main class="studio-main">
      <div class="chat-header-bar">
        <div class="chat-header-title">
          <Button
            size="sm"
            variant="ghost"
            icon-only
            root-class="chat-header-icon lg:hidden"
            title="打开会话列表"
            aria-label="打开会话列表"
            @click="isMobileHistoryOpen = true"
          >
            <Icon icon="lucide:panel-left-open" class="h-4 w-4" />
          </Button>
          <div class="min-w-0">
            <div class="chat-header-name">{{ activeConversation?.title || '新对话' }}</div>
            <div class="chat-header-subtitle">{{ activeHeaderSubtitle }}</div>
          </div>
        </div>

        <div class="chat-header-actions">
          <Button size="sm" variant="outline" root-class="chat-header-action-button" @click="createConversation()">
            <Icon icon="lucide:plus" class="h-3.5 w-3.5" />
            <span class="chat-header-action-label hidden sm:inline">新对话</span>
          </Button>
          <Button
            size="sm"
            variant="outline"
            root-class="chat-header-action-button"
            :disabled="!activeConversation?.messages.length"
            @click="clearCurrentConversation"
          >
            <Icon icon="lucide:trash-2" class="h-3.5 w-3.5" />
            <span class="chat-header-action-label hidden sm:inline">清空</span>
          </Button>
          <Button
            size="sm"
            variant="ghost"
            icon-only
            root-class="chat-header-action-button"
            :title="isFullscreen ? '退出全屏' : '全屏'"
            :aria-label="isFullscreen ? '退出全屏' : '全屏'"
            @click="toggleFullscreen"
          >
            <Icon :icon="isFullscreen ? 'lucide:minimize-2' : 'lucide:maximize-2'" class="h-4 w-4" />
          </Button>
        </div>
      </div>

      <StudioMessageList
        ref="messageListRef"
        :conversation="activeConversation"
        :conversations-count="conversations.length"
        :tasks="imageTasks"
        :fullscreen="isFullscreen"
        @create="createConversation"
        @open-history="isMobileHistoryOpen = true"
        @toggle-fullscreen="toggleFullscreen"
        @retry="retryMessage"
        @edit="editMessage"
        @resend="resendMessage"
        @retry-assistant="retryAssistantMessage"
        @delete-message="deleteMessage"
        @copy-message="copyText"
        @preview="openPreview"
      />

      <StudioComposer
        v-model:mode="composeMode"
        v-model:text="composerText"
        v-model:chat-model="chatModel"
        :image-form="imageForm"
        :chat-model-options="chatModelOptions"
        :image-model-options="imageModelOptions"
        :references="referencePreviews"
        :is-sending="isSending"
        :is-streaming="isStreaming"
        :is-editing="Boolean(editingMessageId)"
        :error="composerError"
        @update:image-model="imageForm.model = $event"
        @update:image-size="imageForm.size = $event"
        @update:image-quality="imageForm.quality = $event"
        @update:image-count="imageForm.n = $event"
        @submit="sendMessage"
        @stop="stopStreaming"
        @cancel-edit="cancelMessageEdit"
        @add-files="appendFiles"
        @remove-reference="removeReference"
        @clear-references="clearReferences"
        @preview-reference="previewReference"
      />
    </main>

    <StudioMobileHistory
      :open="isMobileHistoryOpen"
      :conversations="conversations"
      :active-conversation-id="activeConversationId"
      :badges="conversationBadges"
      @close="isMobileHistoryOpen = false"
      @select="selectConversation"
      @delete="deleteConversation"
    />

    <StudioLightbox
      :preview="previewImage"
      @close="previewImage = null"
      @copy="copyText"
      @download="downloadPreviewImage"
    />
  </div>
</template>

<script setup lang="ts">
import { Icon } from '@iconify/vue'
import { Button } from 'nanocat-ui'
import { computed, defineAsyncComponent, nextTick, onActivated, onBeforeUnmount, onDeactivated, onMounted, reactive, ref, watch } from 'vue'
import { imageTasksApi } from '@/api/imageTasks'
import { streamChatCompletion } from '@/api/chatStream'
import type { DebugChatMessage } from '@/api/debug'
import {
  DEFAULT_IMAGE_MODEL,
  DEFAULT_IMAGE_QUALITY,
  DEFAULT_IMAGE_SIZE,
  isImageSizeSupportedByModel,
  isImageTaskTerminal,
  normalizeImageCount,
  taskPrimaryMessage,
  type ImageTask,
} from '@/api/imageTasks'
import { useModelCatalog } from '@/composables/useModelCatalog'
import { useSettingsStore } from '@/stores/settings'
import { useToast } from '@/composables/useToast'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import {
  getBooleanPreference,
  getJsonPreference,
  getNumberPreference,
  getStringPreference,
  preferenceKeys,
  setBooleanPreference,
  setJsonPreference,
  setNumberPreference,
  setStringPreference,
} from '@/lib/preferences'
import { downloadUrlAsFile } from '@/lib/downloads'
import StudioComposer from '@/components/studio/StudioComposer.vue'
import StudioHistoryPanel from '@/components/studio/StudioHistoryPanel.vue'
import type {
  StudioComposeMode,
  StudioConversation,
  StudioConversationBadge,
  StudioConversationBadgeState,
  StudioImageForm,
  StudioMessage,
  StudioMessageStatus,
  StudioPreviewImage,
  StudioReference,
} from '@/components/studio/types'

defineOptions({ name: 'Studio' })

const StudioLightbox = defineAsyncComponent(() => import('@/components/studio/StudioLightbox.vue'))
const StudioMessageList = defineAsyncComponent(() => import('@/components/studio/StudioMessageList.vue'))
const StudioMobileHistory = defineAsyncComponent(() => import('@/components/studio/StudioMobileHistory.vue'))

const settingsStore = useSettingsStore()
const toast = useToast()
const confirmDialog = useConfirmDialog()
const { chatModels, imageModels, loadModelCatalog } = useModelCatalog(() => settingsStore.settings)

const defaultSidebarWidth = 244
const composeMode = ref<StudioComposeMode>(normalizeMode(getStringPreference(preferenceKeys.studioActiveMode, 'image')))
const composerText = ref('')
const composerError = ref('')
const editingMessageId = ref('')
const isSending = ref(false)
const isStreaming = ref(false)
const isFullscreen = ref(getBooleanPreference(preferenceKeys.studioFullscreen, false))
const isMobileHistoryOpen = ref(false)
const isFetchingTasks = ref(false)
const sidebarWidth = ref(getNumberPreference(preferenceKeys.studioSidebarWidth, defaultSidebarWidth, { min: 220, max: 380 }))
type StudioMessageListExpose = { scrollToBottom: () => Promise<void> | void }
const messageListRef = ref<StudioMessageListExpose | null>(null)

const chatModel = ref(getStringPreference(preferenceKeys.studioChatModel, 'auto') || 'auto')
const imageForm = reactive<StudioImageForm>({
  model: getStringPreference(preferenceKeys.studioImageModel, DEFAULT_IMAGE_MODEL) || DEFAULT_IMAGE_MODEL,
  size: DEFAULT_IMAGE_SIZE,
  quality: DEFAULT_IMAGE_QUALITY,
  n: 1,
})

const conversations = ref<StudioConversation[]>(loadConversations())
const activeConversationId = ref(getStringPreference(preferenceKeys.studioActiveConversationId, ''))
const conversationNotices = ref<Record<string, StudioConversationBadgeState>>(loadConversationNotices())
const imageTasks = ref<ImageTask[]>([])
const selectedFiles = ref<File[]>([])
const referencePreviews = ref<StudioReference[]>([])
const previewImage = ref<StudioPreviewImage | null>(null)

let imagePollTimer: number | null = null
let streamController: AbortController | null = null
let sidebarResizeStartX = 0
let sidebarResizeStartWidth = defaultSidebarWidth
let conversationsPersistTimer: number | null = null
let conversationNoticesPersistTimer: number | null = null
let activeConversationPersistTimer: number | null = null
let imageRefreshTimer: number | null = null
let imageRefreshQueued = false
let imageRefreshQueuedForce = false
let scrollFrameId: number | null = null
let scrollScheduled = false
let scrollRequestToken = 0
let pendingConversationSelectId = ''
let conversationSelectFrameId: number | null = null
let lastSuccessfulImageRefreshSignature = ''
let hasActivatedOnce = false
let isStudioActive = true

const workspaceStyle = computed(() => ({
  '--studio-history-width': `${sidebarWidth.value}px`,
}))
const activeConversation = computed(() => {
  return conversations.value.find((conversation) => conversation.id === activeConversationId.value)
    || conversations.value[0]
    || null
})
const activeHeaderSubtitle = computed(() => {
  if (isStreaming.value || isSending.value) return '正在生成'
  if (activeRunningTaskCount.value > 0) return `图片处理中 ${activeRunningTaskCount.value}`
  const count = activeConversation.value?.messages.length || 0
  return count ? `${count} 条消息` : '准备就绪'
})
const taskById = computed(() => new Map(imageTasks.value.map((task) => [task.id, task])))
const activeImageTaskIds = computed(() => {
  const ids = activeConversation.value?.messages.map((message) => message.taskId).filter(Boolean) || []
  return Array.from(new Set(ids)).slice(0, 80)
})
const conversationTaskState = computed(() => {
  const pendingIds = new Set<string>()
  const runningCounts: Record<string, number> = {}
  conversations.value.forEach((conversation) => {
    let running = 0
    conversation.messages.forEach((message) => {
      if (message.mode === 'image' && isImageMessageRunning(message)) {
        running += 1
        if (message.taskId) pendingIds.add(message.taskId)
      } else if (message.status === 'sending' || message.status === 'streaming') {
        running += 1
      }
    })
    if (running > 0) runningCounts[conversation.id] = running
  })
  return {
    pendingImageTaskIds: Array.from(pendingIds).slice(0, 160),
    runningCounts,
  }
})
const pendingImageTaskIds = computed(() => conversationTaskState.value.pendingImageTaskIds)
const requestedImageTaskIds = computed(() => Array.from(new Set([
  ...activeImageTaskIds.value,
  ...pendingImageTaskIds.value,
])).slice(0, 180))
const activeRunningTaskCount = computed(() => activeConversation.value ? (conversationTaskState.value.runningCounts[activeConversation.value.id] || 0) : 0)
const conversationBadges = computed<Record<string, StudioConversationBadge>>(() => {
  const badges: Record<string, StudioConversationBadge> = {}
  conversations.value.forEach((conversation) => {
    const running = conversationTaskState.value.runningCounts[conversation.id] || 0
    if (running > 0) {
      badges[conversation.id] = {
        state: 'running',
        label: `处理中 ${running}`,
        count: running,
      }
      return
    }
    const notice = conversationNotices.value[conversation.id]
    if (notice === 'done') {
      badges[conversation.id] = { state: 'done', label: '已完成' }
    } else if (notice === 'error') {
      badges[conversation.id] = { state: 'error', label: '失败' }
    }
  })
  return badges
})
const chatModelOptions = computed(() => uniqueStrings(['auto', ...chatModels.value]))
const imageModelOptions = computed(() => uniqueStrings([imageForm.model, DEFAULT_IMAGE_MODEL, ...imageModels.value]))

watch(composeMode, (mode) => setStringPreference(preferenceKeys.studioActiveMode, mode))
watch(chatModel, (model) => setStringPreference(preferenceKeys.studioChatModel, model || 'auto'))
watch(conversations, schedulePersistConversations)
watch(conversationNotices, schedulePersistConversationNotices)
watch(activeConversationId, schedulePersistActiveConversationId)
watch(requestedImageTaskIds, () => scheduleImageTaskRefresh())
watch(pendingImageTaskIds, scheduleImagePoll)
watch(isFullscreen, (value) => setBooleanPreference(preferenceKeys.studioFullscreen, value))
watch(sidebarWidth, (value) => setNumberPreference(preferenceKeys.studioSidebarWidth, value))
watch(() => imageForm.model, (model) => {
  setStringPreference(preferenceKeys.studioImageModel, model || DEFAULT_IMAGE_MODEL)
  if (!isImageSizeSupportedByModel(imageForm.size, model)) imageForm.size = DEFAULT_IMAGE_SIZE
})

function normalizeMode(value: string): StudioComposeMode {
  return value === 'chat' ? 'chat' : 'image'
}

function uniqueStrings(values: string[]) {
  return values.map((value) => String(value || '').trim()).filter((value, index, arr) => value && arr.indexOf(value) === index)
}

function createId(prefix: string) {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return `${prefix}-${crypto.randomUUID()}`
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function cleanText(value: unknown) {
  return String(value ?? '').trim()
}

function loadConversations(): StudioConversation[] {
  const items = getJsonPreference<unknown[]>(preferenceKeys.studioConversations, [])
  if (!Array.isArray(items)) return []
  return items.map(normalizeConversation).filter((item): item is StudioConversation => Boolean(item)).slice(0, 80)
}

function loadConversationNotices(): Record<string, StudioConversationBadgeState> {
  const raw = getJsonPreference<Record<string, unknown>>(preferenceKeys.studioConversationBadges, {})
  const notices: Record<string, StudioConversationBadgeState> = {}
  Object.entries(raw || {}).forEach(([id, state]) => {
    if (state === 'done' || state === 'error') notices[id] = state
  })
  return notices
}

function normalizeConversation(item: unknown): StudioConversation | null {
  if (!item || typeof item !== 'object') return null
  const raw = item as Partial<StudioConversation>
  const messages = Array.isArray(raw.messages)
    ? raw.messages.map(normalizeMessage).filter((message): message is StudioMessage => Boolean(message)).slice(-160)
    : []
  return {
    id: cleanText(raw.id) || createId('studio'),
    title: cleanText(raw.title) || '新对话',
    createdAt: cleanText(raw.createdAt) || new Date().toISOString(),
    updatedAt: cleanText(raw.updatedAt) || new Date().toISOString(),
    messages,
  }
}

function normalizeMessage(item: unknown): StudioMessage | null {
  if (!item || typeof item !== 'object') return null
  const raw = item as Partial<StudioMessage>
  const content = cleanText(raw.content)
  const taskId = cleanText(raw.taskId)
  if (!content && !taskId) return null
  return {
    id: cleanText(raw.id) || createId('message'),
    role: raw.role === 'assistant' ? 'assistant' : 'user',
    mode: raw.mode === 'chat' ? 'chat' : 'image',
    content,
    createdAt: cleanText(raw.createdAt) || new Date().toISOString(),
    status: normalizeMessageStatus(raw.status),
    model: cleanText(raw.model) || undefined,
    imageSize: cleanText(raw.imageSize) || undefined,
    imageCount: Number.isFinite(Number(raw.imageCount)) ? normalizeImageCount(raw.imageCount) : undefined,
    taskId: taskId || undefined,
    error: cleanText(raw.error) || undefined,
    attachments: Array.isArray(raw.attachments) ? raw.attachments.map(cleanText).filter(Boolean).slice(0, 8) : undefined,
  }
}

function normalizeMessageStatus(value: unknown): StudioMessageStatus | undefined {
  if (['sending', 'streaming', 'queued', 'running', 'done', 'error'].includes(String(value))) {
    return String(value) as StudioMessageStatus
  }
  return undefined
}

function persistConversations() {
  const payload = conversations.value.slice(0, 80).map((conversation) => ({
    ...conversation,
    messages: conversation.messages.slice(-160).map((message) => ({
      ...message,
      status: message.status === 'streaming' || message.status === 'sending' ? 'done' : message.status,
    })),
  }))
  setJsonPreference(preferenceKeys.studioConversations, payload)
}

function schedulePersistConversations() {
  if (conversationsPersistTimer !== null) return
  conversationsPersistTimer = window.setTimeout(() => {
    conversationsPersistTimer = null
    persistConversations()
  }, 300)
}

function flushPersistConversations() {
  if (conversationsPersistTimer !== null) {
    window.clearTimeout(conversationsPersistTimer)
    conversationsPersistTimer = null
  }
  persistConversations()
}

function persistConversationNotices() {
  const validIds = new Set(conversations.value.map((conversation) => conversation.id))
  const payload = Object.fromEntries(
    Object.entries(conversationNotices.value).filter(([id, state]) => validIds.has(id) && (state === 'done' || state === 'error')),
  )
  setJsonPreference(preferenceKeys.studioConversationBadges, payload)
}

function schedulePersistConversationNotices() {
  if (conversationNoticesPersistTimer !== null) return
  conversationNoticesPersistTimer = window.setTimeout(() => {
    conversationNoticesPersistTimer = null
    persistConversationNotices()
  }, 300)
}

function flushPersistConversationNotices() {
  if (conversationNoticesPersistTimer !== null) {
    window.clearTimeout(conversationNoticesPersistTimer)
    conversationNoticesPersistTimer = null
  }
  persistConversationNotices()
}

function schedulePersistActiveConversationId() {
  if (activeConversationPersistTimer !== null) {
    window.clearTimeout(activeConversationPersistTimer)
  }
  activeConversationPersistTimer = window.setTimeout(() => {
    activeConversationPersistTimer = null
    setStringPreference(preferenceKeys.studioActiveConversationId, activeConversationId.value)
  }, 200)
}

function flushPersistActiveConversationId() {
  if (activeConversationPersistTimer !== null) {
    window.clearTimeout(activeConversationPersistTimer)
    activeConversationPersistTimer = null
  }
  setStringPreference(preferenceKeys.studioActiveConversationId, activeConversationId.value)
}

function buildTitle(content: string) {
  const title = content.trim().replace(/\s+/g, ' ')
  return title.length > 18 ? `${title.slice(0, 18)}...` : title || '新对话'
}

function ensureConversation(content = '') {
  if (activeConversation.value) return activeConversation.value
  return createConversation(content)
}

function createConversation(seed = '') {
  cancelPendingConversationSelection()
  cancelMessageEdit(false)
  const seedText = typeof seed === 'string' ? seed : ''
  const now = new Date().toISOString()
  const conversation: StudioConversation = {
    id: createId('studio'),
    title: seedText ? buildTitle(seedText) : '新对话',
    createdAt: now,
    updatedAt: now,
    messages: [],
  }
  conversations.value = [conversation, ...conversations.value]
  activeConversationId.value = conversation.id
  isMobileHistoryOpen.value = false
  scheduleScrollToBottom()
  return conversation
}

function selectConversation(id: string) {
  if (!id || (activeConversationId.value === id && !pendingConversationSelectId)) return
  pendingConversationSelectId = id
  if (conversationSelectFrameId !== null) return
  conversationSelectFrameId = window.requestAnimationFrame(() => {
    conversationSelectFrameId = null
    const nextId = pendingConversationSelectId
    pendingConversationSelectId = ''
    applyConversationSelection(nextId)
  })
}

function applyConversationSelection(id: string) {
  if (!id || activeConversationId.value === id) return
  if (!conversations.value.some((conversation) => conversation.id === id)) return
  cancelMessageEdit(false)
  activeConversationId.value = id
  clearConversationNotice(id)
  composerError.value = ''
}

function cancelPendingConversationSelection() {
  pendingConversationSelectId = ''
  if (conversationSelectFrameId !== null) {
    window.cancelAnimationFrame(conversationSelectFrameId)
    conversationSelectFrameId = null
  }
}

function renameConversation(id: string, title: string) {
  const conversation = conversations.value.find((item) => item.id === id)
  if (!conversation) return
  const nextTitle = title.trim()
  conversation.title = nextTitle || '新对话'
  touchConversation(conversation)
}

function reorderConversation(sourceId: string, targetId: string) {
  if (!sourceId || !targetId || sourceId === targetId) return
  const sourceIndex = conversations.value.findIndex((item) => item.id === sourceId)
  const targetIndex = conversations.value.findIndex((item) => item.id === targetId)
  if (sourceIndex < 0 || targetIndex < 0) return
  const next = conversations.value.slice()
  const [moved] = next.splice(sourceIndex, 1)
  next.splice(targetIndex, 0, moved)
  conversations.value = next
  schedulePersistConversations()
}

async function deleteConversation(id: string) {
  cancelPendingConversationSelection()
  const conversation = conversations.value.find((item) => item.id === id)
  if (!conversation) return
  const ok = await confirmDialog.ask({
    title: '删除对话',
    message: `确定删除“${conversation.title || '未命名对话'}”吗？本地历史会被移除。`,
    confirmText: '删除',
    cancelText: '取消',
  })
  if (!ok) return
  conversations.value = conversations.value.filter((item) => item.id !== id)
  clearConversationNotice(id)
  if (activeConversationId.value === id) activeConversationId.value = conversations.value[0]?.id || ''
  if (!conversations.value.length) createConversation()
  schedulePersistConversations()
}

async function confirmClearHistory() {
  cancelPendingConversationSelection()
  if (!conversations.value.length) return
  const ok = await confirmDialog.ask({
    title: '清空历史',
    message: '确定清空本地对话画图历史吗？已生成的图片文件不会删除。',
    confirmText: '清空',
    cancelText: '取消',
  })
  if (!ok) return
  cancelMessageEdit()
  conversations.value = []
  imageTasks.value = []
  conversationNotices.value = {}
  activeConversationId.value = ''
  createConversation()
}

async function clearCurrentConversation() {
  const conversation = activeConversation.value
  if (!conversation?.messages.length) return
  const ok = await confirmDialog.ask({
    title: '清空当前对话',
    message: `确定清空“${conversation.title || '未命名对话'}”中的消息吗？`,
    confirmText: '清空',
    cancelText: '取消',
  })
  if (!ok) return
  cancelMessageEdit()
  conversation.messages = []
  conversation.title = '新对话'
  clearConversationNotice(conversation.id)
  touchConversation(conversation)
  imageTasks.value = []
  composerError.value = ''
  scheduleScrollToBottom()
}

function deleteMessage(messageId: string) {
  const conversation = activeConversation.value
  if (!conversation) return
  if (editingMessageId.value === messageId) cancelMessageEdit()
  conversation.messages = conversation.messages.filter((message) => message.id !== messageId)
  touchConversation(conversation)
}

function retryMessage(message: StudioMessage) {
  cancelMessageEdit(false)
  fillComposerFromMessage(message)
}

function editMessage(message: StudioMessage) {
  const target = findConversationMessage(message.id)
  if (!target || target.message.role !== 'user') return
  activeConversationId.value = target.conversation.id
  editingMessageId.value = message.id
  composerText.value = target.message.content
  composeMode.value = target.message.mode
  composerError.value = ''
  clearReferences()
  scheduleScrollToBottom()
}

async function resendMessage(message: StudioMessage) {
  if (isSending.value || isStreaming.value) return
  cancelMessageEdit(false)
  fillComposerFromMessage(message)
  await nextTick()
  await sendMessage()
}

async function retryAssistantMessage(message: StudioMessage) {
  if (isSending.value || isStreaming.value) return
  const target = findConversationMessage(message.id)
  if (!target) return
  const { conversation, index } = target
  const previousUserMessage = conversation.messages
    .slice(0, index)
    .reverse()
    .find((item) => item.role === 'user' && item.content.trim())
  if (!previousUserMessage) return
  activeConversationId.value = conversation.id
  conversation.messages = conversation.messages.slice(0, index)
  composerError.value = ''
  clearConversationNotice(conversation.id)
  touchConversation(conversation)
  isSending.value = true
  try {
    if (previousUserMessage.mode === 'chat') {
      await sendTextMessage(conversation)
    } else {
      await sendImageMessage(conversation, previousUserMessage.content, [])
    }
  } catch (error) {
    const mode = previousUserMessage.mode
    const retryError = errorMessage(error, mode === 'image' ? '图片重新生成失败' : '对话重新生成失败')
    composerError.value = retryError
    markConversationNotice(conversation.id, 'error')
    addMessage(conversation, {
      role: 'assistant',
      mode,
      content: retryError,
      status: 'error',
      error: retryError,
    })
  } finally {
    isSending.value = false
    scheduleScrollToBottom()
  }
}

function findConversationMessage(messageId: string) {
  if (!messageId) return null
  for (const conversation of conversations.value) {
    const index = conversation.messages.findIndex((item) => item.id === messageId)
    if (index >= 0) return { conversation, index, message: conversation.messages[index] }
  }
  return null
}

function cancelMessageEdit(clearComposer = true) {
  editingMessageId.value = ''
  composerError.value = ''
  if (clearComposer) composerText.value = ''
}

function fillComposerFromMessage(message: StudioMessage) {
  composerText.value = message.content
  composeMode.value = message.mode
  composerError.value = ''
}

function isImageMessageRunning(message: StudioMessage) {
  if (!message.taskId) return message.status === 'queued' || message.status === 'running'
  const task = taskById.value.get(message.taskId)
  if (task) return !isImageTaskTerminal(task)
  return message.status === 'queued' || message.status === 'running'
}

function addMessage(conversation: StudioConversation, message: Omit<StudioMessage, 'id' | 'createdAt'>) {
  const next: StudioMessage = {
    id: createId('message'),
    createdAt: new Date().toISOString(),
    ...message,
  }
  conversation.messages.push(next)
  const inserted = conversation.messages[conversation.messages.length - 1] || next
  touchConversation(conversation)
  if (conversation.title === '新对话' && message.role === 'user') {
    conversation.title = buildTitle(message.content)
  }
  scheduleScrollToBottom()
  return inserted
}

function touchConversation(conversation: StudioConversation) {
  conversation.updatedAt = new Date().toISOString()
  schedulePersistConversations()
}

function markConversationNotice(conversationId: string, state: StudioConversationBadgeState) {
  if (!conversationId) return
  const current = conversationNotices.value[conversationId]
  const nextState = current === 'error' && state === 'done' ? current : state
  conversationNotices.value = {
    ...conversationNotices.value,
    [conversationId]: nextState,
  }
  schedulePersistConversationNotices()
}

function clearConversationNotice(conversationId: string) {
  if (!conversationId || !conversationNotices.value[conversationId]) return
  const next = { ...conversationNotices.value }
  delete next[conversationId]
  conversationNotices.value = next
  schedulePersistConversationNotices()
}

async function sendMessage() {
  const content = composerText.value.trim()
  if (!content || isSending.value || isStreaming.value) return

  composerError.value = ''
  if (editingMessageId.value) {
    await sendEditedMessage(content)
    return
  }
  const conversation = ensureConversation(content)
  const mode = composeMode.value
  const files = selectedFiles.value.slice(0, 8)
  addMessage(conversation, {
    role: 'user',
    mode,
    content,
    status: 'done',
    attachments: mode === 'image' && referencePreviews.value.length ? referencePreviews.value.map((file) => file.name) : undefined,
  })
  composerText.value = ''
  isSending.value = true

  try {
    if (mode === 'chat') {
      await sendTextMessage(conversation)
    } else {
      await sendImageMessage(conversation, content, files)
      clearReferences()
    }
  } catch (error) {
    const message = errorMessage(error, mode === 'image' ? '图片生成失败' : '对话请求失败')
    composerError.value = message
    markConversationNotice(conversation.id, 'error')
    addMessage(conversation, {
      role: 'assistant',
      mode,
      content: message,
      status: 'error',
      error: message,
    })
  } finally {
    isSending.value = false
    scheduleScrollToBottom()
  }
}

async function sendEditedMessage(content: string) {
  const target = findConversationMessage(editingMessageId.value)
  if (!target || target.message.role !== 'user') {
    editingMessageId.value = ''
    return
  }

  const { conversation, index, message } = target
  const mode = composeMode.value
  const files = selectedFiles.value.slice(0, 8)
  const editedMessage: StudioMessage = {
    ...message,
    mode,
    content,
    status: 'done',
    error: undefined,
    attachments: mode === 'image' && referencePreviews.value.length ? referencePreviews.value.map((file) => file.name) : undefined,
  }

  activeConversationId.value = conversation.id
  conversation.messages = [
    ...conversation.messages.slice(0, index),
    editedMessage,
  ]
  if (!conversation.messages.slice(0, index).some((item) => item.role === 'user')) {
    conversation.title = buildTitle(content)
  }
  editingMessageId.value = ''
  composerText.value = ''
  composerError.value = ''
  clearConversationNotice(conversation.id)
  touchConversation(conversation)
  isSending.value = true

  try {
    if (mode === 'chat') {
      await sendTextMessage(conversation)
    } else {
      await sendImageMessage(conversation, content, files)
      clearReferences()
    }
  } catch (error) {
    const messageText = errorMessage(error, mode === 'image' ? '鍥剧墖鐢熸垚澶辫触' : '瀵硅瘽璇锋眰澶辫触')
    composerError.value = messageText
    markConversationNotice(conversation.id, 'error')
    addMessage(conversation, {
      role: 'assistant',
      mode,
      content: messageText,
      status: 'error',
      error: messageText,
    })
  } finally {
    isSending.value = false
    scheduleScrollToBottom()
  }
}

async function sendTextMessage(conversation: StudioConversation) {
  const assistantMessage = addMessage(conversation, {
    role: 'assistant',
    mode: 'chat',
    content: '',
    status: 'streaming',
    model: chatModel.value,
  })
  const controller = new AbortController()
  streamController = controller
  isStreaming.value = true
  let pendingDelta = ''
  let deltaFrameId: number | null = null
  const flushPendingDelta = () => {
    if (deltaFrameId !== null) {
      window.cancelAnimationFrame(deltaFrameId)
      deltaFrameId = null
    }
    if (!pendingDelta) return
    assistantMessage.content += pendingDelta
    pendingDelta = ''
    touchConversation(conversation)
    scheduleScrollToBottom()
  }
  const scheduleDeltaFlush = (delta: string) => {
    pendingDelta += delta
    if (deltaFrameId !== null) return
    deltaFrameId = window.requestAnimationFrame(() => {
      deltaFrameId = null
      flushPendingDelta()
    })
  }

  try {
    await streamChatCompletion({
      model: chatModel.value,
      messages: buildChatMessages(conversation, assistantMessage.id),
      signal: controller.signal,
      onDelta: (delta) => {
        scheduleDeltaFlush(delta)
      },
    })
    flushPendingDelta()
    assistantMessage.status = 'done'
    if (!assistantMessage.content.trim()) assistantMessage.content = '上游没有返回内容。'
    markConversationNotice(conversation.id, 'done')
  } catch (error) {
    flushPendingDelta()
    if (controller.signal.aborted) {
      assistantMessage.status = 'done'
      if (!assistantMessage.content.trim()) assistantMessage.content = '已停止。'
      markConversationNotice(conversation.id, 'done')
      return
    }
    const message = errorMessage(error, '对话请求失败')
    assistantMessage.status = 'error'
    assistantMessage.error = message
    assistantMessage.content = assistantMessage.content.trim() ? `${assistantMessage.content}\n\n${message}` : message
    markConversationNotice(conversation.id, 'error')
  } finally {
    flushPendingDelta()
    isStreaming.value = false
    streamController = null
    touchConversation(conversation)
  }
}

function buildChatMessages(conversation: StudioConversation, currentAssistantId: string): DebugChatMessage[] {
  return conversation.messages
    .filter((message) => {
      if (message.id === currentAssistantId) return false
      if (message.error) return false
      if (!message.content.trim()) return false
      if (message.role === 'assistant' && message.mode !== 'chat') return false
      return true
    })
    .map((message): DebugChatMessage => ({
      role: message.role === 'assistant' ? 'assistant' : 'user',
      content: message.mode === 'image' && message.role === 'user'
        ? `画图请求：${message.content}`
        : message.content,
    }))
    .slice(-32)
}

async function sendImageMessage(conversation: StudioConversation, prompt: string, files: File[]) {
  const assistantMessage = addMessage(conversation, {
    role: 'assistant',
    mode: 'image',
    content: files.length ? '图像编辑任务已提交' : '图片任务已提交',
    status: 'queued',
    model: imageForm.model,
    imageSize: imageForm.size,
    imageCount: normalizeImageCount(imageForm.n),
  })

  let task: ImageTask
  try {
    task = files.length
      ? await imageTasksApi.createEdit({
        prompt,
        files,
        model: imageForm.model || DEFAULT_IMAGE_MODEL,
        n: normalizeImageCount(imageForm.n),
        size: imageForm.size,
        quality: imageForm.quality || DEFAULT_IMAGE_QUALITY,
      })
      : await imageTasksApi.createGeneration({
        prompt,
        model: imageForm.model || DEFAULT_IMAGE_MODEL,
        n: normalizeImageCount(imageForm.n),
        size: imageForm.size,
        quality: imageForm.quality || DEFAULT_IMAGE_QUALITY,
      })
  } catch (error) {
    const message = errorMessage(error, '图片任务提交失败')
    assistantMessage.status = 'error'
    assistantMessage.content = message
    assistantMessage.error = message
    composerError.value = message
    touchConversation(conversation)
    markConversationNotice(conversation.id, 'error')
    return
  }

  assistantMessage.taskId = task.id
  assistantMessage.status = 'running'
  touchConversation(conversation)
  rememberImageTaskId(task.id)
  mergeImageTasks([task])
  toast.success('图片任务已提交')
  scheduleImagePoll()
}

function stopStreaming() {
  streamController?.abort()
}

function toggleFullscreen() {
  isFullscreen.value = !isFullscreen.value
  void nextTick(scrollToBottom)
}

function errorMessage(error: unknown, fallback: string) {
  if (error instanceof Error && error.message) return error.message
  if (error && typeof error === 'object' && 'message' in error) return String((error as { message?: unknown }).message || fallback)
  return fallback
}

function storedImageTaskIds() {
  const ids = getJsonPreference<unknown[]>(preferenceKeys.imageTaskLocalIds, [])
  return Array.isArray(ids) ? ids.map((id) => cleanText(id)).filter(Boolean) : []
}

function rememberImageTaskId(taskId: string) {
  if (!taskId) return
  const ids = Array.from(new Set([taskId, ...storedImageTaskIds()])).slice(0, 160)
  setJsonPreference(preferenceKeys.imageTaskLocalIds, ids)
}

async function refreshImageTasks(force = false) {
  if (!isStudioActive) return
  if (isFetchingTasks.value) {
    imageRefreshQueued = true
    imageRefreshQueuedForce = imageRefreshQueuedForce || force
    return
  }
  const ids = requestedImageTaskIds.value
  const signature = ids.join('\u0000')
  if (!force && signature && signature === lastSuccessfulImageRefreshSignature) return
  if (!ids.length) {
    imageTasks.value = []
    lastSuccessfulImageRefreshSignature = ''
    return
  }
  isFetchingTasks.value = true
  try {
    const response = await imageTasksApi.list(ids)
    mergeImageTasks(response.items)
    markMissingImageTasks(response.missing_ids)
    syncImageMessageStatuses()
    composerError.value = ''
    lastSuccessfulImageRefreshSignature = signature
  } catch (error) {
    composerError.value = errorMessage(error, '刷新图片任务失败')
    lastSuccessfulImageRefreshSignature = ''
  } finally {
    isFetchingTasks.value = false
    scheduleImagePoll()
    if (imageRefreshQueued) {
      const queuedForce = imageRefreshQueuedForce
      imageRefreshQueued = false
      imageRefreshQueuedForce = false
      scheduleImageTaskRefresh(0, queuedForce)
    }
  }
}

function mergeImageTasks(items: ImageTask[]) {
  const map = new Map(imageTasks.value.map((task) => [task.id, task]))
  items.filter((task) => task.id).forEach((task) => map.set(task.id, task))
  imageTasks.value = Array.from(map.values())
  lastSuccessfulImageRefreshSignature = ''
}

function markMissingImageTasks(taskIds: string[]) {
  const missing = new Set(taskIds.filter(Boolean))
  if (!missing.size) return
  conversations.value.forEach((conversation) => {
    conversation.messages.forEach((message) => {
      if (!message.taskId || !missing.has(message.taskId)) return
      if (message.status === 'done' || message.status === 'error') return
      message.status = 'error'
      message.error = '图片任务已过期或不存在'
      touchConversation(conversation)
      markConversationNotice(conversation.id, 'error')
    })
  })
}

function syncImageMessageStatuses() {
  conversations.value.forEach((conversation) => {
    let changed = false
    conversation.messages.forEach((message) => {
      if (!message.taskId) return
      const task = taskById.value.get(message.taskId)
      if (!task) return
      const previousStatus = message.status
      if (task.status === 'success') {
        message.status = 'done'
        if (previousStatus !== 'done') markConversationNotice(conversation.id, 'done')
      } else if (task.status === 'error') {
        message.status = 'error'
        message.error = taskPrimaryMessage(task) || task.error || '图片任务失败'
        if (previousStatus !== 'error') markConversationNotice(conversation.id, 'error')
      } else {
        message.status = 'running'
      }
      if (message.status !== previousStatus) changed = true
    })
    if (changed) touchConversation(conversation)
  })
}

function scheduleImagePoll() {
  if (imagePollTimer !== null) {
    window.clearTimeout(imagePollTimer)
    imagePollTimer = null
  }
  if (!isStudioActive) return
  if (!pendingImageTaskIds.value.length) return
  imagePollTimer = window.setTimeout(() => {
    imagePollTimer = null
    void refreshImageTasks(true)
  }, 4000)
}

function scheduleImageTaskRefresh(delay = 120, force = false) {
  if (!isStudioActive) return
  if (imageRefreshTimer !== null) {
    window.clearTimeout(imageRefreshTimer)
  }
  imageRefreshTimer = window.setTimeout(() => {
    imageRefreshTimer = null
    void refreshImageTasks(force)
  }, delay)
}

function isImageFile(file: File) {
  return file.type.startsWith('image/') || /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|svg|tiff?|webp)$/i.test(file.name)
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result || ''))
    reader.onerror = () => reject(new Error('读取参考图失败'))
    reader.readAsDataURL(file)
  })
}

async function appendFiles(files: File[]) {
  const imageFiles = files.filter(isImageFile).slice(0, Math.max(0, 8 - selectedFiles.value.length))
  if (!imageFiles.length) return
  for (const file of imageFiles) {
    selectedFiles.value.push(file)
    referencePreviews.value.push({
      id: createId('source'),
      name: file.name || '参考图',
      type: file.type || 'image/png',
      size: file.size,
      dataUrl: await readFileAsDataUrl(file),
    })
  }
  composeMode.value = 'image'
}

function removeReference(index: number) {
  selectedFiles.value.splice(index, 1)
  referencePreviews.value.splice(index, 1)
}

function clearReferences() {
  selectedFiles.value = []
  referencePreviews.value = []
}

function previewReference(reference: StudioReference) {
  if (!reference.dataUrl) return
  previewImage.value = {
    src: reference.dataUrl,
    name: reference.name,
  }
}

function openPreview(src: string, name: string, localPath = '') {
  if (!src) return
  previewImage.value = { src, name, localPath }
}

async function copyText(value: string) {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    toast.success('已复制')
  } catch {
    toast.error('复制失败')
  }
}

async function downloadPreviewImage() {
  if (!previewImage.value) return
  try {
    await downloadUrlAsFile(previewImage.value.src, previewImage.value.name || 'image.png', { localPath: previewImage.value.localPath })
    toast.success('已开始下载')
  } catch (error: any) {
    toast.error(`下载失败：${error.message || '无法读取图片文件'}`)
  }
}

function scrollToBottom() {
  void messageListRef.value?.scrollToBottom()
}

function scheduleScrollToBottom() {
  if (!isStudioActive) return
  if (scrollScheduled) return
  const requestToken = ++scrollRequestToken
  scrollScheduled = true
  void nextTick(() => {
    if (scrollFrameId !== null) return
    scrollFrameId = window.requestAnimationFrame(() => {
      scrollFrameId = null
      scrollScheduled = false
      if (requestToken !== scrollRequestToken || !isStudioActive) return
      scrollToBottom()
    })
  })
}

function startSidebarResize(event: PointerEvent) {
  event.preventDefault()
  ;(event.currentTarget as HTMLElement | null)?.setPointerCapture?.(event.pointerId)
  sidebarResizeStartX = event.clientX
  sidebarResizeStartWidth = sidebarWidth.value
  document.body.classList.add('studio-resizing')
  window.addEventListener('pointermove', handleSidebarResize)
  window.addEventListener('pointerup', stopSidebarResize, { once: true })
  window.addEventListener('pointercancel', stopSidebarResize, { once: true })
}

function handleSidebarResize(event: PointerEvent) {
  const nextWidth = sidebarResizeStartWidth + event.clientX - sidebarResizeStartX
  sidebarWidth.value = Math.min(380, Math.max(220, Math.round(nextWidth)))
}

function stopSidebarResize() {
  document.body.classList.remove('studio-resizing')
  window.removeEventListener('pointermove', handleSidebarResize)
  window.removeEventListener('pointerup', stopSidebarResize)
  window.removeEventListener('pointercancel', stopSidebarResize)
}

function clearImagePollTimer() {
  if (imagePollTimer !== null) {
    window.clearTimeout(imagePollTimer)
    imagePollTimer = null
  }
}

function clearImageRefreshTimer() {
  if (imageRefreshTimer !== null) {
    window.clearTimeout(imageRefreshTimer)
    imageRefreshTimer = null
  }
}

function cancelScheduledScroll() {
  scrollRequestToken += 1
  if (scrollFrameId !== null) {
    window.cancelAnimationFrame(scrollFrameId)
    scrollFrameId = null
  }
  scrollScheduled = false
}

function stopTransientStudioUi() {
  stopSidebarResize()
  cancelPendingConversationSelection()
  cancelScheduledScroll()
}

function ensureActiveConversation() {
  if (!conversations.value.length) {
    createConversation()
  } else if (!activeConversationId.value || !conversations.value.some((item) => item.id === activeConversationId.value)) {
    activeConversationId.value = conversations.value[0]?.id || ''
  }
}

function initializeStudio() {
  ensureActiveConversation()
  if (!settingsStore.settings && !settingsStore.isLoading) {
    void settingsStore.loadSettings()
  }
  void loadModelCatalog()
  void refreshImageTasks()
  scheduleScrollToBottom()
}

function activateStudio() {
  isStudioActive = true
  void refreshImageTasks()
  scheduleImagePoll()
  scheduleScrollToBottom()
}

function deactivateStudio() {
  isStudioActive = false
  clearImagePollTimer()
  clearImageRefreshTimer()
  stopTransientStudioUi()
  if (conversationsPersistTimer !== null) flushPersistConversations()
  if (conversationNoticesPersistTimer !== null) flushPersistConversationNotices()
  if (activeConversationPersistTimer !== null) flushPersistActiveConversationId()
}

function disposeStudio() {
  deactivateStudio()
  streamController?.abort()
}

onMounted(() => {
  initializeStudio()
})

onActivated(() => {
  if (!hasActivatedOnce) {
    hasActivatedOnce = true
    return
  }
  activateStudio()
})

onDeactivated(() => {
  deactivateStudio()
})

onBeforeUnmount(() => {
  disposeStudio()
})
</script>

<style scoped>
.studio-workspace {
  --studio-content-width: min(100%, 66rem);
  --ui-card-border: hsl(var(--border));
  --ui-card-bg: hsl(var(--card));
  --ui-panel-border: hsl(var(--border));
  --ui-panel-bg: hsl(var(--card));
  --ui-control-border: hsl(var(--border));
  --ui-control-bg: hsl(var(--background));
  --ui-control-hover-bg: hsl(var(--secondary));
  --ui-control-hover-border: hsl(var(--foreground) / 0.16);
  --ui-fg-strong: hsl(var(--foreground));
  --ui-fg-muted: hsl(var(--muted-foreground));
  --ui-fg-subtle: hsl(var(--muted-foreground));
  --ui-accent: hsl(var(--foreground));
  --ui-accent-soft: hsl(var(--secondary));
  --ui-accent-strong: hsl(var(--foreground));
  --ui-accent-border: hsl(var(--border));
  --ui-accent-border-strong: hsl(var(--foreground) / 0.22);
  --ui-accent-ring: hsl(var(--foreground) / 0.10);
  --ui-active-border: hsl(var(--foreground) / 0.22);
  --ui-active-bg: hsl(var(--secondary));
  --ui-active-fg: hsl(var(--foreground));
  --ui-divider: hsl(var(--border));
  --ui-danger-fg: rgb(220 38 38);
  --ui-danger-bg: rgb(254 242 242 / 0.88);
  --ui-danger-border: rgb(248 113 113 / 0.35);
  --ui-duration-fast: 150ms;
  --ui-duration-normal: 220ms;
  --ui-ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  display: grid;
  box-sizing: border-box;
  height: calc(100dvh - 11rem);
  min-height: 34rem;
  grid-template-columns: var(--studio-history-width) minmax(0, 1fr);
  gap: 0.75rem;
  overflow: hidden;
}

.studio-workspace.is-fullscreen {
  position: fixed;
  inset: 0;
  z-index: 180;
  height: 100dvh;
  min-height: 0;
  background: hsl(var(--background));
  padding: 1rem 1.25rem 1.25rem;
}

.studio-sidebar-wrap {
  position: relative;
  min-width: 0;
  min-height: 0;
}

.studio-history-resizer {
  display: none;
  position: absolute;
  top: 0;
  right: -0.5rem;
  bottom: 0;
  z-index: 10;
  width: 0.75rem;
  cursor: col-resize;
  border-radius: 999px;
  touch-action: none;
  transition: background 0.15s;
}

.studio-history-resizer::before {
  position: absolute;
  top: 0.75rem;
  bottom: 0.75rem;
  left: 50%;
  width: 2px;
  transform: translateX(-50%);
  border-radius: 999px;
  background: hsl(var(--foreground) / 0.42);
  content: '';
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
}

.studio-history-resizer:hover {
  background: transparent;
}

.studio-history-resizer:hover::before,
:global(.studio-resizing) .studio-history-resizer::before {
  background: hsl(var(--primary) / 0.58);
  opacity: 1;
}

.studio-main {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid hsl(var(--border));
  border-radius: 1.25rem;
  background: hsl(var(--card) / 0.88);
  box-shadow: 0 16px 44px -36px rgba(15, 23, 42, 0.45);
}

.chat-header-bar {
  display: flex;
  min-height: 3.5rem;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  border-bottom: 1px solid hsl(var(--border));
  background: hsl(var(--card) / 0.84);
  padding: 0.625rem 0.875rem;
}

.chat-header-title {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 0.5rem;
}

.chat-header-name {
  min-width: 0;
  max-width: min(32rem, 48vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: hsl(var(--foreground));
  font-size: 0.875rem;
  font-weight: 700;
}

.chat-header-subtitle {
  margin-top: 0.125rem;
  color: hsl(var(--muted-foreground));
  font-size: 0.75rem;
  line-height: 1rem;
}

.chat-header-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 0.375rem;
}

.chat-header-action-button,
.chat-header-icon {
  border-radius: 0.75rem;
}

:global(.studio-resizing) {
  cursor: col-resize;
  user-select: none;
}

@media (min-width: 1024px) {
  .studio-history-resizer {
    display: block;
  }
}

@media (max-width: 1023px) {
  .studio-workspace {
    height: calc(100dvh - 9.5rem);
    min-height: 28rem;
    grid-template-columns: minmax(0, 1fr);
  }

  .studio-sidebar-wrap {
    display: none;
  }

  .studio-workspace.is-fullscreen {
    height: 100dvh;
  }

  .chat-header-name {
    max-width: 42vw;
  }

}
</style>
