<template>
  <AuthShell
    title="创建账号"
    subtitle="注册 SprIoT_Platform 账号"
    heading-id="register-heading"
  >
    <el-form
      ref="formRef"
      class="auth-form"
      :model="form"
      :rules="rules"
      label-position="top"
      size="large"
      aria-label="注册表单"
      :aria-busy="loading"
      @submit.prevent="handleRegister"
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

      <el-form-item label="邮箱" prop="email">
        <el-input
          v-model="form.email"
          name="email"
          autocomplete="email"
          inputmode="email"
          aria-label="邮箱"
          placeholder="请输入邮箱（选填）"
          :prefix-icon="Message"
          clearable
        />
      </el-form-item>

      <el-form-item label="密码" prop="password">
        <el-input
          v-model="form.password"
          name="new-password"
          type="password"
          autocomplete="new-password"
          aria-label="密码"
          placeholder="请输入密码（至少8位）"
          :prefix-icon="Lock"
          show-password
        />
      </el-form-item>

      <el-form-item label="确认密码" prop="password2">
        <el-input
          v-model="form.password2"
          name="confirm-password"
          type="password"
          autocomplete="new-password"
          aria-label="确认密码"
          placeholder="请再次输入密码"
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
          注 册
        </el-button>
      </el-form-item>

      <p class="auth-form__status" role="status" aria-live="polite">
        {{ loading ? '正在注册，请稍候' : '' }}
      </p>
    </el-form>

    <template #footer>
      <div>
        <span>已有账号？</span>
        <router-link to="/login" class="auth-link">返回登录</router-link>
      </div>
    </template>
  </AuthShell>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { User, Lock, Message } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { register } from '@/api/auth'
import AuthShell from '@/components/common/AuthShell.vue'

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
