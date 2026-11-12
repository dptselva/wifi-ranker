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
    print("❌ Dependency error:")
    print("   The 'speedtest-cli' package is not installed.")
    print("="*70)
    print("To run the script safely, open Command Prompt and")
    print("run the following command to install the package:")
    print("\n    pip install speedtest-cli")
    print("="*70 + "\n")
    sys.exit(1)

# --- ⚙️ CONFIGURATION & CONSTANTS / 各種設定 ---
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "wifi_ranker.ini"

def load_config():
    if not CONFIG_FILE.exists():
        print(f"❌ Configuration error: {CONFIG_FILE}")
        print(f"❌ Configuration file not found: {CONFIG_FILE}")
        sys.exit(1)

    config = configparser.ConfigParser()
    try:
        config.read(CONFIG_FILE, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeError) as e:
        print(f"❌ Configuration error: {CONFIG_FILE}")
        print(f"❌ Could not read the configuration file: {CONFIG_FILE}")
        print(f"   Details: {e}")
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
            f"{section}.{option} has an invalid value: {value!r} "
            " (expected "
            "true/false, yes/no, on/off, or 1/0)"
        )


def get_positive_int_setting(section, option, fallback=None):
    try:
        value = CONFIG.getint(section, option, fallback=fallback)
    except (configparser.Error, ValueError) as e:
        raise ValueError(
            f"{section}.{option} "
            f"must be a positive integer: {e}"
        ) from e
    if value <= 0:
        raise ValueError(
            f"{section}.{option} "
            "must be a positive integer"
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
    print("❌ Invalid configuration:")
    print("   Check the option names and values in wifi_ranker.ini.")
    print(f"   Details: {e}")
    sys.exit(1)

def print_runtime_settings():
    def setting_source(section, option, default=None, blank_uses_default=False):
        if CONFIG.has_option(section, option):
            if CONFIG.get(section, option).strip():
                return "INI setting"
            if default is not None and blank_uses_default:
                return f"blank in INI; using default {default}"
            return "INI setting (blank)"
        if default is not None:
            return f"not set; using default {default}"
        return "required option"

    optimization_effects = {
        "download_heavy": "80% download / 20% upload weighting",
        "upload_heavy": "20% download / 80% upload weighting",
        "balanced": "harmonic mean of download and upload speeds",
    }
    optimization_effect = optimization_effects.get(
        OPTIMIZATION_MODE,
        "unrecognized value; scoring by download speed only",
    )
    auto_switch_effect = (
        "automatically switch to the highest-scoring network"
        if AUTO_SWITCH
        else "ask before switching"
    )
    discovery_effect = (
        "automatically discover saved and visible SSIDs"
        if DISCOVERY_MODE
        else "use SSIDs specified in target_networks"
    )
    target_networks_value = ", ".join(COMPANY_WIFI_POOL) or "(blank)"
    target_networks_effect = (
        "not used when automatic discovery is enabled"
        if DISCOVERY_MODE
        else "used as targets when automatic discovery is disabled"
    )
    target_prefix_value = ", ".join(TARGET_PREFIXES) or "(blank)"
    if not DISCOVERY_MODE:
        target_prefix_effect = "not used when automatic discovery is disabled"
    elif not TARGET_PREFIXES:
        target_prefix_effect = "no prefix filtering"
    else:
        target_prefix_effect = "filter discovered SSIDs by this prefix"

    print("\n =========================================================== ")
    print("\n[Run Settings]")
    print("[GENERAL]")
    print(
        f"  Wi-Fi interface (wifi_interface): {WIFI_INTERFACE}"
        f" (used to scan for and connect to Wi-Fi)"
        f" [{setting_source('GENERAL', 'wifi_interface')}]"
    )
    print(
        f"  Connection timeout (connect_timeout): {CONNECT_TIMEOUT} seconds"
        f" (time to wait for a Wi-Fi connection)"
        f" [{setting_source('GENERAL', 'connect_timeout')}]"
    )
    print(
        f"  Speed test timeout (test_timeout): {TEST_TIMEOUT} seconds"
        f" (time to wait for network responses)"
        f" [{setting_source('GENERAL', 'test_timeout')}]"
    )
    print(
        f"  Scoring mode (optimization_mode): {OPTIMIZATION_MODE}"
        f"（{optimization_effect}）"
        f" [{setting_source('GENERAL', 'optimization_mode')}]"
    )
    print(
        f"  Automatic switching (auto_switch): "
        f"{'Enabled / true' if AUTO_SWITCH else 'Disabled / false'}"
        f"（{auto_switch_effect}）"
        f" [{setting_source('GENERAL', 'auto_switch', 'false', blank_uses_default=True)}]"
    )
    print(
        f"  Speed test threads (thread_count): {SPEEDTEST_THREAD_COUNT}"
        f" (used for speed testing)"
        f" [{setting_source('GENERAL', 'thread_count', '2')}]"
    )
    print("[WIFI]")
    print(
        f"  Automatic discovery (discovery_mode): "
        f"{'Enabled / true' if DISCOVERY_MODE else 'Disabled / false'}"
        f"（{discovery_effect}）"
        f" [{setting_source('WIFI', 'discovery_mode', 'false', blank_uses_default=True)}]"
    )
    print(
        f"  Target SSIDs (target_networks): {target_networks_value}"
        f"（{target_networks_effect}）"
        f" [{setting_source('WIFI', 'target_networks', 'blank')}]"
    )
    print(
        f"  Prefix filter (target_prefix): {target_prefix_value}"
        f"（{target_prefix_effect}）"
        f" [{setting_source('WIFI', 'target_prefix', 'blank')}]"
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
            if len(ssid) >= 2 and ssid.startswith('"') and ssid.endswith('"'):
                ssid = ssid[1:-1]
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
        print("⚠️ Wi-Fi rescan unavailable: WLAN service initialization failed.")
        print("⚠️ Could not initialize the Windows WLAN service for a Wi-Fi scan.")
        print(f"   Details: {ctypes.FormatError(result).strip()}")
        return False

    interface_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
    try:
        result = wlanapi.WlanEnumInterfaces(
            handle, None, ctypes.byref(interface_list)
        )
        if result:
            print("⚠️ Wi-Fi rescan unavailable: could not list wireless interfaces.")
            print("⚠️ Could not list wireless interfaces for the Wi-Fi scan.")
            print(f"   Details: {ctypes.FormatError(result).strip()}")
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
                    f"⚠️ Interface scan failed: {interface.strInterfaceDescription} "
                    "could not be started."
                )
                print(
                    f"⚠️ Could not start a Wi-Fi scan for "
                    f"'{interface.strInterfaceDescription}'."
                )
                print(f"   Details: {ctypes.FormatError(result).strip()}")
            else:
                scan_started = True
        if not scan_started:
            print("⚠️ No wireless interface was available for Wi-Fi scanning.")
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
            print(f"⚠️ Could not start a Wi-Fi rescan (attempt {scan_number + 1}/2).")
            print(f"   Details: {e}")

        time.sleep(5)
        try:
            networks_cmd = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=bssid", f"interface={WIFI_INTERFACE}"],
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"⚠️ Could not search for nearby Wi-Fi networks (attempt {scan_number + 1}/2).")
            print(f"   Details: {e}")
            continue

        output = networks_cmd.stdout.decode("cp932", errors="ignore")
        for line in output.splitlines():
            label, separator, value = line.partition(":")
            ssid = value.strip()
            if separator and label.strip().startswith("SSID ") and ssid and ssid not in visible_ssids:
                visible_ssids.append(ssid)

    return visible_ssids

def switch_to_wifi(ssid, profile_name=None, retry_once=False):
    """指定したWi-Fiプロファイルへの切り替えを行います"""
    if get_current_wifi_ssid() == ssid:
        return True

    command = [
        "netsh",
        "wlan",
        "connect",
        f"name={profile_name or ssid}",
        f"interface={WIFI_INTERFACE}",
    ]
    attempts = 2 if retry_once else 1
    for attempt in range(attempts):
        try:
            result = subprocess.run(command, shell=False, capture_output=True)
        except OSError as e:
            if attempt + 1 < attempts:
                print(f"⚠️ Could not run the Wi-Fi connection request; retrying: {e}")
                time.sleep(2)
                continue
            print(f"⚠️ Could not run netsh to connect to Wi-Fi: {e}")
            return False

        command_output = (result.stdout + result.stderr).decode(
            "cp932", errors="ignore"
        ).strip()
        if result.returncode != 0:
            if attempt + 1 < attempts:
                print(
                    "⚠️ The Wi-Fi connection request failed; retrying "
                    f"(netsh exit code {result.returncode})."
                )
                if command_output:
                    print(f"   Details: {command_output}")
                time.sleep(2)
                continue
            print(
                "⚠️ The Wi-Fi connection request failed "
                f"(netsh exit code {result.returncode})."
            )
            print(f"   Details: {command_output or 'netsh returned no details.'}")
            return False

        start_time = time.monotonic()
        while time.monotonic() - start_time < CONNECT_TIMEOUT:
            if get_current_wifi_ssid() == ssid:
                time.sleep(2)
                return True
            time.sleep(1)

        current_ssid = get_current_wifi_ssid()
        if current_ssid == ssid:
            time.sleep(2)
            return True
        if attempt + 1 < attempts:
            print(
                f"⚠️ Could not confirm a connection to '{ssid}' within "
                f"{CONNECT_TIMEOUT} seconds; retrying."
            )
            time.sleep(2)
            continue

        print(
            f"⚠️ Could not confirm a connection to '{ssid}' within "
            f"{CONNECT_TIMEOUT} seconds "
            f"(current SSID: {current_ssid or 'not connected'})."
        )
        print(f"   netsh: {command_output or 'no additional details'}")
        return False

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
                    f"   ⚠️ DNS lookup failed. Retrying the speed test "
                    f"(retry {attempt + 1}/2)."
                )
                print(f"      Details: {e}")
                time.sleep(1)
                continue
            print("   ⚠️ Could not measure the speed. Check your connection and try again later.")
            print(f"      Details: {e}")
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
        print(f"💾 Starting network saved: '{starting_wifi}'")
    else:
        print("ℹ️ No Wi-Fi network is currently connected.")

    leaderboard = []
    best_target_ssid = None
    saved_profile_by_ssid = {}

    try:
        if not DISCOVERY_MODE and not COMPANY_WIFI_POOL:
            print("❌ No target Wi-Fi networks are configured.")
            print("   Specify target SSIDs in target_networks as a comma-separated list, or")
            print("   set discovery_mode to true.")
            print("❌ discovery_mode is false, but [WIFI] target_networks is empty.")
            print("   Set target SSIDs in target_networks or enable discovery_mode.")
            return

        print("🔍 Checking saved Wi-Fi profiles...")
        saved_profile_by_ssid = get_saved_pc_profiles()
        visible_ssids = get_visible_wifi_ssids()
        print("\nSaved Wi-Fi profiles (SSID-to-profile mapping):", list(saved_profile_by_ssid))
        print("\nCurrently visible SSIDs:", visible_ssids)
                
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
                    f"ℹ️ Automatic discovery will only use saved and visible Wi-Fi networks "
                    f"whose names start with one of: {', '.join(TARGET_PREFIXES)}."
                )
        else:
            candidate_profiles = COMPANY_WIFI_POOL
        wifi_targets = [
            ssid for ssid in candidate_profiles
            if ssid in saved_profile_by_ssid and ssid in visible_ssids
        ]
        
        if not wifi_targets:
            if not saved_profile_by_ssid:
                print("❌ No saved Wi-Fi profiles were found on this PC.")
            elif not visible_ssids:
                print("❌ No Wi-Fi networks are currently visible.")
            elif not DISCOVERY_MODE:
                print("❌ None of the configured target Wi-Fi profiles are both saved and currently visible.")
            elif TARGET_PREFIXES:
                print(
                    f"❌ Wi-Fi names starting with one of: {', '.join(TARGET_PREFIXES)} "
                    "have no saved profiles currently visible."
                )
            else:
                print("❌ No saved Wi-Fi networks are currently visible.")
            return

        print(f"\n📋 Wi-Fi targets: {wifi_targets}\n")
        print(f"⚙️ Speed test threads: {SPEEDTEST_THREAD_COUNT}")

        # 🚀 現在接続中のネットワークを最初に測定
        if starting_wifi and starting_wifi in wifi_targets:
            print(f"⚡ Immediately testing the currently connected network '{starting_wifi}'...")
            measurement = test_current_network_speed_safely()
            if measurement is not None:
                dl, ul = measurement
                leaderboard.append({"ssid": starting_wifi, "download": dl, "upload": ul})
                print(f"   📊 Results -> DL: {dl} Mbps | UL: {ul} Mbps")
            else:
                print("   ⚠️ Test failed; excluding this network from the rankings.")
            wifi_targets.remove(starting_wifi)

        # 🔄 残りの対象ネットワークを順番に測定
        for ssid in wifi_targets:
            print(f"🔄 Switching to target network: {ssid}...")
            if switch_to_wifi(ssid, saved_profile_by_ssid[ssid]):
                print("   ✅ Connected. Starting speed test...")
                measurement = test_current_network_speed_safely()
                if measurement is not None:
                    dl, ul = measurement
                    leaderboard.append({"ssid": ssid, "download": dl, "upload": ul})
                    print(f"   📊 Results -> DL: {dl} Mbps | UL: {ul} Mbps")
                else:
                    print("   ⚠️ Test failed; excluding this network from the rankings.")
            else:
                print(f"   ❌ Could not connect (out of range or authentication error): {ssid}")

        # 🏆 結果発表
        if leaderboard:
            print(f"\n👑 --- Wi-Fi Rankings (scoring mode: {OPTIMIZATION_MODE}) ---")
            ranked_list = sorted(leaderboard, key=calculate_score, reverse=True)
            for rank, item in enumerate(ranked_list, 1):
                print(
                    f"Rank {rank}: {item['ssid']} | "
                    f"Download: {item['download']} Mbps | "
                    f"Upload: {item['upload']} Mbps"
                )
            
            # 評価スコア順に並べたリストの先頭を最上位候補にします。
            best_target_ssid = ranked_list[0]['ssid']
        else:
            print("\n📊 No valid speed test results were obtained.")

    except Exception as e:
        print("❌ An error occurred; stopping the process.")
        print(f"   Details: {e}")

    finally:
        # 🔌 人間の意思決定を挟むプロンプト判定
        if best_target_ssid:
            if best_target_ssid != starting_wifi:
                print("\n" + "="*70)
                print(
                    f"💡 Top-ranked network: '{best_target_ssid}' "
                    "(ranked first in the selected scoring mode)."
                )
                if AUTO_SWITCH:
                    print(f"🚀 Keeping the top-ranked network '{best_target_ssid}' connected.")
                    target_wifi = best_target_ssid 
                else:
                    if sys.stdin is None:
                        print("ℹ️ Standard input is unavailable; returning to the starting network.")
                    else:
                        try:
                            user_input = input(
                                f"👉 Keep the top-ranked network '{best_target_ssid}' connected? "
                                "(Y: yes / N: no): "
                            ).strip().lower()
                        except (EOFError, OSError, ValueError) as e:
                            print("ℹ️ Could not read your response; attempting to return to the starting network.")
                            print(f"   Details: {e}")
                        else:
                            if user_input == 'y':
                                print(f"🚀 Keeping the top-ranked network '{best_target_ssid}' connected.")
                                target_wifi = best_target_ssid
                print("="*70)
            else:
                print("\n" + "="*70)
                print(
                    f"✅ Result: The currently connected network '{starting_wifi}' "
                    "is ranked first in the selected scoring mode."
                )
                print("="*70)
        
        time.sleep(2)
        
        # 🔄 CLEANUP: ロールバック安全装置
        current_now = get_current_wifi_ssid()
        if target_wifi and current_now != target_wifi:
            print(f"\n" + "-"*70)
            print(f"\n🧹 Cleanup: Switching to or restoring the selected network '{target_wifi}'...")
            profile_name = saved_profile_by_ssid.get(target_wifi, target_wifi)
            if switch_to_wifi(target_wifi, profile_name, retry_once=True):
                print("   🔌 The selected network connection was restored successfully!")
            else:
                print(f"   ⚠️ Could not reconnect to '{target_wifi}'. Check the network manually.")
        elif not starting_wifi:
            if current_now:
                print(
                    f"\n🧹 Cleanup: Wi-Fi was disconnected at startup. "
                    f"No return network was selected, so the current connection '{current_now}' will be kept."
                )
                print(
                    f"   Cleanup: Started offline; no return target was selected, "
                    f"so the current connection '{current_now}' will be kept."
                )
            else:
                print(
                    "\n🧹 Cleanup: Wi-Fi was disconnected at startup. "
                    "No return network was selected, and no connection was detected at shutdown."
                )
                print(
                    "   Cleanup: Started offline; no return target was selected, "
                    "and no connection was detected at shutdown."
                )
        else:
            print(f"\n🧹 Cleanup: Already connected to the selected network '{target_wifi}'. No restoration is needed.")

if __name__ == "__main__":
    main()
