#!/usr/bin/env python3
import urllib.request, urllib.error, json, sys, io, subprocess, os, tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8000"
results = {}

def api_get(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    resp = urllib.request.urlopen(req)
    return resp.status, json.loads(resp.read())

def api_get_raw(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    resp = urllib.request.urlopen(req)
    return resp.status, resp.read()

print("Obtaining auth token...")
data = json.dumps({"email": "pro@test.com", "password": "Test1234!"}).encode()
req = urllib.request.Request(BASE + "/api/v1/auth/login", data=data, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
token = json.loads(resp.read())["access_token"]
headers = {"Authorization": "Bearer " + token}
print("Token obtained: " + token[:20] + "...")
print()

print("=" * 60)
print("TEST 1: /api/v1/auth/me (cursor_factory bug)")
print("=" * 60)
try:
    status, body = api_get("/api/v1/auth/me", headers)
    email = body.get("email", "")
    print("  Response keys: " + str(list(body.keys())))
    print("  Email: " + str(email))
    results[1] = "PASS"
except urllib.error.HTTPError as e:
    err = e.read().decode("utf-8", errors="replace")[:500]
    print("  HTTP " + str(e.code) + ": " + err)
    results[1] = "FAIL"
except Exception as e:
    print("  Exception: " + str(e))
    results[1] = "FAIL"
print("  >> " + results[1])
print()

print("=" * 60)
print("TEST 2: Image search POST /api/v1/search/intelligent")
print("=" * 60)
try:
    proc = subprocess.run(["docker", "exec", "ipwatch_backend", "sh", "-c", "find /app/bulletins -maxdepth 4 -type f -name '*.jpg' 2>/dev/null | head -1"], capture_output=True, text=True, timeout=30)
    img_path = proc.stdout.strip()
    if img_path:
        print("  Found image: " + img_path)
        tmp_img = os.path.join(tempfile.gettempdir(), "test_logo.jpg")
        subprocess.run(["docker", "cp", "ipwatch_backend:" + img_path, tmp_img], capture_output=True, timeout=15)
        if os.path.exists(tmp_img):
            proc = subprocess.run(["curl", "-s", "-H", "Authorization: Bearer " + token, "-F", "query=test", "-F", "image=@" + tmp_img, "-F", "per_page=3", BASE + "/api/v1/search/intelligent"], capture_output=True, text=True, timeout=120)
            print("  Response: " + proc.stdout[:500])
            try:
                rj = json.loads(proc.stdout)
                results[2] = "FAIL" if ("detail" in rj and "error" in str(rj.get("detail", "")).lower()) else "PASS"
            except Exception:
                results[2] = "FAIL"
        else:
            print("  Could not copy image"); results[2] = "SKIP"
    else:
        print("  No image found"); results[2] = "SKIP"
except Exception as e:
    print("  Exception: " + str(e)); results[2] = "FAIL"
print("  >> " + results[2])
print()

print("=" * 60)
print("TEST 3: /api/v1/leads/feed (was 500 before)")
print("=" * 60)
try:
    status, body = api_get("/api/v1/leads/feed?page=1&page_size=3", headers)
    if isinstance(body, list):
        print("  Response: list with " + str(len(body)) + " items"); results[3] = "PASS"
    elif isinstance(body, dict) and "detail" in body:
        print("  Error: " + str(body["detail"])); results[3] = "FAIL"
    else:
        results[3] = "PASS"
except urllib.error.HTTPError as e:
    err = e.read().decode("utf-8", errors="replace")[:500]
    print("  HTTP " + str(e.code) + ": " + err); results[3] = "FAIL"
print("  >> " + results[3])
print()

print("=" * 60)
print("TEST 4: /api/v1/attorneys/search")
print("=" * 60)
try:
    status, body = api_get("/api/v1/attorneys/search?query=patent&limit=3", headers)
    if isinstance(body, list):
        print("  Got " + str(len(body)) + " results")
    elif isinstance(body, dict):
        print("  Keys: " + str(list(body.keys())))
    results[4] = "PASS"
except urllib.error.HTTPError as e:
    err = e.read().decode("utf-8", errors="replace")[:500]
    print("  HTTP " + str(e.code) + ": " + err); results[4] = "FAIL"
print("  >> " + results[4])
print()

print("=" * 60)
print("TEST 5: opposition-timeline.js static file")
print("=" * 60)
try:
    status, content = api_get_raw("/static/js/components/opposition-timeline.js")
    print("  HTTP " + str(status) + " - size: " + str(len(content)) + " bytes"); results[5] = "PASS"
except urllib.error.HTTPError as e:
    print("  HTTP " + str(e.code)); results[5] = "FAIL"
print("  >> " + results[5])
print()

print("=" * 60)
print("TEST 6: /api/v1/search/quick NIKE")
print("=" * 60)
try:
    status, body = api_get("/api/v1/search/quick?query=NIKE&per_page=3", headers)
    if isinstance(body, dict) and "results" in body:
        r = body["results"]
        print("  Got " + str(len(r)) + " results")
        for item in r[:3]:
            name = item.get("trademark_name", item.get("name", "N/A"))
            score = item.get("similarity_score", item.get("score", "N/A"))
            print("    - " + str(name) + ": score=" + str(score))
        results[6] = "PASS"
    elif isinstance(body, list):
        print("  Got " + str(len(body)) + " results (list)"); results[6] = "PASS"
    else:
        results[6] = "FAIL" if "detail" in body else "PASS"
except urllib.error.HTTPError as e:
    err = e.read().decode("utf-8", errors="replace")[:500]
    print("  HTTP " + str(e.code) + ": " + err); results[6] = "FAIL"
print("  >> " + results[6])
print()

print("=" * 60)
print("TEST 7: VRAM check")
print("=" * 60)
try:
    proc = subprocess.run(["docker", "exec", "ipwatch_backend", "nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
    vram_output = proc.stdout.strip()
    if not vram_output:
        proc = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.free", "--format=csv,noheader"], capture_output=True, text=True, timeout=15)
        vram_output = proc.stdout.strip()
        print("  VRAM (host): " + vram_output)
    else:
        print("  VRAM: " + vram_output)
    used = vram_output.split(",")[0].strip() if vram_output else "N/A"
    results[7] = used
    print("  Pre-rebuild was: 8255 MiB")
except Exception as e:
    print("  Exception: " + str(e)); results[7] = "ERROR"
print()
print()
print("=" * 60)
print("FINAL SUMMARY")
print("=" * 60)
fmt = "{:<5} | {:<35} | {:<15}"
print(fmt.format("TEST", "Description", "Result"))
print(fmt.format("-----", "-" * 35, "-" * 15))
print(fmt.format("1", "GET /api/v1/auth/me", str(results.get(1, "N/A"))))
print(fmt.format("2", "Image search POST intelligent", str(results.get(2, "N/A"))))
print(fmt.format("3", "Leads feed", str(results.get(3, "N/A"))))
print(fmt.format("4", "Attorney search", str(results.get(4, "N/A"))))
print(fmt.format("5", "opposition-timeline.js", str(results.get(5, "N/A"))))
print(fmt.format("6", "Quick search NIKE", str(results.get(6, "N/A"))))
print(fmt.format("7", "VRAM usage", str(results.get(7, "N/A"))))
passed = sum(1 for k, v in results.items() if k < 7 and v == "PASS")
failed = sum(1 for k, v in results.items() if k < 7 and v == "FAIL")
skipped = sum(1 for k, v in results.items() if k < 7 and v == "SKIP")
print()
print("Passed: " + str(passed) + "/6, Failed: " + str(failed) + "/6, Skipped: " + str(skipped) + "/6")
