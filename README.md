wifi-ranker

A Python utility to:
- Evaluate configured Wi‑Fi networks
- Measure upload/download throughput
- Rank available connections
- Restore the original network after testing

Created as a Python practice and AI-assisted development experiment

The program tests only saved Wi-Fi profiles that are currently visible. When `target_networks` in `wifi_ranker.ini` is empty, it considers all such profiles; to exclude unrelated nearby networks, keep only the desired SSIDs in `target_networks`.
