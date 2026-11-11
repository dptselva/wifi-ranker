import configparser
import ctypes
import subprocess
import time
import socket
import sys
from pathlib import Path
from ctypes import wintypes
from urllib.error import URLError

# =====================================================================
# 🔍 STEP 1: DEPENDENCY CHECK / 依存関係の確認
# =====================================================================
try:
    import speedtest
except ModuleNotFoundError:
    print("\n" + "="*70)
    print("❌ 依存関係エラー: 'speedtest-cli' パッケージが見つかりません。")
    print("❌ Dependency error: the 'speedtest-cli' package is not installed.")
    print("="*70)
    print("スクリプトを安全に実行するために、コマンドプロンプトで")
    print("以下のコマンドを実行してライブラリをインストールしてください:")
    print("\n    pip install speedtest-cli")
    print("="*70 + "\n")
    sys.exit(1)

# --- ⚙️ CONFIGURATION & CONSTANTS / 各種設定 ---
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "wifi_ranker.ini"

def load_config():
    if not CONFIG_FILE.exists():
        print(f"❌ 設定ファイルが見つかりません: {CONFIG_FILE}")
        print(f"❌ Configuration file not found: {CONFIG_FILE}")
        sys.exit(1)

    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_FILE, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError) as e:
        print(f"❌ 設定ファイルを読み込めません: {CONFIG_FILE}")
        print(f"❌ Could not read the configuration file: {CONFIG_FILE}")
        print(f"   詳細 (Details): {e}")
        sys.exit(1)
    return config


CONFIG = load_config()

def get_boolean_setting(section, option, fallback=False):
    value = CONFIG.get(section, option, fallback="").strip()
    if not value:
        return fallback
    try:
        return configparser.ConfigParser.BOOLEAN_STATES[value.lower()]
    except KeyError:
        raise ValueError(
            f"{section}.{option} の設定値が正しくありません: {value!r} "
            "(true/false、yes/no、on/off、1/0 を指定してください; "
            "expected true/false, yes/no, on/off, or 1/0)"
        )


def get_positive_int_setting(section, option, fallback=None):
    try:
        value = CONFIG.getint(section, option, fallback=fallback)
    except (configparser.Error, ValueError) as e:
        raise ValueError(
            f"{section}.{option} は正の整数で指定してください "
            f"(must be a positive integer): {e}"
        ) from e
    if value <= 0:
        raise ValueError(
            f"{section}.{option} は正の整数で指定してください "
            "(must be a positive integer)"
        )
    return value


try:
    WIFI_INTERFACE = CONFIG.get("GENERAL", "wifi_interface")
    CONNECT_TIMEOUT = get_positive_int_setting("GENERAL", "connect_timeout")
    TEST_TIMEOUT = get_positive_int_setting("GENERAL", "test_timeout")
    SPEEDTEST_THREAD_COUNT = get_positive_int_setting(
        "GENERAL", "thread_count", fallback=2
    )
    OPTIMIZATION_MODE = CONFIG.get("GENERAL", "optimization_mode")
    AUTO_SWITCH = get_boolean_setting("GENERAL", "auto_switch", fallback=False)
    COMPANY_WIFI_POOL = [
        ssid.strip()
        for ssid in CONFIG.get("WIFI", "target_networks", fallback="").split(",")
        if ssid.strip()
    ]
    DISCOVERY_MODE = get_boolean_setting("WIFI", "discovery_mode", fallback=False)
    TARGET_PREFIXES = [
        prefix.strip()
        for prefix in CONFIG.get("WIFI", "target_prefix", fallback="").split(",")
        if prefix.strip()
    ]
except (configparser.Error, ValueError) as e:
    print("❌ 設定値が正しくありません。wifi_ranker.ini の項目名と値を確認してください。")
    print("❌ Invalid configuration. Check the option names and values in wifi_ranker.ini.")
    print(f"   詳細 (Details): {e}")
    sys.exit(1)

def print_runtime_settings():
    def setting_source(section, option, default=None, blank_uses_default=False):
        if CONFIG.has_option(section, option):
            if CONFIG.get(section, option).strip():
                return "INI設定値"
            if default is not None and blank_uses_default:
                return f"INI空欄のため既定値 {default}"
            return "INI設定値（空欄）"
        if default is not None:
            return f"未設定のため既定値 {default}"
        return "必須項目"

    optimization_effects = {
        "download_heavy": "ダウンロード80%・アップロード20%で評価",
        "upload_heavy": "ダウンロード20%・アップロード80%で評価",
        "balanced": "ダウンロードとアップロードの調和平均で評価",
    }
    optimization_effect = optimization_effects.get(
        OPTIMIZATION_MODE,
        "認識されない値のため、ダウンロード速度のみで評価",
    )
    auto_switch_effect = (
        "評価スコア1位へ自動切替"
        if AUTO_SWITCH
        else "切替前に利用者へ確認"
    )
    discovery_effect = (
        "保存済みかつ検出可能なSSIDを自動検索"
        if DISCOVERY_MODE
        else "target_networksで指定されたSSIDを使用"
    )
    target_networks_value = ", ".join(COMPANY_WIFI_POOL) or "（空欄）"
    target_networks_effect = (
        "自動検出が有効のため使用しない"
        if DISCOVERY_MODE
        else "自動検出が無効の場合に対象として使用"
    )
    target_prefix_value = ", ".join(TARGET_PREFIXES) or "（空欄）"
    if not DISCOVERY_MODE:
        target_prefix_effect = "自動検出が無効のため使用しない"
    elif not TARGET_PREFIXES:
        target_prefix_effect = "接頭辞による絞り込みなし"
    else:
        target_prefix_effect = "自動検出するSSIDをこの接頭辞で絞り込み"

    print("\n =========================================================== ")
    print("\n【実行設定 / Run settings】")
    print("[GENERAL]")
    print(
        f"  Wi-Fiインターフェース (wifi_interface): {WIFI_INTERFACE}"
        f"（無線LANの検索・接続に使用）"
        f" [{setting_source('GENERAL', 'wifi_interface')}]"
    )
    print(
        f"  接続待ち時間 (connect_timeout): {CONNECT_TIMEOUT}秒"
        f"（Wi-Fi接続を待つ時間）"
        f" [{setting_source('GENERAL', 'connect_timeout')}]"
    )
    print(
        f"  速度測定タイムアウト (test_timeout): {TEST_TIMEOUT}秒"
        f"（通信応答を待つ時間）"
        f" [{setting_source('GENERAL', 'test_timeout')}]"
    )
    print(
        f"  評価方式 (optimization_mode): {OPTIMIZATION_MODE}"
        f"（{optimization_effect}）"
        f" [{setting_source('GENERAL', 'optimization_mode')}]"
    )
    print(
        f"  自動切替 (auto_switch): "
        f"{'有効 / true' if AUTO_SWITCH else '無効 / false'}"
        f"（{auto_switch_effect}）"
        f" [{setting_source('GENERAL', 'auto_switch', 'false', blank_uses_default=True)}]"
    )
    print(
        f"  測定スレッド数 (thread_count): {SPEEDTEST_THREAD_COUNT}"
        f"（速度測定に使用）"
        f" [{setting_source('GENERAL', 'thread_count', '2')}]"
    )
    print("[WIFI]")
    print(
        f"  自動検出 (discovery_mode): "
        f"{'有効 / true' if DISCOVERY_MODE else '無効 / false'}"
        f"（{discovery_effect}）"
        f" [{setting_source('WIFI', 'discovery_mode', 'false', blank_uses_default=True)}]"
    )
    print(
        f"  対象SSID (target_networks): {target_networks_value}"
        f"（{target_networks_effect}）"
        f" [{setting_source('WIFI', 'target_networks', '空欄')}]"
    )
    print(
        f"  接頭辞フィルター (target_prefix): {target_prefix_value}"
        f"（{target_prefix_effect}）"
        f" [{setting_source('WIFI', 'target_prefix', '空欄')}]"
    )
    print("\n =========================================================== ")
    print()

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
    """SSIDから接続に使う保存済みWi-Fiプロファイル名への対応表を返します。"""
    profiles_cmd = subprocess.run(
        ["netsh", "wlan", "show", "profiles"],
        capture_output=True,
        check=True,
    )
    profile_names = []
    for line in profiles_cmd.stdout.decode("cp932", errors="ignore").splitlines():
        label, separator, value = line.partition(":")
        profile = value.strip()
        if (
            separator
            and profile
            and label.strip().casefold()
            in ("all user profile", "すべてのユーザー プロファイル")
            and profile not in profile_names
        ):
            profile_names.append(profile)

    ssid_to_profile = {}
    for profile in profile_names:
        profile_cmd = subprocess.run(
            ["netsh", "wlan", "show", "profile", f"name={profile}"],
            capture_output=True,
            check=True,
        )
        for line in profile_cmd.stdout.decode("cp932", errors="ignore").splitlines():
            label, separator, value = line.partition(":")
            normalized_label = "".join(label.casefold().split())
            ssid = value.strip()
            if (
                separator
                and normalized_label in ("ssidname", "ssid名")
                and ssid
            ):
                ssid_to_profile.setdefault(ssid, profile)
    return ssid_to_profile

def request_wifi_scan():
    """Windows WLAN サービスに無線インターフェースのスキャンを要求します。"""
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
        print("⚠️ Windows WLAN サービスを初期化できないため、Wi-Fiを再スキャンできません。")
        print("⚠️ Could not initialize the Windows WLAN service for a Wi-Fi scan.")
        print(f"   詳細 (Details): {ctypes.FormatError(result).strip()}")
        return False

    interface_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
    try:
        result = wlanapi.WlanEnumInterfaces(
            handle, None, ctypes.byref(interface_list)
        )
        if result:
            print("⚠️ 無線インターフェースの一覧を取得できないため、Wi-Fiを再スキャンできません。")
            print("⚠️ Could not list wireless interfaces for the Wi-Fi scan.")
            print(f"   詳細 (Details): {ctypes.FormatError(result).strip()}")
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
                    f"⚠️ インターフェース「{interface.strInterfaceDescription}」の"
                    "Wi-Fi再スキャンを開始できませんでした。"
                )
                print(
                    f"⚠️ Could not start a Wi-Fi scan for "
                    f"'{interface.strInterfaceDescription}'."
                )
                print(f"   詳細 (Details): {ctypes.FormatError(result).strip()}")
            else:
                scan_started = True
        if not scan_started:
            print("⚠️ どの無線インターフェースでもWi-Fi再スキャンを開始できませんでした。")
            print("⚠️ Windows did not start a Wi-Fi scan on any wireless interface.")
        return scan_started
    finally:
        if interface_list:
            wlanapi.WlanFreeMemory(interface_list)
        wlanapi.WlanCloseHandle(handle, None)

def get_visible_wifi_ssids():
    """Wi-Fiを2回スキャンし、検出したSSIDをまとめます。"""
    visible_ssids = []
    for scan_number in range(2):
        if scan_number:
            time.sleep(5)
        try:
            request_wifi_scan()
        except OSError as e:
            print(f"⚠️ Wi-Fiの再スキャンを開始できませんでした（{scan_number + 1}/2回目）。")
            print(f"   詳細 (Details): {e}")

        time.sleep(5)
        try:
            networks_cmd = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=bssid", f"interface={WIFI_INTERFACE}"],
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"⚠️ 周辺のWi-Fiネットワークを検索できませんでした（{scan_number + 1}/2回目）。")
            print(f"   詳細 (Details): {e}")
            continue

        output = networks_cmd.stdout.decode("cp932", errors="ignore")
        for line in output.splitlines():
            label, separator, value = line.partition(":")
            ssid = value.strip()
            if separator and label.strip().startswith("SSID ") and ssid and ssid not in visible_ssids:
                visible_ssids.append(ssid)

    return visible_ssids

def switch_to_wifi(ssid, profile_name=None):
    """指定したWi-Fiプロファイルへの切り替えを行います"""
    if get_current_wifi_ssid() == ssid:
        return True

    subprocess.run(
        [
            "netsh",
            "wlan",
            "connect",
            f"name={profile_name or ssid}",
            f"interface={WIFI_INTERFACE}",
        ],
        shell=False,
        capture_output=True,
    )
    
    start_time = time.time()
    while time.time() - start_time < CONNECT_TIMEOUT:
        if get_current_wifi_ssid() == ssid:
            time.sleep(2)
            return True
        time.sleep(1)
    return False

def test_current_network_speed_safely():
    """スレッド数を指定して帯域を測定します"""
    socket.setdefaulttimeout(float(TEST_TIMEOUT))
    for attempt in range(3):
        st = None
        try:
            st = speedtest.Speedtest(secure=True)
            st.get_best_server()
            download_speed = st.download(threads=SPEEDTEST_THREAD_COUNT) / 1_000_000
            upload_speed = st.upload(threads=SPEEDTEST_THREAD_COUNT) / 1_000_000
            return round(download_speed, 2), round(upload_speed, 2)
        except (speedtest.SpeedtestException, socket.timeout, OSError, URLError) as e:
            dns_lookup_failed = isinstance(e, socket.gaierror) or (
                isinstance(e, URLError) and isinstance(e.reason, socket.gaierror)
            )
            if dns_lookup_failed and attempt < 2:
                print(
                    f"   ⚠️ DNSの名前解決に失敗しました。速度測定を再試行します"
                    f"（再試行 {attempt + 1}/2回目）。"
                )
                print(f"      詳細 (Details): {e}")
                time.sleep(1)
                continue
            print("   ⚠️ 速度を測定できませんでした。通信状態や時間をおいて再度お試しください。")
            print(f"      詳細 (Details): {e}")
            return None

def calculate_score(item):
    dl = item['download']
    ul = item['upload']
    if dl == 0 or ul == 0: return 0.0
    if OPTIMIZATION_MODE == 'download_heavy': return (dl * 0.8) + (ul * 0.2)
    elif OPTIMIZATION_MODE == 'upload_heavy': return (dl * 0.2) + (ul * 0.8)
    elif OPTIMIZATION_MODE == 'balanced': return 2 * (dl * ul) / (dl + ul)
    return dl

def main():
    print_runtime_settings()
    starting_wifi = get_current_wifi_ssid()
    target_wifi = starting_wifi 
    
    if starting_wifi:
        print(f"💾 初期ネットワークを保存しました (Saved base): '{starting_wifi}'")
    else:
        print("ℹ️ 現在、Wi-Fiネットワークに接続されていません。")

    leaderboard = []
    best_target_ssid = None
    saved_profile_by_ssid = {}

    try:
        if not DISCOVERY_MODE and not COMPANY_WIFI_POOL:
            print("❌ discovery_mode が false ですが、[WIFI] の target_networks が空です。")
            print("   wifi_ranker.ini の target_networks に対象SSIDをカンマ区切りで指定するか、")
            print("   discovery_mode を true にしてください。")
            print("❌ discovery_mode is false, but [WIFI] target_networks is empty.")
            print("   Set target SSIDs in target_networks or enable discovery_mode.")
            return

        print("🔍 パスワード保存済みプロファイルを確認中...")
        saved_profile_by_ssid = get_saved_pc_profiles()
        visible_ssids = get_visible_wifi_ssids()
        print("\n保存済みプロファイルのSSID (Saved profile SSIDs):", list(saved_profile_by_ssid))
        print("\n現在検出されているSSID (Visible SSIDs):", visible_ssids)
                
        if DISCOVERY_MODE:
            candidate_profiles = [
                ssid for ssid in saved_profile_by_ssid
                if ssid in visible_ssids and (
                    not TARGET_PREFIXES
                    or any(ssid.casefold().startswith(prefix.casefold()) for prefix in TARGET_PREFIXES)
                )
            ]
            if TARGET_PREFIXES:
                print(
                    f"ℹ️ 自動検出では {', '.join(TARGET_PREFIXES)} のいずれかで始まる"
                    "保存済み・検出済みWi-Fiのみを使用します。"
                )
        else:
            candidate_profiles = COMPANY_WIFI_POOL
        wifi_targets = [
            ssid for ssid in candidate_profiles
            if ssid in saved_profile_by_ssid and ssid in visible_ssids
        ]
        
        if not wifi_targets:
            if not saved_profile_by_ssid:
                print("❌ このPCに保存済みのWi-Fiプロファイルが見つかりません。")
            elif not visible_ssids:
                print("❌ 現在検出できるWi-Fiネットワークがありません。")
            elif not DISCOVERY_MODE:
                print("❌ 設定された対象のうち、保存済みで現在検出できるWi-Fiプロファイルがありません。")
            else:
                print(
                    f"❌ {', '.join(TARGET_PREFIXES)} のいずれかで始まる"
                    "保存済み・現在検出できるWi-Fiプロファイルがありません。"
                )
            return

        print(f"\n📋 評価対象のWi-Fiリスト (Queued targets): {wifi_targets}\n")
        print(f"⚙️ 速度測定スレッド数 (ThreadCount): {SPEEDTEST_THREAD_COUNT}")

        # 🚀 現在接続中のネットワークを最初に測定
        if starting_wifi and starting_wifi in wifi_targets:
            print(f"⚡ 現在の接続先 '{starting_wifi}' をそのまま即時測定します...")
            measurement = test_current_network_speed_safely()
            if measurement is not None:
                dl, ul = measurement
                leaderboard.append({"ssid": starting_wifi, "download": dl, "upload": ul})
                print(f"   📊 測定結果 -> DL: {dl} Mbps | UL: {ul} Mbps")
            else:
                print("   ⚠️ 測定に失敗したため、ランキング対象から除外します。")
            wifi_targets.remove(starting_wifi)

        # 🔄 残りの対象ネットワークを順番に測定
        for ssid in wifi_targets:
            print(f"🔄 対象ネットワークへ切り替え中: {ssid}...")
            if switch_to_wifi(ssid, saved_profile_by_ssid[ssid]):
                print("   ✅ 接続完了。速度測定を開始します...")
                measurement = test_current_network_speed_safely()
                if measurement is not None:
                    dl, ul = measurement
                    leaderboard.append({"ssid": ssid, "download": dl, "upload": ul})
                    print(f"   📊 測定結果 -> DL: {dl} Mbps | UL: {ul} Mbps")
                else:
                    print("   ⚠️ 測定に失敗したため、ランキング対象から除外します。")
            else:
                print(f"   ❌ 圏外または認証エラーにより接続失敗しました: {ssid}")

        # 🏆 結果発表
        if leaderboard:
            print(f"\n👑 --- 評価結果ランキング（評価モード: {OPTIMIZATION_MODE}）---")
            ranked_list = sorted(leaderboard, key=calculate_score, reverse=True)
            for rank, item in enumerate(ranked_list, 1):
                print(
                    f"第 {rank} 位: {item['ssid']} | "
                    f"ダウンロード: {item['download']} Mbps | "
                    f"アップロード: {item['upload']} Mbps"
                )
            
            # 評価スコア順に並べたリストの先頭を最上位候補にします。
            best_target_ssid = ranked_list[0]['ssid']
        else:
            print("\n📊 有効な速度測定データを取得できませんでした。")

    except Exception as e:
        print("❌ エラーが発生したため、処理を中断します。")
        print(f"   詳細 (Details): {e}")

    finally:
        # 🔌 人間の意思決定を挟むプロンプト判定
        if best_target_ssid:
            if best_target_ssid != starting_wifi:
                print("\n" + "="*70)
                print(
                    f"💡 評価スコアの最上位候補: '{best_target_ssid}' "
                    "（設定した評価モードで1位のネットワークです）。"
                )
                if AUTO_SWITCH:
                    print(f"🚀 評価スコア1位のネットワーク '{best_target_ssid}' を維持して終了します。")
                    target_wifi = best_target_ssid 
                else:
                    if sys.stdin is None:
                        print("ℹ️ 標準入力が利用できないため、初期ネットワークへ復帰します。")
                    else:
                        try:
                            user_input = input(
                                f"👉 評価スコア1位の '{best_target_ssid}' への接続を維持しますか？"
                                "（はい: Y / いいえ: N）: "
                            ).strip().lower()
                        except (EOFError, OSError, ValueError) as e:
                            print("ℹ️ 回答を読み取れないため、開始時のネットワークへの復帰を試みます。")
                            print(f"   詳細 (Details): {e}")
                        else:
                            if user_input == 'y':
                                print(f"🚀 評価スコア1位のネットワーク '{best_target_ssid}' を維持して終了します。")
                                target_wifi = best_target_ssid
                print("="*70)
            else:
                print("\n" + "="*70)
                print(
                    f"✅ 判定: 現在接続中の '{starting_wifi}' が、"
                    "設定した評価モードで1位のネットワークです。"
                )
                print("="*70)
        
        time.sleep(2)
        
        # 🔄 CLEANUP: ロールバック安全装置
        current_now = get_current_wifi_ssid()
        if target_wifi and current_now != target_wifi:
            print(f"\n" + "-"*70)
            print(f"🧹 クリーンアップ: 指定されたネットワーク '{target_wifi}' へ切り替え・復帰中...")
            profile_name = saved_profile_by_ssid.get(target_wifi, target_wifi)
            if switch_to_wifi(target_wifi, profile_name):
                print("   🔌 正常に指定の接続状況に復元されました!")
            else:
                print(f"   ⚠️ '{target_wifi}' への接続復帰に失敗しました。手動でネットワークを確認してください。")
        elif not starting_wifi:
            if current_now:
                print(
                    f"\n🧹 クリーンアップ: 起動時はWi-Fi未接続でした。"
                    f"選択された復帰先がないため、現在の接続 '{current_now}' を維持します。"
                )
                print(
                    f"   Cleanup: Started offline; no return target was selected, "
                    f"so the current connection '{current_now}' will be kept."
                )
            else:
                print(
                    "\n🧹 クリーンアップ: 起動時はWi-Fi未接続でした。"
                    "選択された復帰先がなく、終了時の接続も検出されませんでした。"
                )
                print(
                    "   Cleanup: Started offline; no return target was selected, "
                    "and no connection was detected at shutdown."
                )
        else:
            print(f"\n🧹 クリーンアップ: 目的のネットワーク '{target_wifi}' 上に正しく留まっています。復帰処理は不要です。")

if __name__ == "__main__":
    main()
