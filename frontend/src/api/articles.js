import request from './request'

// 创建文章
export function createArticle(data) {
  return request({
    url: '/articles/create',
    method: 'POST',
    data
  })
}

// 获取文章详情
export function getArticleDetail(id) {
  return request({
    url: `/articles/${id}`,
    method: 'GET'
  })
}

// 获取文章列表
export function getArticleList(params) {
  return request({
    url: '/articles',
    method: 'GET',
    params
  })
}

// 删除文章
export function deleteArticle(id) {
  return request({
    url: `/articles/${id}`,
    method: 'DELETE'
  })
}
