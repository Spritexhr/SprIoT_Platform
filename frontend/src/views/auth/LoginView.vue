<template>
  <AuthShell
    title="SprIoT_Platform"
    subtitle="登录您的账号以继续"
    heading-id="login-heading"
  >
    <el-form
      ref="formRef"
      class="auth-form"
      :model="form"
      :rules="rules"
      label-position="top"
      size="large"
      aria-label="登录表单"
      :aria-busy="loading"
      @submit.prevent="handleLogin"
    >
      <el-form-item label="用户名" prop="username">
        <el-input
          v-model="form.username"
          name="username"
          autocomplete="username"
          aria-label="用户名"
          placeholder="请输入用户名"
          :prefix-icon="User"
          clearable
        />
      </el-form-item>

      <el-form-item label="密码" prop="password">
        <el-input
          v-model="form.password"
          name="password"
          type="password"
          autocomplete="current-password"
          aria-label="密码"
          placeholder="请输入密码"
          :prefix-icon="Lock"
          show-password
        />
      </el-form-item>

      <el-form-item>
        <el-button
          native-type="submit"
          type="primary"
          :loading="loading"
          class="auth-submit-btn"
        >
          登 录
        </el-button>
      </el-form-item>

      <p class="auth-form__status" role="status" aria-live="polite">
        {{ loading ? '正在登录，请稍候' : '' }}
      </p>
    </el-form>

    <template #footer>
      <div>
        <span>还没有账号？</span>
        <router-link to="/register" class="auth-link">立即注册</router-link>
      </div>
    </template>
  </AuthShell>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import AuthShell from '@/components/common/AuthShell.vue'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const userStore = useUserStore()

const formRef = ref(null)
const loading = ref(false)

const form = reactive({
  username: '',
  password: '',
})

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await userStore.login({
      username: form.username,
      password: form.password,
    })
    ElMessage.success('登录成功')
    const redirect = router.currentRoute.value.query.redirect || '/'
    router.push(redirect)
  } catch (error) {
    const detail = error.response?.data?.detail
    ElMessage.error(detail || '登录失败，请检查用户名和密码')
  } finally {
    loading.value = false
  }
}
</script>
