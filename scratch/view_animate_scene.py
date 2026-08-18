import urllib.request

url = "https://huggingface.co/spaces/alexnasa/Wan2.2-Animate-ZEROGPU/raw/main/app.py"
req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req, timeout=10) as resp:
    lines = resp.read().decode("utf-8", errors="ignore").splitlines()

for i in range(450, 560):
    if i < len(lines):
        safe_line = lines[i].encode("ascii", errors="replace").decode("ascii")
        print(f"{i+1}: {safe_line}")
