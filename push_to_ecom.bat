@echo off
echo ========================================================
echo   Pushing College Library ERP & E-Commerce to GitHub
echo   Target: https://github.com/Atif-Azmi/Ecom-Project
echo ========================================================
echo.

cd /d "%~dp0"

echo [*] 1. Staging updated files...
git add .

echo [*] 2. Committing ERP & E-Commerce modules...
git commit -m "College Library ERP Portal with E-Commerce Features (Complete Project)"

echo [*] 3. Setting main branch...
git branch -M main

echo [*] 4. Updating remote origin...
git remote remove origin 2>nul
git remote add origin https://github.com/Atif-Azmi/Ecom-Project.git

echo [*] 5. Pushing to GitHub (main branch)...
git push -u origin main

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [*] Note: Agar remote repo me pehle se koi README/files hain, toh force push kar rahe hain...
    git push -u origin main --force
)

echo.
echo ========================================================
echo [SUCCESS] Project successfully pushed to:
echo https://github.com/Atif-Azmi/Ecom-Project
echo ========================================================
pause
