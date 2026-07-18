<template>
  <main
    class="auth-shell"
    :aria-labelledby="headingId"
    :aria-describedby="`${headingId}-description`"
  >
    <div class="auth-shell__ambient" aria-hidden="true">
      <span class="auth-shell__orb auth-shell__orb--primary" />
      <span class="auth-shell__orb auth-shell__orb--secondary" />
    </div>

    <section class="auth-shell__card">
      <header class="auth-shell__header">
        <div class="auth-shell__mark" aria-hidden="true">
          <svg viewBox="0 0 40 40" focusable="false">
            <path d="M12 20h16M20 12v16" />
            <path d="m12 14 4 4-4 4M24 14l4 4-4 4" />
          </svg>
        </div>

        <h1 :id="headingId" class="auth-shell__title">{{ title }}</h1>
        <p :id="`${headingId}-description`" class="auth-shell__subtitle">
          {{ subtitle }}
        </p>
      </header>

      <div class="auth-shell__body">
        <slot />
      </div>

      <footer v-if="$slots.footer" class="auth-shell__footer">
        <slot name="footer" />
      </footer>
    </section>
  </main>
</template>

<script setup>
defineProps({
  title: {
    type: String,
    required: true,
  },
  subtitle: {
    type: String,
    required: true,
  },
  headingId: {
    type: String,
    default: 'auth-page-heading',
  },
})
</script>

<style scoped>
.auth-shell {
  position: relative;
  isolation: isolate;
  display: grid;
  width: 100%;
  min-height: 100vh;
  min-height: 100svh;
  place-items: center;
  overflow: hidden;
  padding: clamp(var(--iot-spacing-md), 5vw, var(--iot-spacing-2xl));
  background:
    radial-gradient(circle at 14% 12%, var(--iot-color-primary-bg), transparent 34rem),
    radial-gradient(circle at 88% 86%, var(--iot-color-success-bg), transparent 30rem),
    linear-gradient(145deg, var(--iot-bg-page), var(--iot-bg-page-deep));
}

.auth-shell::before {
  position: absolute;
  z-index: -1;
  width: min(68vw, 52rem);
  aspect-ratio: 1;
  border: 1px solid var(--iot-border-color-lighter);
  border-radius: var(--iot-radius-round);
  background: color-mix(in srgb, var(--iot-material-thin) 58%, transparent);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
  content: '';
  opacity: 0.72;
  transform: translate(34vw, -32vh);
}

.auth-shell__ambient {
  position: absolute;
  z-index: -2;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.auth-shell__orb {
  position: absolute;
  display: block;
  border-radius: var(--iot-radius-round);
  filter: blur(var(--iot-material-blur-lg));
  opacity: 0.7;
}

.auth-shell__orb--primary {
  top: -12rem;
  right: -8rem;
  width: min(54vw, 38rem);
  aspect-ratio: 1;
  background: var(--iot-color-primary-soft);
}

.auth-shell__orb--secondary {
  bottom: -13rem;
  left: -9rem;
  width: min(48vw, 32rem);
  aspect-ratio: 1;
  background: var(--iot-color-success-bg);
}

.auth-shell__card {
  position: relative;
  width: min(100%, 29rem);
  overflow: hidden;
  padding: clamp(1.75rem, 4vw, 2.75rem) clamp(1.4rem, 4vw, 2.5rem);
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-2xl);
  background:
    linear-gradient(
      145deg,
      color-mix(in srgb, var(--iot-material-regular) 90%, var(--iot-highlight-edge)),
      var(--iot-material-regular)
    );
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-lg);
  backdrop-filter: blur(var(--iot-material-blur-lg)) saturate(180%);
  -webkit-backdrop-filter: blur(var(--iot-material-blur-lg)) saturate(180%);
  animation: auth-shell-materialize 520ms var(--iot-ease-spring) both;
}

.auth-shell__card::before {
  position: absolute;
  inset: 0 10% auto;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--iot-highlight-edge), transparent);
  content: '';
  pointer-events: none;
}

.auth-shell__header {
  margin-bottom: var(--iot-spacing-xl);
  text-align: center;
}

.auth-shell__mark {
  display: grid;
  width: 3.35rem;
  height: 3.35rem;
  margin: 0 auto var(--iot-spacing-lg);
  place-items: center;
  border: 1px solid color-mix(in srgb, var(--iot-color-primary-light) 62%, transparent);
  border-radius: 1.05rem;
  color: var(--iot-text-inverse);
  background: linear-gradient(145deg, var(--iot-color-primary-light), var(--iot-color-primary-dark));
  box-shadow:
    inset 0 1px 0 color-mix(in srgb, var(--iot-text-inverse) 36%, transparent),
    0 10px 24px var(--iot-color-primary-soft);
}

.auth-shell__mark svg {
  width: 2rem;
  height: 2rem;
  fill: none;
  stroke: currentColor;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 2;
}

.auth-shell__title {
  margin: 0;
  color: var(--iot-text-primary);
  font-family: var(--iot-font-display);
  font-size: var(--iot-font-size-xl);
  font-weight: 680;
  letter-spacing: -0.035em;
  line-height: 1.12;
}

.auth-shell__subtitle {
  max-width: 32ch;
  margin: var(--iot-spacing-xs) auto 0;
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-base);
  line-height: 1.5;
}

.auth-shell__body {
  position: relative;
}

.auth-shell__footer {
  margin-top: var(--iot-spacing-lg);
  padding-top: var(--iot-spacing-lg);
  border-top: 1px solid var(--iot-separator);
  color: var(--iot-text-secondary);
  font-size: var(--iot-font-size-sm);
  text-align: center;
}

.auth-shell__footer :deep(.auth-link) {
  display: inline-flex;
  min-height: 2rem;
  align-items: center;
  margin-left: var(--iot-spacing-2xs);
  border-radius: var(--iot-radius-xs);
  color: var(--iot-color-primary-dark);
  font-weight: 620;
  text-underline-offset: 0.2em;
}

.auth-shell__footer :deep(.auth-link:hover) {
  color: var(--iot-color-primary);
  text-decoration: underline;
}

.auth-shell__body :deep(.auth-form .el-form-item) {
  margin-bottom: 1.25rem;
}

.auth-shell__body :deep(.auth-form .el-form-item:last-of-type) {
  margin-bottom: 0;
}

.auth-shell__body :deep(.auth-form .el-form-item__label) {
  padding-bottom: 0.45rem;
  line-height: 1.25;
}

.auth-shell__body :deep(.auth-form .el-input__wrapper) {
  min-height: 2.9rem;
  padding-inline: var(--iot-spacing-sm);
}

.auth-shell__body :deep(.auth-submit-btn) {
  width: 100%;
  min-height: 2.9rem;
  margin-top: var(--iot-spacing-2xs);
  border-radius: var(--iot-radius-base) !important;
  font-size: var(--iot-font-size-base);
  font-weight: 620 !important;
  transition:
    transform var(--iot-transition-instant),
    background-color var(--iot-transition-fast),
    border-color var(--iot-transition-fast),
    box-shadow var(--iot-transition-fast) !important;
}

@media (hover: hover) and (pointer: fine) {
  .auth-shell__body :deep(.auth-submit-btn:hover:not(.is-disabled)) {
    box-shadow: 0 2px 3px var(--iot-color-primary-bg), 0 10px 24px var(--iot-color-primary-soft) !important;
    transform: translateY(-1px);
  }
}

.auth-shell__body :deep(.auth-submit-btn:active:not(.is-disabled)) {
  transform: scale(0.985);
}

.auth-shell__body :deep(.auth-form__status) {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  clip-path: inset(50%);
  white-space: nowrap;
}

@keyframes auth-shell-materialize {
  from {
    opacity: 0;
    filter: blur(5px);
    transform: translateY(14px) scale(0.985);
  }
  to {
    opacity: 1;
    filter: blur(0);
    transform: translateY(0) scale(1);
  }
}

@media screen and (max-width: 560px) {
  .auth-shell {
    padding: var(--iot-spacing-md);
  }

  .auth-shell::before {
    width: 34rem;
    transform: translate(18rem, -22rem);
  }

  .auth-shell__card {
    padding: 1.75rem 1.35rem;
    border-radius: var(--iot-radius-xl);
  }

  .auth-shell__header {
    margin-bottom: var(--iot-spacing-lg);
  }

  .auth-shell__mark {
    width: 3rem;
    height: 3rem;
    margin-bottom: var(--iot-spacing-md);
    border-radius: var(--iot-radius-base);
  }
}

@media screen and (max-height: 720px) and (min-width: 561px) {
  .auth-shell {
    place-items: start center;
    padding-block: var(--iot-spacing-md);
  }

  .auth-shell__card {
    padding-block: 1.75rem;
  }

  .auth-shell__header {
    margin-bottom: var(--iot-spacing-lg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .auth-shell__card {
    animation: none;
  }

  .auth-shell__body :deep(.auth-submit-btn:hover:not(.is-disabled)),
  .auth-shell__body :deep(.auth-submit-btn:active:not(.is-disabled)) {
    transform: none;
  }
}

@media (prefers-reduced-transparency: reduce) {
  .auth-shell {
    background: var(--iot-bg-page);
  }

  .auth-shell__ambient,
  .auth-shell::before {
    display: none;
  }

  .auth-shell__card {
    background: var(--iot-bg-card-solid);
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
}

@media (prefers-contrast: more) {
  .auth-shell__card {
    border-width: 2px;
    background: var(--iot-bg-card-solid);
    box-shadow: none;
  }
}
</style>
