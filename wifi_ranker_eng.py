import subprocess
import time
import socket
import sys

# =====================================================================
# 🔍 STEP 1: DEPENDENCY CHECK
# =====================================================================
try:
    import speedtest
except ModuleNotFoundError:
    print("\n" + "="*70)
    print("❌ DEPENDENCY ERROR: Missing 'speedtest-cli' package.")
    print("="*70)
    print("To make sure this script runs safely, please install the library")
    print("manually via your Command Prompt or terminal by running:")
    print("\n    pip install speedtest-cli")
    print("="*70 + "\n")
    sys.exit(1)

# --- CONFIGURATION CONSTRAINTS & PREFERENCES ---
WIFI_INTERFACE = "Wi-Fi"      # Name of your Windows network adapter
CONNECT_TIMEOUT = 20           # Stability fix: Extended to 20 seconds
TEST_TIMEOUT = 15              # Max seconds allowed for the speedtest socket
OPTIMIZATION_MODE = 'download_heavy' # Optimized for download speeds

# --- TARGET COMPANY NETWORK POOL ---
COMPANY_WIFI_POOL = ['DP-TK1', 'DP-TK2', 'DP-TK3', 'DP-TK4', 'DP-TK5 2.4G', 'DP-TK5 5.0G']

def get_current_wifi_ssid():
    """Safely retrieves the name of the Wi-Fi network currently connected."""
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
    """Returns only the valid profile names saved inside Windows database."""
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

def switch_to_wifi(ssid):
    """Force-triggers an execution switch command via netsh."""
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
    """Tests metrics forcing a single-thread calculation to protect network interfaces."""
    socket.setdefaulttimeout(float(TEST_TIMEOUT))
    st = None
    try:
        st = speedtest.Speedtest(secure=True)
        st.get_best_server()
        download_speed = st.download(threads=1) / 1_000_000 
        upload_speed = st.upload(threads=1) / 1_000_000
        return round(download_speed, 2), round(upload_speed, 2)
    except (speedtest.SpeedtestException, socket.timeout, Exception) as e:
        print(f"   ⚠️ Network speedtest interrupted/timed out: {e}")
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
        print(f"💾 Saved your starting network: '{starting_wifi}'")
    else:
        print("ℹ️ You are not currently connected to any Wi-Fi network.")

    leaderboard = []
    best_target_ssid = None

    try:
        print("🔍 Checking system database profiles...")
        saved_profiles = get_saved_pc_profiles()
        wifi_targets = [ssid for ssid in COMPANY_WIFI_POOL if ssid in saved_profiles]
        
        if not wifi_targets:
            print("❌ None of your target company profiles were found in your saved Windows list.")
            return

        print(f"📋 Targets queued for evaluation: {wifi_targets}\n")

        # 🚀 PHASE 1: Test active connection immediately
        if starting_wifi in wifi_targets:
            print(f"⚡ Testing current network '{starting_wifi}' immediately (Single-Threaded)...")
            dl, ul = test_current_network_speed_safely()
            leaderboard.append({"ssid": starting_wifi, "download": dl, "upload": ul})
            print(f"   📊 Performance -> DL: {dl} Mbps | UL: {ul} Mbps")
            wifi_targets.remove(starting_wifi)

        # 🔄 PHASE 2: Cycle directly through target list
        for ssid in wifi_targets:
            print(f"🔄 Forcing switch to target network: {ssid}...")
            if switch_to_wifi(ssid):
                print("   ✅ Connected successfully! Commencing speed metrics (Single-Threaded)...")
                dl, ul = test_current_network_speed_safely()
                leaderboard.append({"ssid": ssid, "download": dl, "upload": ul})
                print(f"   📊 Performance -> DL: {dl} Mbps | UL: {ul} Mbps")
            else:
                print(f"   ❌ Network out of range or handshake failed on {ssid}. Skipping.")

        # 🏆 EVALUATE SCORES
        if leaderboard:
            print(f"\n👑 --- WI-FI LEADERBOARD (Mode: {OPTIMIZATION_MODE}) ---")
            ranked_list = sorted(leaderboard, key=calculate_score, reverse=True)
            for rank, item in enumerate(ranked_list, 1):
                print(f"Rank {rank}: {item['ssid']} | DL: {item['download']} Mbps | UL: {item['upload']} Mbps")
            
            # Fix: Query index 0 dictionary object before requesting data text key
            best_target_ssid = ranked_list[0]['ssid']
        else:
            print("\n📊 Evaluation finished. No speed metrics were successfully captured.")

    except Exception as e:
        print(f"❌ Execution error encountered: {e}")

    finally:
        # 🔌 HUMAN-IN-THE-LOOP PROMPT LAYER
        if best_target_ssid:
            if best_target_ssid != starting_wifi:
                print("\n" + "="*70)
                print(f"💡 Recommendation: '{best_target_ssid}' provides the best download speed here.")
                user_input = input(f"👉 Would you like to switch to '{best_target_ssid}' permanently? (Y/N): ").strip().lower()
                print("="*70)
                
                if user_input == 'y':
                    print(f"🚀 Keeping connection established on '{best_target_ssid}'.")
                    target_wifi = best_target_ssid 
            else:
                print("\n" + "="*70)
                print(f"✅ Notice: Your current network '{starting_wifi}' is already the fastest available option here!")
                print("="*70)

        time.sleep(2)

        # 🔄 CLEANUP LAYER
        current_now = get_current_wifi_ssid()
        if target_wifi and current_now != target_wifi:
            print(f"\n" + "-"*70)
            print(f"🧹 Cleaning up: Reconnecting to your designated network '{target_wifi}'...")
            if switch_to_wifi(target_wifi):
                print("   🔌 Successfully restored your intended connection state!")
            else:
                print(f"   ⚠️ Could not reconnect to '{target_wifi}' automatically. Configuration sync timed out.")
        else:
            print(f"\n🧹 Cleaning up: Successfully resting on your chosen network '{target_wifi}'. No migration required.")

if __name__ == "__main__":
    main()
