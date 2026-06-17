<template>
  <div class="space-y-6">
    <PagePanel>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="ui-section-title">服务状态</p>
          <p class="mt-1 text-xs text-muted-foreground">
            最近更新：{{ updatedAt || '未获取' }}
          </p>
        </div>
        <div class="flex items-center gap-2">
          <Button
            tag="a"
            href="/#/public/uptime"
            target="_blank"
            size="sm"
            variant="outline"
          >
            打开监控页
          </Button>
        </div>
      </div>

      <div class="mt-6 grid gap-8 md:grid-cols-2">
        <ServiceStatusCard
          v-for="service in services"
          :key="service.key"
          :service="service"
        />
      </div>
    </PagePanel>
  </div>
</template>

<script setup lang="ts">
import { Button } from 'nanocat-ui'
import { PagePanel, ServiceStatusCard } from '@/components/ai'
import { useUptimeStatus } from '@/composables/useUptimeStatus'

const { services, updatedAt } = useUptimeStatus()
</script>
