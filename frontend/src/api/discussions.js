import request from './request'

// 创建讨论
export function createDiscussion(data) {
  return request({
    url: '/discussions/create',
    method: 'POST',
    data
  })
}

// 获取讨论详情
export function getDiscussionDetail(id) {
  return request({
    url: `/discussions/${id}`,
    method: 'GET'
  })
}

// 暂停讨论
export function pauseDiscussion(id) {
  return request({
    url: `/discussions/${id}/pause`,
    method: 'POST'
  })
}

// 恢复讨论
export function resumeDiscussion(id) {
  return request({
    url: `/discussions/${id}/resume`,
    method: 'POST'
  })
}

// 终止讨论
export function terminateDiscussion(id) {
  return request({
    url: `/discussions/${id}/terminate`,
    method: 'POST'
  })
}

// 获取讨论列表
export function getDiscussionList(params) {
  return request({
    url: '/discussions',
    method: 'GET',
    params
  })
}

// 删除讨论
export function deleteDiscussion(id) {
  return request({
    url: `/discussions/${id}`,
    method: 'DELETE'
  })
}
