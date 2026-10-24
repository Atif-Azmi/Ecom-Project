Write-Host "[*] Staging files..." -ForegroundColor Cyan
git add .

Write-Host "[*] Committing..." -ForegroundColor Cyan
git commit -m "College Library ERP Portal with E-Commerce Features (Complete Project)"

Write-Host "[*] Setting main branch..." -ForegroundColor Cyan
git branch -M main

Write-Host "[*] Updating remote origin..." -ForegroundColor Cyan
git remote remove origin 2>$null
git remote add origin https://github.com/Atif-Azmi/Ecom-Project.git

Write-Host "[*] Pushing to https://github.com/Atif-Azmi/Ecom-Project..." -ForegroundColor Green
git push -u origin main --force

Write-Host "[SUCCESS] Pushed to GitHub!" -ForegroundColor Green
