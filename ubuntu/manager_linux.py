import os
import subprocess
import webbrowser
import time
import zipfile
import tarfile
import urllib.request
import urllib.error
import json
import getpass
import shutil
import platform

# --- 產品配置 ---
APP_NAME = "OpenClaw 懶人盒 Pro (v22 引擎升級版)"

# 偵測系統架構，選擇對應的 Node.js 下載包
def _get_node_url():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "https://nodejs.org/dist/v22.13.1/node-v22.13.1-linux-x64.tar.xz", "node-v22.13.1-linux-x64"
    elif machine in ("aarch64", "arm64"):
        return "https://nodejs.org/dist/v22.13.1/node-v22.13.1-linux-arm64.tar.xz", "node-v22.13.1-linux-arm64"
    else:
        raise RuntimeError(f"不支援的 CPU 架構: {machine}")

OPENCLAW_MAIN_URL = "https://github.com/openclaw/openclaw/archive/refs/heads/main.zip"
OPENCLAW_RELEASE_API = "https://api.github.com/repos/openclaw/openclaw/releases/latest"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_DEFAULT_MODEL_ID = "meta/llama-3.3-70b-instruct"
NVIDIA_DEFAULT_MODEL = f"nvidia/{NVIDIA_DEFAULT_MODEL_ID}"
NVIDIA_PREFERRED_MODELS = [
    {"id": "meta/llama-3.3-70b-instruct",                     "name": "Meta Llama 3.3 70B Instruct"},
    {"id": "meta/llama-3.1-8b-instruct",                      "name": "Meta Llama 3.1 8B Instruct"},
    {"id": "mistralai/mistral-small-3.1-24b-instruct-2503",   "name": "Mistral Small 3.1 24B Instruct"},
    {"id": "nvidia/llama-3.1-nemotron-70b-instruct",           "name": "NVIDIA Llama 3.1 Nemotron 70B Instruct"},
    {"id": "nvidia/mistral-nemo-minitron-8b-8k-instruct",     "name": "NVIDIA Mistral NeMo Minitron 8B"},
]
WORKDIR = os.getcwd()

# ──────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────

def log(msg):
    print(f"[*] {msg}")

def err(msg):
    print(f"[!] 錯誤: {msg}")

def pause_exit(code=1):
    """取代原本的 input('按 Enter 結束...') + exit()"""
    input("\n按 Enter 結束...")
    exit(code)

def download_file(url, dest):
    log(f"正在下載: {url}")
    urllib.request.urlretrieve(url, dest)
    log("下載完成")

def extract_archive(archive_path, target_dir):
    """支援 .zip 與 .tar.xz / .tar.gz"""
    log(f"正在解壓縮: {archive_path}")
    extracted_roots = []

    if archive_path.endswith(".zip"):
        with zipfile.ZipFile(archive_path, 'r') as zf:
            members = [n for n in zf.namelist() if n and not n.startswith("__MACOSX/")]
            extracted_roots = sorted({n.split("/", 1)[0] for n in members if "/" in n})
            zf.extractall(target_dir)
    elif archive_path.endswith((".tar.xz", ".tar.gz", ".tgz")):
        with tarfile.open(archive_path) as tf:
            members = tf.getnames()
            extracted_roots = sorted({n.split("/", 1)[0] for n in members if "/" in n})
            tf.extractall(target_dir)
    else:
        raise RuntimeError(f"不支援的壓縮格式: {archive_path}")

    os.remove(archive_path)
    log("解壓縮完成")
    return extracted_roots

def download_and_extract(url, target_dir):
    suffix = ".tar.xz" if url.endswith(".tar.xz") else \
             ".tar.gz"  if url.endswith((".tar.gz", ".tgz")) else ".zip"
    tmp = os.path.join(WORKDIR, f"temp_download{suffix}")
    download_file(url, tmp)
    return extract_archive(tmp, target_dir)

# ──────────────────────────────────────────────
# Node.js / npm / pnpm 路徑查找（Linux 版）
# ──────────────────────────────────────────────

def find_node_bin(node_dir):
    """回傳含有 node 可執行檔的目錄"""
    for root, dirs, files in os.walk(node_dir):
        if "node" in files:
            return root
    return None

def find_npm(node_dir):
    for root, dirs, files in os.walk(node_dir):
        if "npm" in files:
            return os.path.join(root, "npm")
    return None

# ──────────────────────────────────────────────
# OpenClaw 來源管理
# ──────────────────────────────────────────────

def get_latest_openclaw_source():
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
        log(f"警告: 取得 stable release 失敗，改用 main 分支 ({e})")
    return "main", OPENCLAW_MAIN_URL

def normalize_openclaw_folder(extracted_roots, app_folder):
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
    log(f"獲取 OpenClaw {'main' if tag == 'main' else f'穩定核心 ({tag})'}...")
    extracted_roots = download_and_extract(source_url, WORKDIR)
    if not normalize_openclaw_folder(extracted_roots, app_folder):
        raise RuntimeError("OpenClaw 解壓縮後找不到可用資料夾")
    source_meta = os.path.join(app_folder, ".launcher-source")
    with open(source_meta, "w", encoding="ascii", newline="\n") as f:
        f.write(f"tag={tag}\n")
        f.write(f"url={source_url}\n")

def try_backup_folder(path, label="舊版本"):
    backup = f"{path}.backup-{int(time.time())}"
    try:
        os.rename(path, backup)
        log(f"已備份{label}至: {backup}")
        return True
    except OSError as e:
        log(f"警告: 無法備份 {path} ({e})")
        return False

# ──────────────────────────────────────────────
# 互動工具
# ──────────────────────────────────────────────

def ask_yes_no(prompt, default=False):
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {hint}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes", "1", "true")

# ──────────────────────────────────────────────
# NVIDIA 設定
# ──────────────────────────────────────────────

def extract_provider_model_id(primary_model, provider="nvidia"):
    if not isinstance(primary_model, str):
        return None
    prefix = f"{provider}/"
    if primary_model.startswith(prefix):
        return primary_model[len(prefix):]
    return None

def build_nvidia_provider_models(selected_model_id=None):
    models = []
    seen = set()
    if selected_model_id and isinstance(selected_model_id, str):
        model_id = selected_model_id.strip()
        if model_id:
            models.append({"id": model_id, "name": model_id})
            seen.add(model_id)
    for item in NVIDIA_PREFERRED_MODELS:
        model_id = item.get("id")
        if model_id and model_id not in seen:
            models.append(item)
            seen.add(model_id)
    return models

def probe_nvidia_model(api_key, model_id):
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8
    }
    req = urllib.request.Request(
        f"{NVIDIA_BASE_URL}/chat/completions",
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

def read_current_nvidia_model_id(config_file):
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
        return extract_provider_model_id(primary, "nvidia")
    except Exception:
        return None

def choose_nvidia_model(api_key, current_model_id=None):
    candidates = []
    if current_model_id:
        candidates.append(current_model_id)
    for item in NVIDIA_PREFERRED_MODELS:
        model_id = item.get("id")
        if model_id and model_id not in candidates:
            candidates.append(model_id)

    log("正在測試 NVIDIA 模型可用性... 約 5-20 秒")
    results = []
    for idx, model_id in enumerate(candidates, start=1):
        ok, detail = probe_nvidia_model(api_key, model_id)
        status = "OK" if ok else f"HTTP {detail}" if isinstance(detail, int) else str(detail)
        print(f"  {idx}. {model_id} ({status})")
        results.append((model_id, ok))

    working = [model_id for model_id, ok in results if ok]
    default_model_id = working[0] if working else (current_model_id or NVIDIA_DEFAULT_MODEL_ID)

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

def maybe_bootstrap_nvidia_config(config_file):
    has_config = os.path.exists(config_file)
    if has_config:
        prompt = "是否要重新設定 NVIDIA NIM API 金鑰？輸入 yes 重新設定，輸入 n 跳過"
    else:
        prompt = "未偵測到 OpenClaw 設定檔，是否改用 NVIDIA NIM API 自動建立設定"

    if not ask_yes_no(prompt, default=False):
        return False

    try:
        api_key = getpass.getpass("請輸入 NVIDIA_API_KEY (nvapi-...): ").strip()
    except Exception:
        api_key = input("請輸入 NVIDIA_API_KEY (nvapi-...): ").strip()

    if not api_key:
        if has_config:
            log("未輸入 NVIDIA_API_KEY，保留現有設定")
        else:
            err("NVIDIA_API_KEY 為空，將改走 setup 精靈")
        return False

    if not api_key.startswith("nvapi-"):
        log("警告: Key 看起來不是 nvapi- 開頭，請確認是否正確")

    current_model_id = read_current_nvidia_model_id(config_file)
    selected_model_id = choose_nvidia_model(api_key, current_model_id)
    selected_primary_model = f"nvidia/{selected_model_id}"

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
        "env": {"NVIDIA_API_KEY": api_key},
        "models": {
            "providers": {
                "nvidia": {
                    "baseUrl": NVIDIA_BASE_URL,
                    "api": "openai-completions",
                    "models": build_nvidia_provider_models(selected_model_id)
                }
            }
        },
        "agents": {
            "defaults": {
                "model": {"primary": selected_primary_model}
            }
        }
    }

    with open(config_file, "w", encoding="utf-8", newline="\n") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")

    log(f"已{'更新' if has_config else '建立'} NVIDIA NIM 設定: {config_file}")
    log(f"已選模型: {selected_primary_model}")
    return True

def ensure_nvidia_models_schema(config_file):
    if not os.path.exists(config_file):
        return False
    try:
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return False

    models   = cfg.get("models") if isinstance(cfg, dict) else None
    providers = models.get("providers") if isinstance(models, dict) else None
    nvidia    = providers.get("nvidia") if isinstance(providers, dict) else None

    if not isinstance(nvidia, dict):
        return False

    primary = (
        cfg.get("agents", {})
           .get("defaults", {})
           .get("model", {})
           .get("primary")
    )
    current_model_id = extract_provider_model_id(primary, "nvidia")
    current_models = nvidia.get("models")
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

    nvidia["models"] = build_nvidia_provider_models(current_model_id)
    with open(config_file, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")

    log("已自動修復 NVIDIA 設定: 補上 models.providers.nvidia.models")
    return True

# ──────────────────────────────────────────────
# 環境初始化（Linux 版）
# ──────────────────────────────────────────────

def init_env():
    os.system('clear')   # Linux 清畫面
    print("=" * 60)
    print(f"  {APP_NAME}")
    print("=" * 60)

    # 1. 配置 Node.js v22（Linux 版）
    node_dir = os.path.join(WORKDIR, "node_env")
    if not os.path.exists(node_dir):
        node_url, node_extracted_name = _get_node_url()
        log("配置 Node.js v22 環境中...")
        download_and_extract(node_url, WORKDIR)
        extracted = os.path.join(WORKDIR, node_extracted_name)
        if os.path.exists(extracted):
            os.rename(extracted, node_dir)
            log("Node.js 配置完成")
        else:
            err("Node.js 解壓縮後找不到預期資料夾")
            pause_exit(1)

    # 2. 確認系統 git（Linux 不需要 MinGit，直接用系統 git）
    git_path = shutil.which("git")
    if git_path:
        log(f"使用系統 git: {git_path}")
    else:
        log("警告: 找不到系統 git，部分功能可能受影響")
        log("可執行 sudo apt install git 安裝")

    # 設定 PATH（Linux 用 : 分隔，執行檔無副檔名）
    node_bin = find_node_bin(node_dir)
    if not node_bin:
        err("找不到 node 執行檔，Node.js 環境可能損壞")
        pause_exit(1)

    os.environ["PATH"] = node_bin + ":" + os.environ["PATH"]
    log(f"PATH 設定完成 | Node: {node_bin}")

    # 3. 配置 OpenClaw
    main_folder   = os.path.join(WORKDIR, "openclaw-main")
    stable_folder = os.path.join(WORKDIR, "openclaw-stable")
    app_folder    = main_folder

    if os.path.exists(main_folder) and is_incompatible_openclaw_snapshot(main_folder):
        log("偵測到 OpenClaw 版本與已發佈套件不相容，將改抓 stable release")
        if not try_backup_folder(main_folder):
            app_folder = stable_folder
            log(f"將改用替代資料夾: {app_folder}")

    if os.path.exists(app_folder) and is_incompatible_openclaw_snapshot(app_folder):
        log(f"目標資料夾 {app_folder} 仍是舊版不相容快照，嘗試重抓 stable")
        if not try_backup_folder(app_folder, "不相容版本"):
            err("OpenClaw 資料夾正被其他程式佔用，請先關閉相關 Node/Gateway 程序後重試")
            pause_exit(1)

    if not os.path.exists(app_folder):
        try:
            install_openclaw_source(app_folder)
        except Exception as e:
            err(f"OpenClaw 下載或解壓失敗: {e}")
            pause_exit(1)

    return app_folder

# ──────────────────────────────────────────────
# 執行（Linux 版）
# ──────────────────────────────────────────────

def run(app_folder):
    node_bin_dir = os.path.join(WORKDIR, "node_env", "bin")

    node_exe  = os.path.join(node_bin_dir, "node")
    npm_path  = os.path.join(node_bin_dir, "npm")
    pnpm_path = os.path.join(node_bin_dir, "pnpm")

    # 確認 node 存在
    if not os.path.exists(node_exe):
        # 有些 Node.js tar 包結構不同，fallback 用 find
        found = find_node_bin(os.path.join(WORKDIR, "node_env"))
        if found:
            node_exe  = os.path.join(found, "node")
            npm_path  = os.path.join(found, "npm")
            pnpm_path = os.path.join(found, "pnpm")
        else:
            err("找不到 node 執行檔，Node.js 環境可能損壞")
            pause_exit(1)

    if not os.path.exists(npm_path):
        err("找不到 npm，Node.js 環境可能損壞")
        pause_exit(1)

    # 安裝 pnpm
    if not os.path.exists(pnpm_path):
        log("安裝 pnpm...")
        subprocess.run([npm_path, "install", "-g", "pnpm"], cwd=app_folder)
        log("pnpm 安裝完成")

    # pnpm install（主專案）
    if not os.path.exists(os.path.join(app_folder, "node_modules")):
        log("首次安裝依賴 (pnpm install)... 約 2-3 分鐘")
        subprocess.run([pnpm_path, "install", "--ignore-scripts"], cwd=app_folder)

    # Build TypeScript
    dist_index = os.path.join(app_folder, "dist", "index.js")
    # 強制修正監聽位址
    subprocess.run(["sed", "-i", "s/127\.0\.0\.1/0.0.0.0/g", dist_index])
    dist_entry = os.path.join(app_folder, "dist", "entry.js")
    if not os.path.exists(dist_index):
        log("編譯 TypeScript... 約 30 秒")
        build_result = subprocess.run([pnpm_path, "run", "build"], cwd=app_folder)
        if build_result.returncode != 0:
            if os.path.exists(dist_index) and os.path.exists(dist_entry):
                log("警告: build 型別檢查失敗，但 runtime dist 已產生，先繼續啟動")
            else:
                err("build 失敗")
                pause_exit(1)
        log("編譯完成")

    # Build UI
    ui_build_flag = os.path.join(app_folder, ".ui_built")
    ui_dir        = os.path.join(app_folder, "ui")
    if not os.path.exists(ui_build_flag) and os.path.exists(ui_dir):
        vite_config     = os.path.join(ui_dir, "vite.config.ts")
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

    # 首次執行 setup
    config_file      = os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")
    nvidia_bootstrapped = maybe_bootstrap_nvidia_config(config_file)
    ensure_nvidia_models_schema(config_file)

    if not os.path.exists(config_file):
        log("首次設定 OpenClaw，正在執行 setup 精靈...")
        subprocess.run([node_exe, dist_index, "setup"], cwd=app_folder)
        log("setup 完成")
    elif nvidia_bootstrapped:
        log("已套用 NVIDIA NIM 設定，略過 setup 精靈")

    # 啟動 Gateway
    PORT     = 8080
    log_path = os.path.join(WORKDIR, "server.log")
    log(f"正在啟動 OpenClaw Gateway (port {PORT})，日誌: {log_path}")

    child_env = os.environ.copy()
    child_env["HOST"] = "0.0.0.0"
    child_env["PATH"] = node_bin_dir + ":" + child_env.get("PATH", "")

    with open(log_path, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [node_exe, dist_index, "gateway", "--port", str(PORT),  "--allow-unconfigured"],
            cwd=app_folder,
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
        pause_exit(1)

    # 讀取 token
    token = ""
    config_path = os.path.join(os.path.expanduser("~"), ".openclaw", "openclaw.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        token = cfg.get("gateway", {}).get("auth", {}).get("token", "")
    except Exception:
        pass

    url = f"http://127.0.0.1:{PORT}/?token={token}" if token else f"http://127.0.0.1:{PORT}"
    webbrowser.open(url)

    if token:
        log(f"Token: {token}")
        log("如果畫面仍顯示未授權，請手動將上方 Token 貼入網頁的「網關令牌」欄位")
    else:
        log(f"找不到 token，請查看 {config_path}")

    print("\n" + "=" * 60)
    print("  OpenClaw Gateway 啟動成功！瀏覽器已開啟")
    print(f"  網址: http://127.0.0.1:{PORT}")
    print(f"  日誌: {log_path}")
    print("=" * 60)
    input("\n按 Enter 關閉此視窗（Gateway 將繼續在背景執行）...")


# ──────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────

if __name__ == "__main__":
    try:
        app_folder = init_env()
        run(app_folder)
    except Exception as e:
        print(f"\n[發生錯誤] {e}")
        import traceback
        traceback.print_exc()
        input("\n請截圖錯誤訊息回報給開發者...")
