$base = 'c:\Users\luka\Desktop\设计总面板\backend'
$files = @(
  'app\config\business.py',
  'app\config\prompts.py',
  'app\config\settings.py',
  'app\database\discussion_repo.py',
  'app\models\schemas.py',
  'app\routers\discussions.py',
  'app\services\discussion_engine.py',
  'app\services\order_judge.py',
  'app\services\extension_service.py',
  'app\services\official_clients\wenxin_client.py'
)

foreach ($f in $files) {
  $src = Join-Path $base $f
  $dst = 'root@123.207.74.83:/server/backend/' + ($f -replace '\\', '/')
  scp $src $dst 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "OK   $f"
  } else {
    Write-Host "FAIL $f"
  }
}
Write-Host "--- 同步完成 ---"
