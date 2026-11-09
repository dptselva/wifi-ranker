import configparser
import ctypes
import subprocess
import time
import socket
import sys
from pathlib import Path
from ctypes import wintypes

# =====================================================================
# 🔍 STEP 1: DEPENDENCY CHECK / 依存関係の確認
# =====================================================================
try:
    import speedtest
except ModuleNotFoundError:
    print("\n" + "="*70)
    print("❌ 依存関係エラー: 'speedtest-cli' パッケージが見つかりません。")
    print("❌ DEPENDENCY ERROR: Missing 'speedtest-cli' package.")
    print("="*70)
    print("スクリプトを安全に実行するために、コマンドプロンプトで")
    print("以下のコマンドを実行してライブラリをインストールしてください:")
    print("\n    pip install speedtest-cli")
    print("="*70 + "\n")
    sys.exit(1)

# --- ⚙️ CONFIGURATION & CONSTANTS / 各種設定 ---
CONFIG_FILE = Path(__file__).resolve().with_name("wifi_ranker.ini")

def load_config():
    if not CONFIG_FILE.exists():
        print(f"❌ 設定ファイルが見つかりません: {CONFIG_FILE}")
        sys.exit(1)

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding="utf-8")
    return config


CONFIG = load_config()

def get_boolean_setting(section, option, fallback=False):
    value = CONFIG.get(section, option, fallback="").strip()
    if not value:
        return fallback
    try:
        return configparser.ConfigParser.BOOLEAN_STATES[value.lower()]
    except KeyError:
        raise ValueError(f"Not a boolean: {value}")


WIFI_INTERFACE = CONFIG.get("GENERAL", "wifi_interface")
CONNECT_TIMEOUT = CONFIG.getint("GENERAL", "connect_timeout")
TEST_TIMEOUT = CONFIG.getint("GENERAL", "test_timeout")
OPTIMIZATION_MODE = CONFIG.get("GENERAL", "optimization_mode")
AUTO_SWITCH = get_boolean_setting("GENERAL", "auto_switch", fallback=False)
COMPANY_WIFI_POOL = [
    ssid.strip()
    for ssid in CONFIG.get("WIFI", "target_networks", fallback="").split(",")
    if ssid.strip()
]
DISCOVERY_MODE = get_boolean_setting("WIFI", "discovery_mode", fallback=False)
TARGET_PREFIX = CONFIG.get("WIFI", "target_prefix", fallback="").strip()

def get_current_wifi_ssid():
    """現在接続中のWi-Fiネットワーク名(SSID)を安全に取得します"""
    try:
        cmd = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True)
        output = cmd.stdout.decode('cp932', errors='ignore')
        for line in output.split('\n'):
            if "SSID" in line and "BSSID" not in line and ":" in line:
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None

def get_saved_pc_profiles():
    """PCのシステム内部に保存されている有効なプロファイル一覧を返します"""
    try:
        profiles_cmd = subprocess.run(["netsh", "wlan", "show", "profiles"], capture_output=True)
        saved_ssids = []
        for line in profiles_cmd.stdout.decode('cp932', errors='ignore').split('\n'):
            if ":" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    label = parts[0].strip()
                    profile = parts[1].strip()
                    if profile and not label.startswith("インターフェイス") and not label.startswith("Interface"):
                        if profile not in saved_ssids: 
                            saved_ssids.append(profile)
        return saved_ssids
    except Exception:
        return []

def request_wifi_scan():
    """Ask the Windows WLAN service to scan its wireless interfaces."""
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    class WLAN_INTERFACE_INFO(ctypes.Structure):
        _fields_ = [
            ("InterfaceGuid", GUID),
            ("strInterfaceDescription", wintypes.WCHAR * 256),
            ("isState", wintypes.DWORD),
        ]

    class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
        _fields_ = [
            ("dwNumberOfItems", wintypes.DWORD),
            ("dwIndex", wintypes.DWORD),
            ("InterfaceInfo", WLAN_INTERFACE_INFO * 1),
        ]

    wlanapi = ctypes.WinDLL("wlanapi")
    wlanapi.WlanOpenHandle.argtypes = [
        wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.HANDLE),
    ]
    wlanapi.WlanOpenHandle.restype = wintypes.DWORD
    wlanapi.WlanEnumInterfaces.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p,
        ctypes.POINTER(ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)),
    ]
    wlanapi.WlanEnumInterfaces.restype = wintypes.DWORD
    wlanapi.WlanScan.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(GUID),
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    wlanapi.WlanScan.restype = wintypes.DWORD
    wlanapi.WlanFreeMemory.argtypes = [ctypes.c_void_p]
    wlanapi.WlanCloseHandle.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    handle = wintypes.HANDLE()
    negotiated_version = wintypes.DWORD()
    result = wlanapi.WlanOpenHandle(
        2, None, ctypes.byref(negotiated_version), ctypes.byref(handle)
    )
    if result:
        print(f"⚠️ Windows Wi-Fi scan could not be initialized: {ctypes.FormatError(result).strip()}")
        return False

    interface_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
    try:
        result = wlanapi.WlanEnumInterfaces(
            handle, None, ctypes.byref(interface_list)
        )
        if result:
            print(f"⚠️ Windows Wi-Fi interfaces could not be listed: {ctypes.FormatError(result).strip()}")
            return False

        scan_started = False
        for index in range(interface_list.contents.dwNumberOfItems):
            address = (
                ctypes.addressof(interface_list.contents.InterfaceInfo)
                + index * ctypes.sizeof(WLAN_INTERFACE_INFO)
            )
            interface = ctypes.cast(address, ctypes.POINTER(WLAN_INTERFACE_INFO)).contents
            result = wlanapi.WlanScan(
                handle, ctypes.byref(interface.InterfaceGuid), None, None, None
            )
            if result:
                print(
                    f"⚠️ Windows Wi-Fi scan request failed for "
                    f"'{interface.strInterfaceDescription}': {ctypes.FormatError(result).strip()}"
                )
            else:
                scan_started = True
        if not scan_started:
            print("⚠️ Windows did not start a Wi-Fi scan on any wireless interface.")
        return scan_started
    finally:
        if interface_list:
            wlanapi.WlanFreeMemory(interface_list)
        wlanapi.WlanCloseHandle(handle, None)

def get_visible_wifi_ssids():
    """Scan twice and combine visible SSIDs to reduce transient omissions."""
    visible_ssids = []
    for scan_number in range(2):
        if scan_number:
            time.sleep(5)
        try:
            request_wifi_scan()
        except OSError as e:
            print(f"⚠️ Wi-Fi再スキャンを開始できませんでした ({scan_number + 1}/2): {e}")

        time.sleep(5)
        try:
            networks_cmd = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=bssid", f"interface={WIFI_INTERFACE}"],
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"⚠️ 周辺Wi-Fiネットワークの検索 ({scan_number + 1}/2) に失敗しました: {e}")
            continue

        output = networks_cmd.stdout.decode("cp932", errors="ignore")
        for line in output.splitlines():
            label, separator, value = line.partition(":")
            ssid = value.strip()
            if separator and label.strip().startswith("SSID ") and ssid and ssid not in visible_ssids:
                visible_ssids.append(ssid)

    return visible_ssids

def switch_to_wifi(ssid):
    """指定したWi-Fiプロファイルへの切り替えを行います"""
    if get_current_wifi_ssid() == ssid:
        return True

    cmd = f'netsh wlan connect name="{ssid}" interface="{WIFI_INTERFACE}"'
    subprocess.run(cmd, shell=True, capture_output=True)
    
    start_time = time.time()
    while time.time() - start_time < CONNECT_TIMEOUT:
        if get_current_wifi_ssid() == ssid:
            time.sleep(2)
            return True
        time.sleep(1)
    return False

def test_current_network_speed_safely():
    """スレッド数を1に固定してルーター負荷を抑えつつ帯域を測定します"""
    socket.setdefaulttimeout(float(TEST_TIMEOUT))
    st = None
    try:
        st = speedtest.Speedtest(secure=True)
        st.get_best_server()
        download_speed = st.download(threads=2) / 1_000_000 
        upload_speed = st.upload(threads=2) / 1_000_000
        return round(download_speed, 2), round(upload_speed, 2)
    except (speedtest.SpeedtestException, socket.timeout, Exception) as e:
        print(f"   ⚠️ 測定エラーまたはタイムアウト: {e}")
        return 0.0, 0.0
    finally:
        if st and hasattr(st, 'results'):
            try:
                del st
            except Exception:
                pass

def calculate_score(item):
    dl = item['download']
    ul = item['upload']
    if dl == 0 or ul == 0: return 0.0
    if OPTIMIZATION_MODE == 'download_heavy': return (dl * 0.8) + (ul * 0.2)
    elif OPTIMIZATION_MODE == 'upload_heavy': return (dl * 0.2) + (ul * 0.8)
    elif OPTIMIZATION_MODE == 'balanced': return 2 * (dl * ul) / (dl + ul)
    return dl

def main():
    starting_wifi = get_current_wifi_ssid()
    target_wifi = starting_wifi 
    
    if starting_wifi:
        print(f"💾 初期ネットワークを保存しました (Saved base): '{starting_wifi}'")
    else:
        print("ℹ️ 現在、Wi-Fiネットワークに接続されていません。")

    leaderboard = []
    best_target_ssid = None

    try:
        if not DISCOVERY_MODE and not COMPANY_WIFI_POOL:
            print("❌ discovery_mode is false, but [WIFI] target_networks is empty.")
            print("   Add one or more comma-separated SSIDs to wifi_ranker.ini, or enable discovery_mode.")
            return

        print("🔍 パスワード保存済みプロファイルを確認中...")
        saved_profiles = get_saved_pc_profiles()
        visible_profiles = get_visible_wifi_ssids()
        print("Saved profiles:", saved_profiles)
        print("Visible profiles:", visible_profiles)
                
        if DISCOVERY_MODE:
            candidate_profiles = [
                ssid for ssid in saved_profiles
                if ssid in visible_profiles and ssid.casefold().startswith(TARGET_PREFIX.casefold())
            ]
            if TARGET_PREFIX:
                print(f"ℹ️ 自動検出では '{TARGET_PREFIX}' で始まる保存済み・検出済みWi-Fiのみを使用します。")
        else:
            candidate_profiles = COMPANY_WIFI_POOL
        wifi_targets = [
            ssid for ssid in candidate_profiles
            if ssid in saved_profiles and ssid in visible_profiles
        ]
        
        if not wifi_targets:
            if not saved_profiles:
                print("❌ このPCに保存済みのWi-Fiプロファイルが見つかりません。")
            elif not visible_profiles:
                print("❌ 現在検出できるWi-Fiネットワークがありません。")
            elif not DISCOVERY_MODE:
                print("❌ 設定された対象のうち、保存済みで現在検出できるWi-Fiプロファイルがありません。")
            else:
                print(f"❌ '{TARGET_PREFIX}' で始まる保存済み・現在検出できるWi-Fiプロファイルがありません。")
            return

        print(f"📋 評価対象のWi-Fiリスト (Queued targets): {wifi_targets}\n")

        # 🚀 PHASE 1: 現在の接続先をそのまま即時測定
        if starting_wifi and starting_wifi in wifi_targets:
            print(f"⚡ 現在の接続先 '{starting_wifi}' をそのまま即時測定します（シングルスレッド）...")
            dl, ul = test_current_network_speed_safely()
            leaderboard.append({"ssid": starting_wifi, "download": dl, "upload": ul})
            print(f"   📊 測定結果 -> DL: {dl} Mbps | UL: {ul} Mbps")
            wifi_targets.remove(starting_wifi)

        # 🔄 PHASE 2: 残りの対象ネットワークを巡回
        for ssid in wifi_targets:
            print(f"🔄 対象ネットワークへ切り替え中: {ssid}...")
            if switch_to_wifi(ssid):
                print("   ✅ 接続完了。速度測定を開始します（シングルスレッド）...")
                dl, ul = test_current_network_speed_safely()
                leaderboard.append({"ssid": ssid, "download": dl, "upload": ul})
                print(f"   📊 測定結果 -> DL: {dl} Mbps | UL: {ul} Mbps")
            else:
                print(f"   ❌ 圏外または認証エラーにより接続失敗しました: {ssid}")

        # 🏆 結果発表
        if leaderboard:
            print(f"\n👑 --- 評価結果ランキング (Mode: {OPTIMIZATION_MODE}) ---")
            ranked_list = sorted(leaderboard, key=calculate_score, reverse=True)
            for rank, item in enumerate(ranked_list, 1):
                print(f"第 {rank} 位 (Rank {rank}): {item['ssid']} | DL: {item['download']} Mbps | UL: {item['upload']} Mbps")
            
            # Fix: 2次元リストアクセス構文のエラーを完全に修正
            best_target_ssid = ranked_list[0]['ssid']
        else:
            print("\n📊 有効な速度測定データを取得できませんでした。")

    except Exception as e:
        print(f"❌ 実行エラーが発生しました: {e}")

    finally:
        # 🔌 人間の意思決定を挟むプロンプト判定
        if best_target_ssid:
            if best_target_ssid != starting_wifi:
                print("\n" + "="*70)
                print(f"💡 提案 (Recommendation): 現在のエリアでは '{best_target_ssid}' が最速です。")
                if AUTO_SWITCH:
                    print(f"🚀 了解しました。最速回線 '{best_target_ssid}' を維持して終了します。")
                    target_wifi = best_target_ssid 
                else:
                    user_input = input(f"👉 このまま最速の '{best_target_ssid}' に接続を維持しますか？ (Y/N): ").strip().lower()
                    if user_input == 'y':
                        print(f"🚀 了解しました。最速回線 '{best_target_ssid}' を維持して終了します。")
                        target_wifi = best_target_ssid
                print("="*70)
            else:
                print("\n" + "="*70)
                print(f"✅ 判定: 現在接続中の '{starting_wifi}' が、このエリアで最も良好なダウンロード速度です！")
                print("="*70)
        
        time.sleep(2)
        
        # 🔄 CLEANUP: ロールバック安全装置
        current_now = get_current_wifi_ssid()
        if target_wifi and current_now != target_wifi:
            print(f"\n" + "-"*70)
            print(f"🧹 クリーンアップ: 指定されたネットワーク '{target_wifi}' へ切り替え・復帰中...")
            if switch_to_wifi(target_wifi):
                print("   🔌 正常に指定の接続状況に復元されました!")
            else:
                print(f"   ⚠️ '{target_wifi}' への接続復帰に失敗しました。手動でネットワークを確認してください。")
        else:
            print(f"\n🧹 クリーンアップ: 目的のネットワーク '{target_wifi}' 上に正しく留まっています。復帰処理は不要です。")

if __name__ == "__main__":
    main()
