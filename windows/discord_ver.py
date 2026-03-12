import os
import subprocess
import webbrowser
import time
import zipfile
import urllib.request
import urllib.error
import json
import getpass
import threading
import sys

import ssl
ssl._create_default_https_context = ssl._create_unverified_context

# --- 產品配置 ---
APP_NAME = "OpenClaw 懶人盒 Pro (v22 引擎升級版)"
NODE_URL = "https://nodejs.org/dist/v22.13.1/node-v22.13.1-win-x64.zip"
OPENCLAW_MAIN_URL = "https://github.com/openclaw/openclaw/archive/refs/heads/main.zip"
OPENCLAW_RELEASE_API = "https://api.github.com/repos/openclaw/openclaw/releases/latest"
MINGIT_URL = "https://github.com/git-for-windows/git/releases/download/v2.44.0.windows.1/MinGit-2.44.0-64-bit.zip"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL_ID = "openrouter/hunter-alpha"
OPENROUTER_DEFAULT_MODEL = f"openrouter/{OPENROUTER_DEFAULT_MODEL_ID}"
OPENROUTER_PREFERRED_MODELS = [
    {"id": "openrouter/hunter-alpha",             "name": "1. Hunter Alpha               [預設] 1T參數 1M context Agentic"},
    {"id": "arcee-ai/trinity-large-preview:free", "name": "2. Arcee Trinity Large Preview       400B Agent框架最佳化"},
    {"id": "stepfun/step-3.5-flash:free",         "name": "3. StepFun Step 3.5 Flash            196B 速度快 256K 匯報整理"},
    {"id": "arcee-ai/trinity-mini:free",          "name": "4. Arcee Trinity Mini                26B 輕量備用"},
    {"id": "nvidia/nemotron-3-nano-30b-a3b:free", "name": "5. NVIDIA Nemotron 3 Nano 30B        Agentic 系統指令穩定"},
    {"id": "openrouter/free",                     "name": "6. OpenRouter Auto Free              自動挑最適合免費模型"},
]
WORKDIR = os.getcwd()

def log(msg):
    print(f"[*] {msg}")

def err(msg):
    print(f"[!] 錯誤: {msg}")

def download_and_extract(url, target_dir):
    zip_tmp = os.path.join(WORKDIR, "temp_download.zip")
    log(f"正在下載: {url}")
    urllib.request.urlretrieve(url, zip_tmp)
    log("下載完成，正在解壓縮...")

    extracted_roots = []
    with zipfile.ZipFile(zip_tmp, 'r') as zip_ref:
        members = [name for name in zip_ref.namelist() if name and not name.startswith("__MACOSX/")]
        extracted_roots = sorted({name.split("/", 1)[0] for name in members if "/" in name})
        zip_ref.extractall(target_dir)
    os.remove(zip_tmp)
    log("解壓縮完成")
    return extracted_roots

def get_latest_openclaw_source():
    """優先抓 GitHub 最新 stable release；失敗時回退 main.zip"""
    try:
        req = urllib.request.Request(
            OPENCLAW_RELEASE_API,
            headers={"User-Agent": "openclaw-launcher"}
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        tag = payload.get("tag_name")
        zip_url = payload.get("zipball_url")
        if tag and zip_url:
            return tag, zip_url
    except Exception as e:
        log(f"警告: 取得 OpenClaw stable release 失敗，改用 main 分支 ({e})")

    return "main", OPENCLAW_MAIN_URL

def normalize_openclaw_folder(extracted_roots, app_folder):
    """把 GitHub zipball 解壓出的隨機資料夾名稱統一整理成 openclaw-main"""
    if os.path.exists(app_folder):
        return True

    candidates = []
    for root_name in extracted_roots:
        root_path = os.path.join(WORKDIR, root_name)
        if os.path.isdir(root_path) and os.path.exists(os.path.join(root_path, "openclaw.mjs")):
            candidates.append(root_path)

    if len(candidates) == 1:
        old_name = os.path.basename(candidates[0])
        os.rename(candidates[0], app_folder)
        log(f"已整理 OpenClaw 資料夾: {old_name} -> {os.path.basename(app_folder)}")
        return True

    return False

def is_incompatible_openclaw_snapshot(app_folder):
    """偵測已知 main 與 npm 套件版本不相容的程式碼形態"""
    oauth_file = os.path.join(app_folder, "src", "agents", "auth-profiles", "oauth.ts")
    if not os.path.exists(oauth_file):
        return False

    try:
        with open(oauth_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(800)
        return "getOAuthApiKey" in content and 'from "@mariozechner/pi-ai";' in content
    except OSError:
        return False

def install_openclaw_source(app_folder):
    tag, source_url = get_latest_openclaw_source()
    if tag == "main":
        log("獲取 OpenClaw 最新核心 (main)...")
    else:
        log(f"獲取 OpenClaw 穩定核心 ({tag})...")

    extracted_roots = download_and_extract(source_url, WORKDIR)

    if not normalize_openclaw_folder(extracted_roots, app_folder):
        raise RuntimeError("OpenClaw 解壓縮後找不到可用資料夾")

    source_meta = os.path.join(app_folder, ".launcher-source")
    with open(source_meta, "w", encoding="ascii", newline="\n") as f:
        f.write(f"tag={tag}\n")
        f.write(f"url={source_url}\n")

def try_backup_folder(path, label="舊版本"):
    """嘗試備份資料夾，若被鎖定則回傳 False 讓呼叫端決定 fallback。"""
    backup = f"{path}.backup-{int(time.time())}"
    try:
        os.rename(path, backup)
        log(f"已備份{label}至: {backup}")
        return True
    except OSError as e:
        log(f"警告: 無法備份 {path} ({e})")
        return False

def ask_yes_no(prompt, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {hint}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "1", "true")

def extract_provider_model_id(primary_model, provider="openrouter"):
    if not isinstance(primary_model, str):
        return None
    prefix = f"{provider}/"
    if primary_model.startswith(prefix):
        return primary_model[len(prefix):]
    return None

def build_openrouter_provider_models(selected_model_id=None):
    models = []
    seen = set()

    if selected_model_id and isinstance(selected_model_id, str):
        model_id = selected_model_id.strip()
        if model_id:
            models.append({"id": model_id, "name": model_id})
            seen.add(model_id)

    for item in OPENROUTER_PREFERRED_MODELS:
        model_id = item.get("id")
        if model_id and model_id not in seen:
            models.append(item)
            seen.add(model_id)

    return models

def probe_openrouter_model(api_key, model_id):
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8
    }
    req = urllib.request.Request(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=20):
            return True, 200
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception as e:
        return False, str(e)

def read_current_openrouter_model_id(config_file):
    if not os.path.exists(config_file):
        return None
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        primary = (
            cfg.get("agents", {})
            .get("defaults", {})
            .get("model", {})
            .get("primary")
        )
        return extract_provider_model_id(primary, "openrouter")
    except Exception:
        return None

def choose_openrouter_model(api_key, current_model_id=None):
    candidates = []
    if current_model_id:
        candidates.append(current_model_id)
    for item in OPENROUTER_PREFERRED_MODELS:
        model_id = item.get("id")
        if model_id and model_id not in candidates:
            candidates.append(model_id)

    log("正在測試 OpenRouter 模型可用性... 約 5-20 秒")
    results = []
    for idx, model_id in enumerate(candidates, start=1):
        ok, detail = probe_openrouter_model(api_key, model_id)
        status = "OK" if ok else f"HTTP {detail}" if isinstance(detail, int) else str(detail)
        print(f"  {idx}. {model_id} ({status})")
        results.append((model_id, ok))

    working = [model_id for model_id, ok in results if ok]
    default_model_id = working[0] if working else (current_model_id or OPENROUTER_DEFAULT_MODEL_ID)

    choice = input(
        f"請輸入模型編號或直接輸入模型ID (Enter 使用 {default_model_id}): "
    ).strip()

    if not choice:
        return default_model_id

    selected_model_id = None
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(results):
            selected_model_id = results[idx - 1][0]
        else:
            log("編號超出範圍，改用預設模型")
            return default_model_id
    else:
        selected_model_id = choice

    for model_id, ok in results:
        if model_id == selected_model_id and not ok:
            if not ask_yes_no("此模型剛測試失敗，仍要使用嗎", default=False):
                return default_model_id

    return selected_model_id

def maybe_bootstrap_openrouter_config(config_file):
    """每次啟動都可選擇是否重設 OpenRouter 設定。"""
    has_config = os.path.exists(config_file)

    if has_config:
        prompt = "是否要重新設定 OpenRouter API 金鑰？輸入 yes 重新設定，輸入 n 跳過"
    else:
        prompt = "未偵測到 OpenClaw 設定檔，是否改用 OpenRouter API 自動建立設定"

    if not ask_yes_no(prompt, default=False):
        return False

    try:
        api_key = getpass.getpass("請輸入 OPENROUTER_API_KEY (sk-or-...): ").strip()
    except Exception:
        api_key = input("請輸入 OPENROUTER_API_KEY (sk-or-...): ").strip()

    if not api_key:
        if has_config:
            log("未輸入 OPENROUTER_API_KEY，保留現有設定")
        else:
            err("OPENROUTER_API_KEY 為空，將改走 setup 精靈")
        return False

    if not api_key.startswith("sk-or-"):
        log("警告: Key 看起來不是 sk-or- 開頭，請確認是否正確")

    current_model_id = read_current_openrouter_model_id(config_file)
    selected_model_id = choose_openrouter_model(api_key, current_model_id)
    selected_primary_model = selected_model_id if selected_model_id.startswith("openrouter/") else f"openrouter/{selected_model_id}"

    config_dir = os.path.dirname(config_file)
    os.makedirs(config_dir, exist_ok=True)

    if has_config:
        backup_file = f"{config_file}.backup-{int(time.time())}"
        try:
            with open(config_file, "r", encoding="utf-8", errors="ignore") as src:
                old_data = src.read()
            with open(backup_file, "w", encoding="utf-8", newline="\n") as dst:
                dst.write(old_data)
            log(f"已備份原設定檔: {backup_file}")
        except OSError as e:
            log(f"警告: 備份設定檔失敗，將直接覆蓋 ({e})")

    config = {
        "env": {
            "OPENROUTER_API_KEY": api_key
        },
        "models": {
            "providers": {
                "openrouter": {
                    "baseUrl": OPENROUTER_BASE_URL,
                    "api": "openai-completions",
                    "models": build_openrouter_provider_models(selected_model_id)
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {
                    "primary": selected_primary_model
                }
            }
        },
        "gateway": {
            "http": {
                "endpoints": {
                    "chatCompletions": {
                        "enabled": True
                    }
                }
            }
        }
    }

    with open(config_file, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")

    if has_config:
        log(f"已更新 OpenRouter 設定: {config_file}")
    else:
        log(f"已建立 OpenRouter 設定: {config_file}")
    log(f"已選模型: {selected_primary_model}")
    return True


def ensure_chat_completions_enabled(config_file):
    """確保 gateway.http.endpoints.chatCompletions.enabled = true"""
    if not os.path.exists(config_file):
        return False
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return False

    gateway = cfg.setdefault("gateway", {})
    http    = gateway.setdefault("http", {})
    endpoints = http.setdefault("endpoints", {})
    chat = endpoints.setdefault("chatCompletions", {})

    if chat.get("enabled") is True:
        return False  # 已經是正確值，不需要改

    chat["enabled"] = True
    try:
        with open(config_file, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        log("已自動啟用 gateway chatCompletions endpoint")
        return True
    except Exception as e:
        log(f"警告: 無法更新設定檔 ({e})")
        return False

def ensure_openrouter_models_schema(config_file):
    """修復既有設定：若 openrouter provider 缺 models 陣列，補上預設值。"""
    if not os.path.exists(config_file):
        return False

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return False

    models = cfg.get("models") if isinstance(cfg, dict) else None
    providers = models.get("providers") if isinstance(models, dict) else None
    openrouter = providers.get("openrouter") if isinstance(providers, dict) else None

    if not isinstance(openrouter, dict):
        return False

    primary = (
        cfg.get("agents", {})
        .get("defaults", {})
        .get("model", {})
        .get("primary")
    )
    current_model_id = extract_provider_model_id(primary, "openrouter")

    current_models = openrouter.get("models")
    if isinstance(current_models, list) and len(current_models) > 0:
        return False

    backup_file = f"{config_file}.backup-autofix-{int(time.time())}"
    try:
        with open(config_file, "r", encoding="utf-8", errors="ignore") as src:
            old_data = src.read()
        with open(backup_file, "w", encoding="utf-8", newline="\n") as dst:
            dst.write(old_data)
    except OSError:
        pass

    openrouter["models"] = build_openrouter_provider_models(current_model_id)

    with open(config_file, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")

    log("已自動修復 OpenRouter 設定: 補上 models.providers.openrouter.models")
    return True

def find_git_bin(git_dir):
    """找到 git.exe 與可用 shell，必要時建立 bash 相容包裝器後回傳 PATH 字串"""
    cmd_dir = None
    bash_dir = None
    sh_dir = None

    for root, dirs, files in os.walk(git_dir):
        if "git.exe" in files and cmd_dir is None:
            log(f"找到 git.exe 於: {root}")
            cmd_dir = root
        if "bash.exe" in files and bash_dir is None:
            log(f"找到 bash.exe 於: {root}")
            bash_dir = root

        if "sh.exe" in files and sh_dir is None:
            sh_dir = root

    if not bash_dir and sh_dir:
        log(f"未找到 bash.exe，改用 sh.exe 於: {sh_dir}")

        if cmd_dir:
            bash_cmd = os.path.join(cmd_dir, "bash.cmd")
            if not os.path.exists(bash_cmd):
                sh_exe = os.path.join(sh_dir, "sh.exe")
                with open(bash_cmd, "w", encoding="ascii", newline="\r\n") as f:
                    f.write("@echo off\r\n")
                    f.write(f'"{sh_exe}" %*\r\n')
                log(f"已建立 bash 相容包裝器: {bash_cmd}")

    parts = [d for d in [cmd_dir, bash_dir or sh_dir] if d]
    return ";".join(parts) if parts else None

def find_npm(node_dir):
    for root, dirs, files in os.walk(node_dir):
        if "npm.cmd" in files:
            log(f"找到 npm.cmd 於: {root}")
            return os.path.join(root, "npm.cmd")
    return None

def find_node(node_dir):
    for root, dirs, files in os.walk(node_dir):
        if "node.exe" in files:
            return root
    return None


def init_env():
    os.system('cls')
    print("="*60)
    print(f"  {APP_NAME}")
    print("="*60)

    # 1. 配置 Node.js v22
    node_dir = os.path.join(WORKDIR, "node_env")
    if not os.path.exists(node_dir):
        log("配置 Node.js v22 環境中...")
        download_and_extract(NODE_URL, WORKDIR)
        extracted = os.path.join(WORKDIR, "node-v22.13.1-win-x64")
        if os.path.exists(extracted):
            os.rename(extracted, node_dir)
            log("Node.js 配置完成")
        else:
            err("Node.js 解壓縮後找不到預期資料夾")
            input("按 Enter 結束...")
            exit(1)

    # 2. 配置 MinGit
    git_dir = os.path.join(WORKDIR, "git_env")
    if not os.path.exists(git_dir):
        log("配置 Git 環境...")
        os.makedirs(git_dir, exist_ok=True)
        download_and_extract(MINGIT_URL, git_dir)

    # 設定 PATH
    node_bin = find_node(node_dir)
    git_bin = find_git_bin(git_dir)

    if not node_bin:
        err("找不到 node.exe，Node.js 環境可能損壞")
        input("按 Enter 結束...")
        exit(1)

    path_prepend = node_bin
    if git_bin:
        path_prepend += f";{git_bin}"
    os.environ["PATH"] = path_prepend + ";" + os.environ["PATH"]
    log(f"PATH 設定完成 | Node: {node_bin} | Git: {git_bin or '未找到'}")

    # 3. 配置 OpenClaw
    main_folder = os.path.join(WORKDIR, "openclaw-main")
    stable_folder = os.path.join(WORKDIR, "openclaw-stable")
    app_folder = main_folder


    if not os.path.exists(app_folder):
        try:
            install_openclaw_source(app_folder)
        except Exception as e:
            err(f"OpenClaw 下載或解壓失敗: {e}")
            err(f"OpenClaw 解壓縮後找不到 {os.path.basename(app_folder)} 資料夾")
            input("按 Enter 結束...")
            exit(1)

    return app_folder

def run(app_folder):
    npm_path = find_npm(os.path.join(WORKDIR, "node_env"))
    if not npm_path:
        err("找不到 npm.cmd，Node.js 環境可能損壞")
        input("按 Enter 結束...")
        exit(1)

    app_folder_clean = app_folder
    node_bin_dir = os.path.join(WORKDIR, "node_env")

    pnpm_path = os.path.join(node_bin_dir, "pnpm.cmd")
    node_exe = os.path.join(node_bin_dir, "node.exe")

    pnpm_global_bin = node_bin_dir

    npm_path_clean = os.path.join(node_bin_dir, "npm.cmd")
    if not os.path.exists(pnpm_path):
        log("安裝 pnpm...")
        subprocess.run([npm_path_clean, "install", "-g", "pnpm"], cwd=app_folder_clean)
        log("pnpm 安裝完成")

    # pnpm install（主專案）
    if not os.path.exists(os.path.join(app_folder_clean, "node_modules")):
        log("首次安裝依賴 (pnpm install)... 約 2-3 分鐘")
        subprocess.run([pnpm_path, "install", "--ignore-scripts"], cwd=app_folder_clean)

    # Build TypeScript
    dist_index = os.path.join(app_folder_clean, "dist", "index.js")
    dist_entry = os.path.join(app_folder_clean, "dist", "entry.js")
    if not os.path.exists(dist_index):
        log("編譯 TypeScript... 約 30 秒")
        build_result = subprocess.run([pnpm_path, "run", "build"], cwd=app_folder_clean)
        if build_result.returncode != 0:
            if os.path.exists(dist_index) and os.path.exists(dist_entry):
                log("警告: build 型別檢查失敗，但 runtime dist 已產生，先繼續啟動")
            else:
                err("build 失敗")
                input("按 Enter 結束...")
                exit(1)
        log("編譯完成")

    # Build UI
    ui_build_flag = os.path.join(app_folder_clean, ".ui_built")
    ui_dir = os.path.join(app_folder_clean, "ui")
    if not os.path.exists(ui_build_flag) and os.path.exists(ui_dir):
        vite_config = os.path.join(ui_dir, "vite.config.ts")
        vite_config_bak = vite_config + ".bak"
        if os.path.exists(vite_config) and not os.path.exists(vite_config_bak):
            with open(vite_config, "r", encoding="utf-8") as f:
                cfg = f.read()
            with open(vite_config_bak, "w", encoding="utf-8") as f:
                f.write(cfg)
            if "defineConfig({" in cfg and "root:" not in cfg:
                cfg = cfg.replace("defineConfig({", "defineConfig({\n  root: './',")
                with open(vite_config, "w", encoding="utf-8") as f:
                    f.write(cfg)
                log("已 patch vite.config.ts (加入 root: './')")

        log("安裝 UI 依賴...")
        subprocess.run([pnpm_path, "install"], cwd=ui_dir)
        log("建置 Control UI... 約 1-2 分鐘")
        ui_result = subprocess.run([pnpm_path, "run", "build"], cwd=ui_dir)

        if ui_result.returncode != 0:
            log("警告: UI build 失敗，介面可能無法顯示，但 Gateway 仍可啟動")
        else:
            open(ui_build_flag, 'w').close()
            log("UI 建置完成")
    elif not os.path.exists(ui_dir):
        log("警告: 找不到 ui/ 資料夾，跳過 UI build")

    # 首次執行時，先執行 setup
    config_file = os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")
    openrouter_bootstrapped = maybe_bootstrap_openrouter_config(config_file)
    ensure_openrouter_models_schema(config_file)
    ensure_chat_completions_enabled(config_file)

    if not os.path.exists(config_file):
        log("首次設定 OpenClaw，正在執行 setup 精靈...")
        subprocess.run([node_exe, dist_index, "setup"], cwd=app_folder_clean)
        log("setup 完成")
    elif openrouter_bootstrapped:
        log("已套用 OpenRouter 設定，略過 setup 精靈")

    # 啟動 Gateway
    PORT = 8080
    log_path = os.path.join(WORKDIR, "server.log")
    log(f"正在啟動 OpenClaw Gateway (port {PORT})，日誌: {log_path}")

    child_env = os.environ.copy()
    child_env["PATH"] = pnpm_global_bin + ";" + child_env.get("PATH", "")

    with open(log_path, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [node_exe, dist_index, "gateway", "--port", str(PORT), "--allow-unconfigured"],
            cwd=app_folder_clean,
            stdout=log_file,
            stderr=log_file,
            env=child_env
        )

    log("等待服務初始化 (約 20 秒)...")
    for i in range(20, 0, -1):
        print(f"\r  剩餘 {i} 秒...", end="", flush=True)
        time.sleep(1)
        if proc.poll() is not None:
            break
    print()

    if proc.poll() is not None:
        err(f"Gateway 已退出，退出碼: {proc.returncode}")
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        print("\n--- 最後 40 行日誌 ---")
        print("".join(lines[-40:]))
        input("\n請截圖錯誤訊息後按 Enter 結束...")
        exit(1)

    # 讀取 token 並直接帶入 URL
    token = ""
    config_path = os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
    except Exception:
        pass

    if token:
        webbrowser.open(f"http://127.0.0.1:{PORT}/?token={token}")
        log(f"Token: {token}")
        log("如果畫面仍顯示未授權，請手動將上方 Token 貼入網頁的「網關令牌」欄位")
    else:
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        log(f"找不到 token，請查看 {config_path}")

    print("\n" + "="*60)
    print("  OpenClaw Gateway 啟動成功！瀏覽器已開啟")
    print(f"  網址: http://127.0.0.1:{PORT}")
    print(f"  日誌: {log_path}")
    print("="*60)

    # 互動式啟動 Discord Bot
    discord_cfg = load_discord_config()
    discord_cfg = maybe_bootstrap_discord_config(discord_cfg)

    if discord_cfg.get("enabled"):
        log("正在背景啟動 Discord Bot...")
        bot_thread = threading.Thread(
            target=run_discord_bot,
            args=(token, discord_cfg["bot_token"], discord_cfg["user_id"]),
            daemon=True
        )
        bot_thread.start()
        log("Discord Bot 已在背景啟動")

    input("\n按 Enter 關閉此視窗（Gateway 將繼續在背景執行）...")

# ──────────────────────────────────────────────
# Discord Bot
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# Discord 設定管理
# ──────────────────────────────────────────────

DISCORD_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".openclaw", "discord.json")

def load_discord_config():
    if not os.path.exists(DISCORD_CONFIG_FILE):
        return {"enabled": False, "bot_token": "", "user_id": 0}
    try:
        with open(DISCORD_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "bot_token": "", "user_id": 0}

def save_discord_config(cfg):
    os.makedirs(os.path.dirname(DISCORD_CONFIG_FILE), exist_ok=True)
    with open(DISCORD_CONFIG_FILE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")

def maybe_bootstrap_discord_config(cfg):
    has_config = cfg.get("enabled") and cfg.get("bot_token") and cfg.get("user_id")

    if has_config:
        prompt = f"是否要啟動 Discord Bot？（目前 User ID: {cfg['user_id']}）輸入 yes 啟動，n 跳過"
        default = True
    else:
        prompt = "是否要啟動 Discord Bot？（需要先安裝：pip install discord.py aiohttp）"
        default = False

    if not ask_yes_no(prompt, default=default):
        cfg["enabled"] = False
        return cfg

    # 已有設定就問要不要重新輸入
    if has_config:
        if not ask_yes_no("是否要重新輸入 Discord Bot Token 和 User ID", default=False):
            cfg["enabled"] = True
            return cfg

    # 輸入 Bot Token
    try:
        bot_token = getpass.getpass("請輸入 Discord Bot Token: ").strip()
    except Exception:
        bot_token = input("請輸入 Discord Bot Token: ").strip()

    if not bot_token:
        log("未輸入 Token，跳過 Discord Bot")
        cfg["enabled"] = False
        return cfg

    # 輸入 User ID
    user_id_str = input("請輸入你的 Discord 使用者 ID（數字）: ").strip()
    if not user_id_str.isdigit():
        log("User ID 格式不正確，跳過 Discord Bot")
        cfg["enabled"] = False
        return cfg

    cfg = {
        "enabled": True,
        "bot_token": bot_token,
        "user_id": int(user_id_str)
    }
    save_discord_config(cfg)
    log(f"已儲存 Discord 設定（User ID: {cfg['user_id']}）")
    return cfg


def run_discord_bot(openclaw_token: str, bot_token: str, user_id: int):
    """在獨立執行緒中跑 Discord Bot，需要先安裝：pip install discord.py aiohttp"""
    try:
        import discord
        from discord.ext import commands, tasks
        from discord import app_commands
        import aiohttp
        import asyncio
        import datetime
    except ImportError:
        print("[!] 缺少 Discord Bot 依賴，請執行：pip install discord.py aiohttp")
        return

    OPENCLAW_BASE = "http://127.0.0.1:8080"

    conversation_history: dict = {}
    active_channels_today: set = set()

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)
    tree = bot.tree

    DISCORD_SYSTEM_PROMPT = (
        "你正在透過 Discord 跟使用者聊天。"
        "請用繁體中文回覆，語氣自然親切。"
        "回覆長度適中，不要過長，Discord 單則訊息有 2000 字上限。"
    )

    async def ask_openclaw(channel_id, user_message, system_prompt=None):
        if channel_id not in conversation_history:
            conversation_history[channel_id] = []
        conversation_history[channel_id].append({"role": "user", "content": user_message})
        messages = conversation_history[channel_id].copy()
        effective_system = system_prompt if system_prompt else DISCORD_SYSTEM_PROMPT
        messages.insert(0, {"role": "system", "content": effective_system})
        headers = {
            "Authorization": f"Bearer {openclaw_token}",
            "Content-Type": "application/json"
        }
        payload = {"messages": messages, "stream": False}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{OPENCLAW_BASE}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        return f"[錯誤] Gateway 回傳 HTTP {resp.status}：{text[:200]}"
                    data = await resp.json()
                    reply = data["choices"][0]["message"]["content"]
            conversation_history[channel_id].append({"role": "assistant", "content": reply})
            active_channels_today.add(channel_id)
            return reply
        except asyncio.TimeoutError:
            return "[錯誤] Gateway 回應超時"
        except Exception as e:
            return f"[錯誤] 無法連線 Gateway：{e}"

    async def ask_openclaw_fresh(prompt):
        headers = {
            "Authorization": f"Bearer {openclaw_token}",
            "Content-Type": "application/json"
        }
        payload = {"messages": [{"role": "user", "content": prompt}], "stream": False}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{OPENCLAW_BASE}/v1/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"]
        except Exception:
            return None

    async def send_long(target, text, is_reply=False, original_msg=None):
        """自動切割超過 2000 字的訊息"""
        chunks = [text[i:i+1990] for i in range(0, len(text), 1990)]
        for i, chunk in enumerate(chunks):
            if i == 0 and is_reply and original_msg:
                await original_msg.reply(chunk)
            else:
                await target.send(chunk)

    @bot.event
    async def on_ready():
        await tree.sync()
        daily_report.start()
        print(f"[*] Discord Bot 上線：{bot.user}")

    @bot.event
    async def on_message(message):
        if message.author.bot:
            return
        if message.author.id != user_id:
            return
        await bot.process_commands(message)
        if message.content.startswith("/") or message.content.startswith("!"):
            return
        async with message.channel.typing():
            reply = await ask_openclaw(message.channel.id, message.content)
        await send_long(message.channel, reply, is_reply=True, original_msg=message)

    @tree.command(name="search", description="叫 AI 上網搜尋並回覆結果")
    @app_commands.describe(query="要搜尋的關鍵字或問題")
    async def search(interaction: discord.Interaction, query: str):
        if interaction.user.id != user_id:
            await interaction.response.send_message("這個 bot 只服務特定使用者。", ephemeral=True)
            return
        await interaction.response.defer()
        system_prompt = (
            "你是一個網路搜尋助理。使用者給你關鍵字或問題，"
            "請提供詳細有用的資訊摘要，並列出相關參考來源。"
            "回覆請使用繁體中文。"
        )
        reply = await ask_openclaw(interaction.channel_id, f"請幫我搜尋：{query}", system_prompt)
        await send_long(interaction.channel, reply)
        await interaction.followup.send("✅ 搜尋完成", ephemeral=True)

    @tasks.loop(time=datetime.time(hour=0, minute=0, second=0))
    async def daily_report():
        if not active_channels_today:
            return
        today_str = datetime.date.today().strftime("%Y/%m/%d")
        for channel_id in list(active_channels_today):
            channel = bot.get_channel(channel_id)
            if not channel:
                continue
            history = conversation_history.get(channel_id, [])
            if not history:
                continue
            history_text = "\n".join(
                f"{'使用者' if m['role'] == 'user' else 'AI'}：{m['content']}"
                for m in history
            )
            prompt = (
                f"以下是 {today_str} 的對話紀錄：\n\n{history_text}\n\n"
                f"請用繁體中文，以條列式整理今天的對話重點，包含：\n"
                f"1. 討論了哪些主題\n2. 完成了哪些事情\n3. 還有哪些未解決的問題（如果有）\n"
                f"格式簡潔清楚，像一份日報。"
            )
            summary = await ask_openclaw_fresh(prompt)
            if not summary:
                summary = "（匯報生成失敗，請檢查 Gateway 狀態）"
            report_msg = f"📋 **{today_str} 每日匯報**\n{'─' * 30}\n{summary}"
            await send_long(channel, report_msg)
            print(f"[*] 已發送匯報到頻道 {channel_id}")
        active_channels_today.clear()
        conversation_history.clear()

    @daily_report.before_loop
    async def before_daily_report():
        await bot.wait_until_ready()

    bot.run(bot_token)


if __name__ == "__main__":
    try:
        app_folder = init_env()
        run(app_folder)
    except Exception as e:
        print(f"\n[發生錯誤] {e}")
        import traceback
        traceback.print_exc()
        input("\n請截圖錯誤訊息回報給開發者...")
