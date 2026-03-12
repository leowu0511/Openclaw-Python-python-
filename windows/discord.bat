@echo off
chcp 65001 >nul
title OpenClaw 懶人盒啟動器
cd /d %~dp0

:: 檢查 pip 是否存在，沒有就先裝 pip
.\python.exe -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [*] 偵測到嵌入式 Python 缺少 pip，正在安裝...
    .\python.exe -c "import urllib.request; urllib.request.urlretrieve('https://bootstrap.pypa.io/get-pip.py', 'get-pip.py')"
    if errorlevel 1 (
        echo [!] 下載 get-pip.py 失敗，請確認網路連線
        pause
        exit /b 1
    )
    .\python.exe get-pip.py --no-warn-script-location
    del get-pip.py
    echo [*] pip 安裝完成
)

:: 嵌入式 Python 需要把 _pth 裡的 #import site 改成 import site
.\python.exe -c "import glob; [open(f,'w',encoding='utf-8').write(open(f,encoding='utf-8').read().replace('#import site','import site')) for f in glob.glob('python*._pth') if '#import site' in open(f,encoding='utf-8').read()]"

:: 檢查 discord.py 是否已安裝，沒有就自動裝
.\python.exe -c "import discord" 2>nul
if errorlevel 1 (
    echo [*] 偵測到缺少 discord.py，正在安裝...
    .\python.exe -m pip install discord.py aiohttp --no-warn-script-location --quiet
    if errorlevel 1 (
        echo [!] 安裝失敗，請確認網路連線後重試
        pause
        exit /b 1
    )
    echo [*] 安裝完成
)

:: 啟動主程式
.\python.exe discord_ver.py

pause
