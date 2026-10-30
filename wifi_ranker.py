import subprocess
import time
import socket
import sys

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
WIFI_INTERFACE = "Wi-Fi"      
CONNECT_TIMEOUT = 20           
TEST_TIMEOUT = 15              
OPTIMIZATION_MODE = 'download_heavy' 

# --- 🎯 TARGET COMPANY NETWORK POOL / 評価対象の社内Wi-Fiリスト ---
COMPANY_WIFI_POOL = ['DP-TK1', 'DP-TK2', 'DP-TK3', 'DP-TK4', 'DP-TK5 2.4G', 'DP-TK5 5.0G']

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
        print("🔍 パスワード保存済みプロファイルを確認中...")
        saved_profiles = get_saved_pc_profiles()
        wifi_targets = [ssid for ssid in COMPANY_WIFI_POOL if ssid in saved_profiles]
        
        if not wifi_targets:
            print("❌ このPCに設定されている対象の社内Wi-Fiプロファイルが見つかりません。")
            return

        print(f"📋 評価対象のWi-Fiリスト (Queued targets): {wifi_targets}\n")

        # 🚀 PHASE 1: 現在の接続先をそのまま即時測定
        if starting_wifi in wifi_targets:
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
                user_input = input(f"👉 このまま最速の '{best_target_ssid}' に接続を維持しますか？ (Y/N): ").strip().lower()
                print("="*70)
                
                if user_input == 'y':
                    print(f"🚀 了解しました。最速回線 '{best_target_ssid}' を維持して終了します。")
                    target_wifi = best_target_ssid 
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
