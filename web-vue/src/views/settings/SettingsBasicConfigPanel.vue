<template>
  <FormSection title="基础配置">
    <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
      <FormField label="账号刷新间隔">
        <template #label-extra>
          <HelpTip text="单位分钟，控制账号自动刷新频率。" />
        </template>
        <Input
          :model-value="refreshAccountIntervalField.input.value"
          type="number"
          block
          placeholder="5"
          @update:model-value="refreshAccountIntervalField.update"
        />
      </FormField>

      <FormField label="图片访问地址">
        <template #label-extra>
          <HelpTip text="用于生成图片结果的访问前缀地址。" />
        </template>
        <Input
          v-model.trim="settings.base_url"
          block
          placeholder="https://example.com"
        />
      </FormField>

      <FormField label="默认出口" class="md:col-span-2">
        <template #label-extra>
          <HelpTip text="账号个人代理、账号组代理优先于默认出口。可填写代理 URL、direct 或 group:代理组ID；完整选择可到代理管理维护。" />
        </template>
        <div class="flex flex-col gap-2 sm:flex-row">
          <Input
            v-model.trim="settings.proxy"
            block
            root-class="font-mono"
            placeholder="http://127.0.0.1:7890"
            @update:model-value="$emit('clearProxyTestResult')"
          />
          <Button
            size="sm"
            variant="outline"
            root-class="shrink-0"
            :disabled="proxyBusy === 'test'"
            @click="$emit('testDefaultProxy')"
          >
            {{ proxyBusy === 'test' ? '测试中...' : '测试出口' }}
          </Button>
        </div>
        <div v-if="proxyTestResult" class="mt-2 rounded-xl border border-border bg-background px-3 py-2 text-xs">
          <p :class="proxyTestResult.ok ? 'text-emerald-600' : 'text-rose-600'">
            {{ proxyTestResult.ok ? `出口可用：HTTP ${proxyTestResult.status}，${proxyTestResult.latency_ms} ms` : `出口不可用：${proxyTestResult.error || '未知错误'}` }}
          </p>
        </div>
      </FormField>

      <FormField label="图片自动清理">
        <template #label-extra>
          <HelpTip text="单位小时。保存后会立即唤醒后台清理任务。" />
        </template>
        <Input
          :model-value="imageRetentionHoursField.input.value"
          type="number"
          block
          placeholder="360"
          @update:model-value="imageRetentionHoursField.update"
        />
      </FormField>

      <FormField label="日志自动清理">
        <template #label-extra>
          <HelpTip text="单位小时。清理后台调用日志数据库中的过期记录。" />
        </template>
        <Input
          :model-value="logRetentionHoursField.input.value"
          type="number"
          block
          placeholder="720"
          @update:model-value="logRetentionHoursField.update"
        />
      </FormField>

      <FormField label="图片轮询超时">
        <template #label-extra>
          <HelpTip text="单位秒，等待上游图片结果的最长时间。" />
        </template>
        <Input
          :model-value="imagePollTimeoutField.input.value"
          type="number"
          block
          placeholder="60"
          @update:model-value="imagePollTimeoutField.update"
        />
      </FormField>

      <FormField label="上游流超时">
        <template #label-extra>
          <HelpTip text="单位秒，限制 ChatGPT 生图 SSE 流最长等待时间。" />
        </template>
        <Input
          :model-value="imageStreamTimeoutField.input.value"
          type="number"
          block
          placeholder="80"
          @update:model-value="imageStreamTimeoutField.update"
        />
      </FormField>

      <FormField label="单账号图片并发">
        <template #label-extra>
          <HelpTip text="限制每个账号同时处理的图片请求数量。默认 1，可设置为 1–3。" />
        </template>
        <Input
          :model-value="imageAccountConcurrencyField.input.value"
          type="number"
          block
          placeholder="1"
          @update:model-value="imageAccountConcurrencyField.update"
        />
      </FormField>

    </div>
  </FormSection>
</template>

<script setup lang="ts">
import { Button, FormField, FormSection, HelpTip, Input } from 'nanocat-ui'
import type { ProxyTestResult } from '@/api/proxy'
import type { Settings } from '@/types/api'
import type { NumberSettingField } from '@/views/settings/useNumberSettingField'

defineProps<{
  settings: Settings
  refreshAccountIntervalField: NumberSettingField
  imageRetentionHoursField: NumberSettingField
  logRetentionHoursField: NumberSettingField
  imagePollTimeoutField: NumberSettingField
  imageStreamTimeoutField: NumberSettingField
  imageAccountConcurrencyField: NumberSettingField
  proxyBusy: string
  proxyTestResult: ProxyTestResult | null
}>()

defineEmits<{
  clearProxyTestResult: []
  testDefaultProxy: []
}>()
</script>
