@echo off
title OpenClaw 懶人盒啟動器
:: 確保指令在當前資料夾執行
cd /d %~dp0

:: 呼叫我們解壓縮出來的 python.exe 執行管理程式
.\python.exe manager.py

pause