# Deploy frontend dist to the server (ASCII only to avoid PS 5.1 encoding issues)
$dist = 'c:\Users\luka\Desktop\设计总面板\frontend\dist'
$remote = 'root@123.207.74.83'

ssh $remote "mkdir -p /server/nginx/html/static"

scp "$dist\index.html" "${remote}:/server/nginx/html/index.html"
Write-Host "index.html -> $LASTEXITCODE"

$files = Get-ChildItem "$dist\static" -File
Write-Host "static files: $($files.Count)"
foreach ($f in $files) {
  scp $f.FullName "${remote}:/server/nginx/html/static/$($f.Name)" 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) { Write-Host "OK   $($f.Name)" }
  else { Write-Host "FAIL $($f.Name)" }
}
Write-Host "--- deploy done ---"
