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
# STEP 1: DEPENDENCY CHECK
# =====================================================================
try:
    import speedtest
except ModuleNotFoundError:
    print("\n" + "=" * 70)
    print("DEPENDENCY ERROR: Missing 'speedtest-cli' package.")
    print("=" * 70)
    print("Install the package from Command Prompt or a terminal with:")
    print("\n    pip install speedtest-cli")
    print("=" * 70 + "\n")
    sys.exit(1)

APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
CONFIG_FILE = APP_DIR / "wifi_ranker.ini"


def load_config():
    if not CONFIG_FILE.exists():
        print(f"ERROR: Configuration file not found: {CONFIG_FILE}")
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
SPEEDTEST_THREAD_COUNT = CONFIG.getint("GENERAL", "thread_count", fallback=2)
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
    """Return the SSID of the currently connected Wi-Fi network."""
    try:
        cmd = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True)
        output = cmd.stdout.decode("cp932", errors="ignore")
        for line in output.split("\n"):
            if "SSID" in line and "BSSID" not in line and ":" in line:
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None


def get_saved_pc_profiles():
    """Return the Wi-Fi profiles saved in Windows."""
    try:
        profiles_cmd = subprocess.run(["netsh", "wlan", "show", "profiles"], capture_output=True)
        saved_ssids = []
        for line in profiles_cmd.stdout.decode("cp932", errors="ignore").split("\n"):
            if ":" in line:
                label, profile = line.split(":", 1)
                label = label.strip()
                profile = profile.strip()
                if profile and not label.startswith(("インターフェイス", "Interface")):
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
        print(f"WARNING: Could not initialize a Windows Wi-Fi scan: {ctypes.FormatError(result).strip()}")
        return False

    interface_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
    try:
        result = wlanapi.WlanEnumInterfaces(
            handle, None, ctypes.byref(interface_list)
        )
        if result:
            print(f"WARNING: Could not list Windows Wi-Fi interfaces: {ctypes.FormatError(result).strip()}")
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
                    f"WARNING: Wi-Fi scan request failed for "
                    f"'{interface.strInterfaceDescription}': {ctypes.FormatError(result).strip()}"
                )
            else:
                scan_started = True
        if not scan_started:
            print("WARNING: Windows did not start a Wi-Fi scan on any wireless interface.")
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
            print(f"WARNING: Could not start Wi-Fi rescan ({scan_number + 1}/2): {e}")

        time.sleep(5)
        try:
            networks_cmd = subprocess.run(
                [
                    "netsh", "wlan", "show", "networks", "mode=bssid",
                    f"interface={WIFI_INTERFACE}",
                ],
                capture_output=True,
                check=True,
            )
        except (OSError, subprocess.CalledProcessError) as e:
            print(f"WARNING: Wi-Fi scan result read failed ({scan_number + 1}/2): {e}")
            continue

        output = networks_cmd.stdout.decode("cp932", errors="ignore")
        for line in output.splitlines():
            label, separator, value = line.partition(":")
            ssid = value.strip()
            if separator and label.strip().startswith("SSID ") and ssid and ssid not in visible_ssids:
                visible_ssids.append(ssid)

    return visible_ssids


def switch_to_wifi(ssid):
    """Connect to a saved Wi-Fi profile."""
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
    """Measure bandwidth with a socket timeout."""
    socket.setdefaulttimeout(float(TEST_TIMEOUT))
    for attempt in range(3):
        st = None
        try:
            st = speedtest.Speedtest(secure=True)
            st.get_best_server()
            download_speed = st.download(threads=SPEEDTEST_THREAD_COUNT) / 1_000_000
            upload_speed = st.upload(threads=SPEEDTEST_THREAD_COUNT) / 1_000_000
            return round(download_speed, 2), round(upload_speed, 2)
        except Exception as e:
            dns_lookup_failed = isinstance(e, socket.gaierror) or (
                isinstance(e, URLError) and isinstance(e.reason, socket.gaierror)
            )
            if dns_lookup_failed and attempt < 2:
                print(
                    f"   WARNING: DNS lookup failed; retrying speed test "
                    f"({attempt + 1}/2): {e}"
                )
                time.sleep(1)
                continue
            print(f"   WARNING: Speed test failed or timed out: {e}")
            return 0.0, 0.0
        finally:
            if st and hasattr(st, "results"):
                try:
                    del st
                except Exception:
                    pass


def calculate_score(item):
    dl = item["download"]
    ul = item["upload"]
    if dl == 0 or ul == 0:
        return 0.0
    if OPTIMIZATION_MODE == "download_heavy":
        return (dl * 0.8) + (ul * 0.2)
    elif OPTIMIZATION_MODE == "upload_heavy":
        return (dl * 0.2) + (ul * 0.8)
    elif OPTIMIZATION_MODE == "balanced":
        return 2 * (dl * ul) / (dl + ul)
    return dl


def main():
    starting_wifi = get_current_wifi_ssid()
    target_wifi = starting_wifi

    if starting_wifi:
        print(f"Saved starting network: '{starting_wifi}'")
    else:
        print("You are not currently connected to a Wi-Fi network.")

    leaderboard = []
    best_target_ssid = None

    try:
        if not DISCOVERY_MODE and not COMPANY_WIFI_POOL:
            print("ERROR: discovery_mode is false, but [WIFI] target_networks is empty.")
            print("Add comma-separated SSIDs to wifi_ranker.ini, or enable discovery_mode.")
            return

        print("Checking saved Wi-Fi profiles...")
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
                print(
                    f"Discovery includes saved and visible Wi-Fi networks "
                    f"starting with '{TARGET_PREFIX}'."
                )
        else:
            candidate_profiles = COMPANY_WIFI_POOL

        wifi_targets = [
            ssid for ssid in candidate_profiles
            if ssid in saved_profiles and ssid in visible_profiles
        ]

        if not wifi_targets:
            if not saved_profiles:
                print("ERROR: No saved Wi-Fi profiles were found on this PC.")
            elif not visible_profiles:
                print("ERROR: No visible Wi-Fi networks were found.")
            elif not DISCOVERY_MODE:
                print("ERROR: None of the configured targets are both saved and currently visible.")
            else:
                print(
                    f"ERROR: No saved and visible Wi-Fi profiles start with "
                    f"'{TARGET_PREFIX}'."
                )
            return

        print(f"Wi-Fi networks queued for evaluation: {wifi_targets}\n")
        print(f"Speed-test thread count (ThreadCount): {SPEEDTEST_THREAD_COUNT}")

        if starting_wifi and starting_wifi in wifi_targets:
            print(f"Testing the current network '{starting_wifi}' first...")
            dl, ul = test_current_network_speed_safely()
            leaderboard.append({"ssid": starting_wifi, "download": dl, "upload": ul})
            print(f"   Results -> DL: {dl} Mbps | UL: {ul} Mbps")
            wifi_targets.remove(starting_wifi)

        for ssid in wifi_targets:
            print(f"Switching to target network: {ssid}...")
            if switch_to_wifi(ssid):
                print("   Connected. Starting the speed test...")
                dl, ul = test_current_network_speed_safely()
                leaderboard.append({"ssid": ssid, "download": dl, "upload": ul})
                print(f"   Results -> DL: {dl} Mbps | UL: {ul} Mbps")
            else:
                print(f"   Connection failed (out of range or authentication error): {ssid}")

        if leaderboard:
            print(f"\n--- Wi-Fi Rankings (Mode: {OPTIMIZATION_MODE}) ---")
            ranked_list = sorted(leaderboard, key=calculate_score, reverse=True)
            for rank, item in enumerate(ranked_list, 1):
                print(
                    f"Rank {rank}: {item['ssid']} | DL: {item['download']} Mbps "
                    f"| UL: {item['upload']} Mbps"
                )
            best_target_ssid = ranked_list[0]["ssid"]
        else:
            print("\nNo valid speed-test results were obtained.")

    except Exception as e:
        print(f"ERROR: An execution error occurred: {e}")

    finally:
        if best_target_ssid:
            if best_target_ssid != starting_wifi:
                print("\n" + "=" * 70)
                print(f"Recommendation: '{best_target_ssid}' is the fastest network here.")
                if AUTO_SWITCH:
                    print(f"Keeping the fastest network '{best_target_ssid}' connected.")
                    target_wifi = best_target_ssid
                else:
                    user_input = input(
                        f"Keep the fastest network '{best_target_ssid}' connected? (Y/N): "
                    ).strip().lower()
                    if user_input == "y":
                        print(f"Keeping '{best_target_ssid}' connected.")
                        target_wifi = best_target_ssid
                print("=" * 70)
            else:
                print("\n" + "=" * 70)
                print(
                    f"Your current network '{starting_wifi}' is already the fastest "
                    "available option here."
                )
                print("=" * 70)

        time.sleep(2)

        current_now = get_current_wifi_ssid()
        if target_wifi and current_now != target_wifi:
            print(f"\nReconnecting to the designated network '{target_wifi}'...")
            if switch_to_wifi(target_wifi):
                print("   Successfully restored the intended connection.")
            else:
                print(
                    f"   WARNING: Could not reconnect to '{target_wifi}'. "
                    "Check the network manually."
                )
        else:
            print(f"\nAlready on the designated network '{target_wifi}'; no reconnection needed.")


if __name__ == "__main__":
    main()
