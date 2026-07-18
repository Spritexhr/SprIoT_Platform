<template>
  <header class="app-header">
    <!-- 左侧：移动端汉堡按钮 + 面包屑 -->
    <div class="app-header__left">
      <button
        type="button"
        class="mobile-menu-btn"
        :aria-label="ls.locale === 'zh' ? '打开导航菜单' : 'Open navigation'"
        @click="$emit('toggle-drawer')"
      >
        <el-icon :size="19"><Operation /></el-icon>
      </button>
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/' }">{{ ls.t('header.home') }}</el-breadcrumb-item>
        <template v-if="breadcrumbItems.length">
          <el-breadcrumb-item
            v-for="(item, idx) in breadcrumbItems"
            :key="idx"
            :to="item.to ? { path: item.to } : undefined"
          >
            {{ item.title }}
          </el-breadcrumb-item>
        </template>
        <el-breadcrumb-item v-else-if="currentTitle">{{ currentTitle }}</el-breadcrumb-item>
      </el-breadcrumb>
    </div>

    <!-- 右侧工具栏 -->
    <div class="app-header__right">
      <!-- MQTT 状态 -->
      <div
        class="mqtt-status"
        :class="mqttConnected ? 'mqtt-status--online' : 'mqtt-status--offline'"
        :title="mqttConnected ? ls.t('header.mqttConnected') : ls.t('header.mqttDisconnected')"
        role="status"
        aria-live="polite"
      >
        <span
          class="iot-status-dot"
          :class="mqttConnected ? 'iot-status-dot--online' : 'iot-status-dot--offline'"
        ></span>
        <span class="mqtt-label">MQTT</span>
      </div>

      <!-- 外观主题选择 -->
      <el-tooltip :content="ls.t('header.theme')" placement="bottom">
        <el-dropdown trigger="click" @command="handleColorTheme">
          <button type="button" class="header-action-btn" :aria-label="ls.t('header.theme')">
            <el-icon :size="18"><Brush /></el-icon>
          </button>
          <template #dropdown>
            <el-dropdown-menu>
              <div class="theme-menu-title">{{ ls.t('header.theme') }}</div>
              <el-dropdown-item
                command="apple"
                :class="{ 'is-active-item': appStore.colorTheme === 'apple' }"
              >
                <span class="theme-dot theme-dot--apple"></span>
                {{ ls.t('header.themeApple') }}
                <el-icon v-if="appStore.colorTheme === 'apple'" class="check-icon"><Check /></el-icon>
              </el-dropdown-item>
              <el-dropdown-item
                command="classic"
                :class="{ 'is-active-item': appStore.colorTheme === 'classic' }"
              >
                <span class="theme-dot theme-dot--classic"></span>
                {{ ls.t('header.themeClassic') }}
                <el-icon v-if="appStore.colorTheme === 'classic'" class="check-icon"><Check /></el-icon>
              </el-dropdown-item>
              <el-divider style="margin: 6px 0;" />
              <div class="theme-menu-title">{{ isDark ? ls.t('header.toLight') : ls.t('header.toDark') }}</div>
              <el-dropdown-item command="toggle-dark">
                <el-icon><Moon v-if="!isDark" /><Sunny v-else /></el-icon>
                {{ isDark ? ls.t('header.toLight') : ls.t('header.toDark') }}
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-tooltip>

      <!-- 语言切换 -->
      <el-tooltip :content="ls.t('header.lang')" placement="bottom">
        <button type="button" class="header-action-btn lang-btn" :aria-label="ls.t('header.lang')" @click="ls.toggleLocale()">
          <span class="lang-text">{{ ls.locale === 'zh' ? 'EN' : '中' }}</span>
        </button>
      </el-tooltip>

      <!-- 全屏切换 -->
      <el-tooltip :content="ls.t('header.fullscreen')" placement="bottom">
        <button type="button" class="header-action-btn" :aria-label="ls.t('header.fullscreen')" @click="toggleFullscreen">
          <el-icon :size="18"><FullScreen /></el-icon>
        </button>
      </el-tooltip>

      <!-- 用户下拉菜单 -->
      <el-dropdown trigger="click" @command="handleUserCommand">
        <button type="button" class="user-menu-btn" :aria-label="ls.locale === 'zh' ? '打开用户菜单' : 'Open user menu'">
          <el-avatar :size="28" class="user-avatar">{{ avatarText }}</el-avatar>
          <span class="user-name">{{ userStore.username || ls.t('header.currentUser') }}</span>
          <el-icon :size="12"><ArrowDown /></el-icon>
        </button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>
              <el-icon><User /></el-icon>
              {{ userStore.username }}
            </el-dropdown-item>
            <el-dropdown-item divided command="profile">
              <el-icon><Setting /></el-icon>
              {{ ls.t('header.profile') }}
            </el-dropdown-item>
            <el-dropdown-item command="password">
              <el-icon><Lock /></el-icon>
              {{ ls.t('header.changePassword') }}
            </el-dropdown-item>
            <el-dropdown-item divided command="logout">
              <el-icon><SwitchButton /></el-icon>
              {{ ls.t('header.logout') }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <!-- 个人信息弹窗 -->
    <el-dialog v-model="profileDialogVisible" :title="ls.t('header.profileTitle')" width="460px" destroy-on-close>
      <el-form :model="profileForm" label-width="80px" size="default">
        <el-form-item :label="ls.t('header.username')">
          <el-input :model-value="profileForm.username" disabled />
        </el-form-item>
        <el-form-item :label="ls.t('header.email')">
          <el-input v-model="profileForm.email" :placeholder="ls.t('header.email')" />
        </el-form-item>
        <el-form-item :label="ls.t('header.lastName')">
          <el-input v-model="profileForm.last_name" :placeholder="ls.t('header.lastName')" />
        </el-form-item>
        <el-form-item :label="ls.t('header.firstName')">
          <el-input v-model="profileForm.first_name" :placeholder="ls.t('header.firstName')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="profileDialogVisible = false">{{ ls.t('header.cancel') }}</el-button>
        <el-button type="primary" :loading="profileLoading" @click="handleUpdateProfile">
          {{ ls.t('header.save') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 修改密码弹窗 -->
    <el-dialog v-model="passwordDialogVisible" :title="ls.t('header.pwdTitle')" width="460px" destroy-on-close>
      <el-form ref="pwdFormRef" :model="passwordForm" :rules="passwordRules" label-width="100px" size="default">
        <el-form-item :label="ls.t('header.oldPwd')" prop="old_password">
          <el-input v-model="passwordForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item :label="ls.t('header.newPwd')" prop="new_password">
          <el-input v-model="passwordForm.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item :label="ls.t('header.confirmPwd')" prop="new_password2">
          <el-input v-model="passwordForm.new_password2" type="password" show-password />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="passwordDialogVisible = false">{{ ls.t('header.cancel') }}</el-button>
        <el-button type="primary" :loading="passwordLoading" @click="handleChangePassword">
          {{ ls.t('header.confirm') }}
        </el-button>
      </template>
    </el-dialog>
  </header>
</template>

<script setup>
import { ref, reactive, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'
import { useLocaleStore } from '@/stores/locale'
import {
  Operation, Moon, Sunny, FullScreen, ArrowDown,
  User, Setting, Lock, SwitchButton, Brush, Check,
} from '@element-plus/icons-vue'
import { updateUserProfile, changePassword } from '@/api/auth'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useWebSocket, buildWsUrl } from '@/composables/useWebSocket'

defineEmits(['toggle-drawer'])

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const userStore = useUserStore()
const ls = useLocaleStore()

const isDark = computed(() => appStore.theme === 'dark')
const currentTitle = computed(() => route.meta.title || '')
const breadcrumbItems = computed(() => {
  const items = route.meta.breadcrumb
  return Array.isArray(items) ? items : []
})
const avatarText = computed(() => (userStore.username || 'U').charAt(0).toUpperCase())

// ==================== MQTT 状态实时推送 ====================
// 订阅 /ws/system/mqtt/，建连时 consumer 立刻发一次当前状态，后续 broker 连/断会立即推
const mqttConnected = ref(false)

useWebSocket(
  () => buildWsUrl('/ws/system/mqtt/'),
  {
    'system.mqtt': (data) => {
      mqttConnected.value = !!data?.is_connected
    },
  },
)

// ==================== 全屏 ====================
function toggleFullscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen()
  } else {
    document.documentElement.requestFullscreen()
  }
}

// ==================== 主题切换 ====================
function handleColorTheme(cmd) {
  if (cmd === 'toggle-dark') {
    appStore.toggleTheme()
  } else {
    appStore.setColorTheme(cmd)
  }
}

// ==================== 用户菜单命令 ====================
function handleUserCommand(command) {
  if (command === 'profile') openProfileDialog()
  else if (command === 'password') openPasswordDialog()
  else if (command === 'logout') handleLogout()
}

// ==================== 个人信息 ====================
const profileDialogVisible = ref(false)
const profileLoading = ref(false)
const profileForm = reactive({ username: '', email: '', first_name: '', last_name: '' })

function openProfileDialog() {
  const info = userStore.userInfo
  if (info) {
    profileForm.username = info.username
    profileForm.email = info.email || ''
    profileForm.first_name = info.first_name || ''
    profileForm.last_name = info.last_name || ''
  }
  profileDialogVisible.value = true
}

async function handleUpdateProfile() {
  profileLoading.value = true
  try {
    await updateUserProfile({
      email: profileForm.email,
      first_name: profileForm.first_name,
      last_name: profileForm.last_name,
    })
    await userStore.fetchUserInfo()
    ElMessage.success(ls.locale === 'zh' ? '信息更新成功' : 'Profile updated')
    profileDialogVisible.value = false
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || (ls.locale === 'zh' ? '更新失败' : 'Update failed'))
  } finally {
    profileLoading.value = false
  }
}

// ==================== 修改密码 ====================
const passwordDialogVisible = ref(false)
const passwordLoading = ref(false)
const pwdFormRef = ref(null)
const passwordForm = reactive({ old_password: '', new_password: '', new_password2: '' })

const passwordRules = {
  old_password: [{ required: true, message: ls.locale === 'zh' ? '请输入当前密码' : 'Required', trigger: 'blur' }],
  new_password: [
    { required: true, message: ls.locale === 'zh' ? '请输入新密码' : 'Required', trigger: 'blur' },
    { min: 8, message: ls.locale === 'zh' ? '密码至少 8 个字符' : 'Min 8 characters', trigger: 'blur' },
  ],
  new_password2: [
    { required: true, message: ls.locale === 'zh' ? '请确认新密码' : 'Required', trigger: 'blur' },
    {
      validator: (rule, value, callback) => {
        if (value !== passwordForm.new_password) {
          callback(new Error(ls.locale === 'zh' ? '两次输入的密码不一致' : 'Passwords do not match'))
        } else {
          callback()
        }
      },
      trigger: 'blur',
    },
  ],
}

function openPasswordDialog() {
  passwordForm.old_password = ''
  passwordForm.new_password = ''
  passwordForm.new_password2 = ''
  passwordDialogVisible.value = true
}

async function handleChangePassword() {
  const valid = await pwdFormRef.value.validate().catch(() => false)
  if (!valid) return
  passwordLoading.value = true
  try {
    await changePassword({
      old_password: passwordForm.old_password,
      new_password: passwordForm.new_password,
      new_password2: passwordForm.new_password2,
    })
    ElMessage.success(ls.locale === 'zh' ? '密码修改成功，请重新登录' : 'Password changed, please login again')
    passwordDialogVisible.value = false
    userStore.logout()
    router.push('/login')
  } catch (error) {
    ElMessage.error(error.response?.data?.detail || (ls.locale === 'zh' ? '修改失败' : 'Failed'))
  } finally {
    passwordLoading.value = false
  }
}

// ==================== 退出登录 ====================
async function handleLogout() {
  try {
    await ElMessageBox.confirm(
      ls.t('header.confirmLogout'),
      ls.t('header.hint'),
      {
        confirmButtonText: ls.t('header.logoutOk'),
        cancelButtonText: ls.t('header.logoutCancel'),
        type: 'warning',
      }
    )
    userStore.logout()
    router.push('/login')
    ElMessage.success(ls.locale === 'zh' ? '已退出登录' : 'Logged out')
  } catch {
    // 取消
  }
}
</script>

<style scoped>
.app-header {
  position: sticky;
  top: 0;
  z-index: 999;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--iot-header-height);
  padding: 0 var(--iot-spacing-lg);
  background: var(--iot-bg-header);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
  backdrop-filter: blur(var(--iot-material-blur)) saturate(175%);
  -webkit-backdrop-filter: blur(var(--iot-material-blur)) saturate(175%);
  transition: background-color var(--iot-transition-base), box-shadow var(--iot-transition-base);
}

.app-header::after {
  position: absolute;
  right: 0;
  bottom: -10px;
  left: 0;
  height: 10px;
  pointer-events: none;
  content: '';
  background: linear-gradient(to bottom, color-mix(in srgb, var(--iot-bg-page-deep) 16%, transparent), transparent);
}

.app-header__left {
  display: flex;
  align-items: center;
  gap: var(--iot-spacing-md);
  min-width: 0;
}

.mobile-menu-btn {
  display: none;
  width: 40px;
  height: 40px;
  flex: none;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: var(--iot-radius-base);
  background: transparent;
  color: var(--iot-text-regular);
  cursor: pointer;
  transition: color var(--iot-transition-fast), background-color var(--iot-transition-fast), transform var(--iot-transition-instant);
}

.mobile-menu-btn:hover {
  background: var(--iot-material-thin);
  color: var(--iot-color-primary);
}

.mobile-menu-btn:active { transform: scale(0.94); }

.app-header__left :deep(.el-breadcrumb) {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.app-header__right {
  display: flex;
  align-items: center;
  gap: 2px;
}

.mqtt-status {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  margin-right: 6px;
  padding: 4px 11px;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-pill);
  background: var(--iot-material-thin);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge);
  font-size: var(--iot-font-size-xs);
  cursor: default;
}

.mqtt-label {
  font-weight: 600;
  transition: color var(--iot-transition-fast);
}

.mqtt-status--online .mqtt-label { color: var(--iot-color-success); }
.mqtt-status--offline .mqtt-label { color: var(--iot-text-secondary); }

.header-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border: none;
  border-radius: var(--iot-radius-base);
  background: transparent;
  color: var(--iot-text-secondary);
  cursor: pointer;
  transition: color var(--iot-transition-fast), background-color var(--iot-transition-fast), transform var(--iot-transition-instant);
}

.header-action-btn:hover {
  background: var(--iot-material-thin);
  color: var(--iot-color-primary);
}

.header-action-btn:active { transform: scale(0.94); }

.lang-btn {
  font-weight: 700;
  letter-spacing: 0.02em;
}

.lang-text {
  font-size: 12px;
  font-weight: 700;
}

.theme-menu-title {
  padding: 5px 12px 4px;
  color: var(--iot-text-secondary);
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.055em;
  text-transform: uppercase;
}

.theme-dot {
  display: inline-block;
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  margin-right: 6px;
  border-radius: var(--iot-radius-round);
}

.theme-dot--apple {
  border: 1px solid rgba(65, 54, 46, 0.14);
  background: linear-gradient(135deg, #fbfaf8 0 48%, #d96845 50% 100%);
}

.theme-dot--classic {
  border: 1px solid rgba(22, 113, 217, 0.2);
  background: linear-gradient(135deg, #f4f8ff 0 48%, #1671d9 50% 100%);
}

.check-icon {
  margin-left: auto;
  color: var(--iot-color-primary);
  font-size: 14px;
}

:deep(.is-active-item) {
  color: var(--iot-color-primary) !important;
  background-color: var(--iot-color-primary-bg) !important;
}

.user-menu-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 42px;
  margin-left: 4px;
  padding: 4px 12px 4px 4px;
  border: 1px solid var(--iot-border-color-light);
  border-radius: var(--iot-radius-pill);
  background: var(--iot-material-regular);
  color: var(--iot-text-regular);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-xs);
  cursor: pointer;
  transition: border-color var(--iot-transition-fast), background-color var(--iot-transition-fast), box-shadow var(--iot-transition-fast), transform var(--iot-transition-instant);
}

.user-menu-btn:hover {
  border-color: color-mix(in srgb, var(--iot-color-primary) 34%, var(--iot-border-color-light));
  background: var(--iot-bg-card-hover);
  box-shadow: inset 0 1px 0 var(--iot-highlight-edge), var(--iot-shadow-sm);
}

.user-menu-btn:active { transform: scale(0.97); }

.user-avatar {
  background: linear-gradient(135deg, var(--iot-color-primary), var(--iot-color-primary-light));
  color: var(--iot-text-inverse);
  font-size: 13px;
  font-weight: 650;
}

.user-name {
  max-width: 100px;
  overflow: hidden;
  color: var(--iot-text-primary);
  font-size: var(--iot-font-size-sm);
  font-weight: 560;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 767px) {
  .mobile-menu-btn { display: flex; }
  .user-name { display: none; }
  .mqtt-status { display: none; }
  .app-header { padding: 0 var(--iot-spacing-sm); }
  .app-header__left { gap: var(--iot-spacing-xs); }
  .app-header__left :deep(.el-breadcrumb__item:not(:last-child)) { display: none; }
  .header-action-btn { width: 38px; height: 38px; }
  .user-menu-btn { min-height: 40px; padding-right: 7px; }
}

@media (max-width: 480px) {
  .app-header__left :deep(.el-breadcrumb) { max-width: 112px; }
  .app-header__right > :nth-child(4) { display: none; }
}

@media (prefers-reduced-transparency: reduce) {
  .app-header {
    backdrop-filter: none;
    -webkit-backdrop-filter: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .mobile-menu-btn:active,
  .header-action-btn:active,
  .user-menu-btn:active { transform: none; }
}
</style>
