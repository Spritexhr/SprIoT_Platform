<template>
  <main class="app-main">
    <router-view v-slot="{ Component }">
      <transition name="fade-transform" appear>
        <component :is="Component" />
      </transition>
    </router-view>
  </main>
</template>

<script setup>
</script>

<style scoped>
.app-main {
  --app-main-padding-inline: clamp(22px, 2.25vw, 38px);
  --app-main-padding-top: clamp(22px, 2.25vw, 38px);
  --app-main-padding-bottom: max(clamp(28px, 3vw, 48px), env(safe-area-inset-bottom));
  position: relative;
  width: 100%;
  max-width: var(--iot-content-max-width);
  min-height: calc(100vh - var(--iot-header-height));
  margin: 0 auto;
  padding: var(--app-main-padding-top) var(--app-main-padding-inline) var(--app-main-padding-bottom);
  transition: padding var(--iot-transition-base);
}

@media (max-width: 767px) {
  .app-main {
    --app-main-padding-inline: 14px;
    --app-main-padding-top: 20px;
    --app-main-padding-bottom: max(28px, env(safe-area-inset-bottom));
  }
}

@media (prefers-reduced-motion: reduce) {
  .app-main { transition: none; }
}
</style>
