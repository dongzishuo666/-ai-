# 部署「文档成品导出」功能：后端导出路由 + 前端导出按钮
# 用法：在 PowerShell 里执行  & "c:\Users\luka\Desktop\设计总面板\deploy_export.ps1"
$ErrorActionPreference = 'Continue'
$remote = 'root@123.207.74.83'
$root = 'c:\Users\luka\Desktop\设计总面板'

Write-Host "=== 1/4 打包后端 ==="
Set-Location "$root\backend"
tar -czf backend_app.tar.gz app
scp backend_app.tar.gz "${remote}:/tmp/backend_app.tar.gz"
scp "$root\_server_export_test.sh" "${remote}:/tmp/_server_export_test.sh"
Remove-Item backend_app.tar.gz
Write-Host "backend uploaded"

Write-Host "=== 2/4 服务端解压 + 装依赖 + 自测 + 重启 ==="
ssh $remote "cd /server/backend && tar -xzf /tmp/backend_app.tar.gz && rm -f /tmp/backend_app.tar.gz && bash /tmp/_server_export_test.sh"

Write-Host "=== 3/4 前端生产构建 ==="
Set-Location "$root\frontend"
npm run build:prod
Write-Host "build exit: $LASTEXITCODE"

Write-Host "=== 4/4 上传前端产物 ==="
& "$root\deploy_dist.ps1"

Write-Host "--- deploy finished ---"
