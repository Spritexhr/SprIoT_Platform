<template>
  <div class="auth-page">
    <div class="auth-card">
      <!-- Logo 区域 -->
      <div class="auth-header">
        <div class="auth-logo">
          <svg viewBox="0 0 40 40" width="40" height="40">
            <rect x="2" y="2" width="36" height="36" rx="8" fill="var(--iot-color-primary)" />
            <path d="M12 20h16M20 12v16M12 14l4 4-4 4M24 14l4 4-4 4" stroke="#fff" stroke-width="2"
              stroke-linecap="round" fill="none" />
          </svg>
        </div>
        <h1 class="auth-title">创建账号</h1>
        <p class="auth-subtitle">注册 SprIoT_Platform 账号</p>
      </div>

      <!-- 注册表单 -->
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        size="large"
        @submit.prevent="handleRegister"
      >
        <el-form-item label="用户名" prop="username">
          <el-input
            v-model="form.username"
            placeholder="请输入用户名"
            :prefix-icon="User"
            clearable
          />
        </el-form-item>

        <el-form-item label="邮箱" prop="email">
          <el-input
            v-model="form.email"
            placeholder="请输入邮箱（选填）"
            :prefix-icon="Message"
            clearable
          />
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码（至少8位）"
            :prefix-icon="Lock"
            show-password
          />
        </el-form-item>

        <el-form-item label="确认密码" prop="password2">
          <el-input
            v-model="form.password2"
            type="password"
            placeholder="请再次输入密码"
            :prefix-icon="Lock"
            show-password
            @keyup.enter="handleRegister"
          />
        </el-form-item>

        <el-form-item>
          <el-button
            type="primary"
            :loading="loading"
            class="auth-submit-btn"
            @click="handleRegister"
          >
            注 册
          </el-button>
        </el-form-item>
      </el-form>

      <!-- 底部链接 -->
      <div class="auth-footer">
        <span>已有账号？</span>
        <router-link to="/login" class="auth-link">返回登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, Message } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { register } from '@/api/auth'

const router = useRouter()

const formRef = ref(null)
const loading = ref(false)

const form = reactive({
  username: '',
  email: '',
  password: '',
  password2: '',
})

const validatePass2 = (rule, value, callback) => {
  if (value !== form.password) {
    callback(new Error('两次输入的密码不一致'))
  } else {
    callback()
  }
}

const rules = {
  username: [
    { required: true, message: '请输入用户名', trigger: 'blur' },
    { min: 3, max: 20, message: '用户名长度为 3 ~ 20 个字符', trigger: 'blur' },
  ],
  email: [
    { type: 'email', message: '请输入正确的邮箱格式', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请输入密码', trigger: 'blur' },
    { min: 8, message: '密码至少 8 个字符', trigger: 'blur' },
  ],
  password2: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validatePass2, trigger: 'blur' },
  ],
}

async function handleRegister() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await register({
      username: form.username,
      email: form.email,
      password: form.password,
      password2: form.password2,
    })
    ElMessage.success('注册成功，请登录')
    router.push('/login')
  } catch (error) {
    const detail = error.response?.data?.detail
    ElMessage.error(detail || '注册失败，请重试')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: #F5F0EB;
  background-image:
    radial-gradient(ellipse at 80% 10%, rgba(217, 119, 87, 0.08) 0%, transparent 60%),
    radial-gradient(ellipse at 20% 90%, rgba(76, 175, 130, 0.06) 0%, transparent 50%);
  padding: var(--iot-spacing-lg);
  position: relative;
}

.auth-card {
  width: 100%;
  max-width: 420px;
  background: #FDFCFB;
  border-radius: var(--iot-radius-xl);
  padding: 40px 36px;
  box-shadow: 0 4px 32px rgba(26, 23, 20, 0.10), 0 1px 4px rgba(26, 23, 20, 0.06);
  border: 1px solid #EDE8E0;
  animation: slide-up 0.35s ease-out;
}

@keyframes slide-up {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}

.auth-header {
  text-align: center;
  margin-bottom: 28px;
}

.auth-logo {
  display: flex;
  justify-content: center;
  margin-bottom: 16px;
}

.auth-title {
  font-size: var(--iot-font-size-xl);
  font-weight: 700;
  color: #1A1714;
  margin: 0 0 8px;
  letter-spacing: -0.02em;
}

.auth-subtitle {
  font-size: var(--iot-font-size-base);
  color: #8B7B6B;
  margin: 0;
}

.auth-submit-btn {
  width: 100%;
  height: 44px;
  font-size: var(--iot-font-size-base);
  font-weight: 600;
  border-radius: var(--iot-radius-base) !important;
  margin-top: 8px;
  background: #D97757 !important;
  border-color: #D97757 !important;
  letter-spacing: 0.03em;
  transition: transform 0.2s, box-shadow 0.2s, background 0.15s !important;
}

.auth-submit-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  background: #C4643E !important;
  border-color: #C4643E !important;
  box-shadow: 0 4px 16px rgba(217, 119, 87, 0.35) !important;
}

.auth-footer {
  text-align: center;
  margin-top: 24px;
  font-size: var(--iot-font-size-sm);
  color: #8B7B6B;
}

.auth-link {
  color: #D97757;
  text-decoration: none;
  font-weight: 600;
  margin-left: 4px;
  transition: color 0.15s;
}

.auth-link:hover {
  color: #C4643E;
  text-decoration: underline;
}

:global(html.dark) .auth-page {
  background-color: #1A1714;
  background-image:
    radial-gradient(ellipse at 80% 10%, rgba(217, 119, 87, 0.06) 0%, transparent 60%),
    radial-gradient(ellipse at 20% 90%, rgba(76, 175, 130, 0.04) 0%, transparent 50%);
}

:global(html.dark) .auth-card {
  background: #252118;
  border-color: #3D352D;
  box-shadow: 0 4px 32px rgba(0, 0, 0, 0.4), 0 1px 4px rgba(0, 0, 0, 0.3);
}

:global(html.dark) .auth-title {
  color: #F5F0EB;
}

/* 移动端适配 */
@media (max-width: 480px) {
  .auth-page {
    padding: var(--iot-spacing-md);
    align-items: center;
  }

  .auth-card {
    padding: 28px 20px;
    border-radius: var(--iot-radius-lg);
  }

  .auth-logo svg {
    width: 32px;
    height: 32px;
  }

  .auth-title {
    font-size: var(--iot-font-size-lg);
  }

  .auth-subtitle {
    font-size: var(--iot-font-size-sm);
  }
}
</style>
