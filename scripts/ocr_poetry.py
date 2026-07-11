#!/usr/bin/env python3
"""Batch OCR poetry PDF pages using qwen3-vl:4b via Ollama."""
import base64, json, os, requests, subprocess, sys, time
from pathlib import Path

PDF = Path.home() / "Documents/NickPoem/nickpoem_footnote-0917-inverted_070221.pdf"
OUT = Path("/tmp/nickpoem_ocr_qwen.txt")
PROGRESS = Path("/tmp/nickpoem_ocr_progress.txt")
START_PAGE = int(sys.argv[1]) if len(sys.argv) > 1 else 1
END_PAGE = int(sys.argv[2]) if len(sys.argv) > 2 else 193

# Convert pages to images
os.makedirs("/tmp/poem_batch", exist_ok=True)

results = []
for page in range(START_PAGE, END_PAGE + 1):
    png = f"/tmp/poem_batch/pg{page:03d}.png"
    
    # Convert page to image
    base = f"/tmp/poem_batch/pg{page:03d}"
    subprocess.run([
        "pdftoppm", "-r", "100", "-png",
        "-f", str(page), "-l", str(page),
        str(PDF), base
    ], check=True, capture_output=True)
    # pdftoppm appends -NNN; find actual file
    import glob
    matches = glob.glob(f"{base}*.png")
    if not matches:
        print(f"ERROR: no output for page {page}", flush=True)
        continue
    png = matches[0]
    
    # Read and base64
    with open(png, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()
    
    # OCR via Ollama
    t0 = time.time()
    try:
        resp = requests.post("http://192.168.1.8:11434/api/generate", json={
            "model": "qwen3-vl:8b",
            "prompt": "输出图片中所有文字内容，直接输出不要任何说明。保留原有格式和换行。",
            "images": [img_b64],
            "stream": False,
            "options": {"temperature": 0, "num_predict": 1500}
        }, timeout=180)
        data = resp.json()
        text = data.get("response", "").strip()
    except Exception as e:
        text = f"[OCR ERROR: {e}]"
    
    elapsed = time.time() - t0
    results.append(f"=== 第{page}页 ===\n{text}")
    
    # Save progress
    with open(PROGRESS, "a") as f:
        f.write(f"Page {page}/{END_PAGE} ({elapsed:.0f}s): {len(text)} chars\n")
    
    print(f"[{page}/{END_PAGE}] {elapsed:.0f}s {len(text)} chars", flush=True)
    try:
        os.unlink(png)
    except OSError:
        pass  # Already cleaned by previous iteration

# Write final output
OUT.write_text("\n\n".join(results), encoding="utf-8")
print(f"\nDone: {OUT} ({OUT.stat().st_size:,} bytes)", flush=True)
