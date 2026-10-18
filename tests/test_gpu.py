"""NEW (1.5.0): GET /gpu's arithmetic, with made-up counters (no graphics card needed)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evora_gpu as g

MB = g.MB
CARD = "luid_0x00000000_0x0000D1B4_phys_0"
IGPU = "luid_0x00000000_0x0000AAAA_phys_0"
NAMES = {
    100: r"C:\Users\me\AppData\Local\Programs\Ollama\ollama.exe",
    200: r"C:\Program Files\Evora\.venv\Scripts\python.exe",
    300: r"C:\Windows\explorer.exe",
}


def test_parts_add_up_to_what_the_card_says():
    procs = [
        ("pid_100_" + CARD, 4000 * MB),
        ("pid_100_" + CARD, 100 * MB),     # same process listed twice
        ("pid_200_" + CARD, 900 * MB),
        ("pid_300_" + CARD, 300 * MB),
        ("pid_300_" + IGPU, 50 * MB),      # the CPU's own graphics: ignored
        ("pid_400_" + CARD, 0),
    ]
    adapters = [(CARD, 5300 * MB), (IGPU, 60 * MB)]
    parts, rows = g.breakdown(5800 * MB, procs, adapters, lambda pid: NAMES.get(pid, ""), own_pid=200)
    assert parts == {"ollama_mb": 4100, "evora_mb": 900, "other_mb": 800}
    assert [r["name"] for r in rows] == ["ollama", "Evora", "explorer"]
    assert sum(parts.values()) == 5800


def test_no_counters_means_no_parts():
    assert g.breakdown(1, [], [], lambda pid: "") == (None, [])


def test_classify():
    assert g.classify("ollama_llama_server.exe", 1, 2) == "ollama"
    assert g.classify(r"C:\x\llama-server.exe", 1, 2) == "ollama"
    assert g.classify("VRChat.exe", 1, 2) == "other"
    assert g.classify("python.exe", 2, 2) == "evora"


def test_nvidia_smi_line():
    card = g.parse_nvidia_smi("NVIDIA GeForce RTX 2070 SUPER, 5120, 8192, 37, 55\r\n")
    assert card["total_bytes"] == 8192 * MB and card["used_bytes"] == 5120 * MB
    assert card["util_pct"] == 37 and card["temp_c"] == 55
    assert g.parse_nvidia_smi("X, [N/A], 8192, 1, 2") is None
    assert g.parse_nvidia_smi("") is None


def test_snapshot_never_raises():
    report = g.snapshot("cpu")
    assert "ok" in report
    if not report["ok"]:
        assert report["reason"]
