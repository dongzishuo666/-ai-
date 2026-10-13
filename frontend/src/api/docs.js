import request from './request'

// 上传文档
export function uploadDoc(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: '/docs/upload',
    method: 'POST',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

// 提交处理
export function processDoc(data) {
  return request({
    url: '/docs/process',
    method: 'POST',
    data
  })
}

// 获取任务详情
export function getDocDetail(id) {
  return request({
    url: `/docs/${id}`,
    method: 'GET'
  })
}

// 保存结果
export function saveDoc(id) {
  return request({
    url: `/docs/${id}/save`,
    method: 'POST'
  })
}

// 获取历史列表
export function getDocList(params) {
  return request({
    url: '/docs',
    method: 'GET',
    params
  })
}

// 删除任务
export function deleteDoc(id) {
  return request({
    url: `/docs/${id}`,
    method: 'DELETE'
  })
}

// 导出成品文件（format: docx | md）
// 下载走原生 fetch + Blob，因此这里返回的是完整 URL 而非 request 实例
export function exportDocUrl(id, format = 'docx') {
  return `/api/docs/${id}/export?format=${format}`
}
