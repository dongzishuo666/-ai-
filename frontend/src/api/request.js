import axios from 'axios'
import { ElMessage } from 'element-plus'

const service = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

service.interceptors.response.use(
  (response) => {
    const res = response.data
    if (res.code === 200) {
      return res.data
    } else {
      ElMessage.error(res.message || '请求失败')
      return Promise.reject(new Error(res.message || '请求失败'))
    }
  },
  (error) => {
    const message = error.response?.data?.message || error.message || '网络连接失败'
    ElMessage.error(message)
    // 抛出带中文信息的 Error，让下游 error.message 能直接展示给用户
    // （原先抛 AxiosError，message 只会是 "Request failed with status code 409"）
    const wrapped = new Error(message)
    wrapped.code = error.response?.data?.code || error.code
    wrapped.status = error.response?.status
    wrapped.cause = error
    return Promise.reject(wrapped)
  }
)

export default service
