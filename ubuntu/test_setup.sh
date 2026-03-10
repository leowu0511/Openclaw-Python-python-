#!/bin/bash

# --- 1. 系統更新與基礎工具安裝 ---
echo "[*] 正在安裝基礎工具 (socat, git, python3)..."
sudo apt update && sudo apt install socat git python3 -y

# --- 2. 處理 1GB RAM 記憶體問題 (建立 4GB Swap) ---
# 檢查如果沒有 Swap，就自動幫你開
if [ $(free -m | grep Swap | awk '{print $2}') -lt 1000 ]; then
    echo "[*] 偵測到記憶體不足，正在建立 4GB Swap..."
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

# --- 3. 處理 CLI 埠位橋接 (18789 -> 8080) ---
echo "[*] 正在搭起埠位橋接隧道 (18789 -> 8080)..."
sudo fuser -k 18789/tcp >/dev/null 2>&1
socat TCP-LISTEN:18789,fork,reuseaddr TCP:127.0.0.1:8080 &

# --- 4. 自動寫入 'oc' 快捷指令 ---
if ! grep -q "alias oc=" ~/.bashrc; then
    echo "[*] 正在設定 'oc' 快捷指令到 .bashrc..."
    echo "alias oc='(sudo fuser 18789/tcp >/dev/null 2>&1 || socat TCP-LISTEN:18789,fork,reuseaddr TCP:127.0.0.1:8080 &) && NODE_OPTIONS=\"--max-old-space-size=2048\" PATH=\$PATH:~/node_env/bin ~/node_env/bin/node ~/openclaw-main/dist/index.js'" >> ~/.bashrc
    source ~/.bashrc
fi

# --- 5. 執行原本的 Python 管理程式 ---
echo "[*] 萬事俱備，啟動 OpenClaw 引擎..."
export NODE_OPTIONS="--max-old-space-size=2048"
python3 manager_linux.py
