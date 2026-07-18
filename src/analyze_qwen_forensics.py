#!/usr/bin/env python3
"""Qwen-VL based visual forensics: let a multimodal LLM 'look' at the document
and flag tampering the classical pixel features miss (misaligned/pasted stamps,
font/baseline inconsistency, edited numbers, splice boundaries, watermarks).

Complements — does NOT replace — the business-logic checks. Reads the same
manifest, renders first page, asks qwen-vl-max for a structured forensic verdict.

Env: DASHSCOPE_API_KEY or QWEN_API_KEY.  Endpoint: DashScope OpenAI-compatible.
"""
from __future__ import annotations
import argparse, base64, csv, json, os, subprocess, time
from collections import Counter
from pathlib import Path
from urllib import request, error

FORENSIC_PROMPT = (
    "你是文档图像取证专家。仔细观察这张【{doc_type}】图片，判断是否存在数字篡改/伪造痕迹。重点看：\n"
    "1) 印章：是否错位、边缘生硬、颜色过于纯净均匀（疑似贴图），是否与底纹叠印自然；\n"
    "2) 文字/数字：字体、字号、基线、间距是否局部不一致，是否有数字被替换/涂改的痕迹；\n"
    "3) 拼接：是否有局部模糊、清晰度突变、色块/亮度不连续、可见拼接边界；\n"
    "4) 明显标记：SAMPLE/VOID/SYNTHETIC/作废/样本 等字样；\n"
    "5) 整体排版是否异常。\n"
    "若看到印章，同时给出归一化坐标 bbox=[x0,y0,x1,y1]（左上右下，范围0-1）、颜色 red/gray/black/unknown、可辨文字和贴图怀疑分。"
    "只输出 JSON：{{\"tampered\":0或1,\"risk_score\":0-100,\"findings\":[{{\"type\":\"...\",\"evidence\":\"...\"}}],"
    "\"seal_candidates\":[{{\"bbox\":[0.1,0.2,0.3,0.4],\"color\":\"gray\",\"text\":\"...\",\"pasted_suspicion\":0-100}}],"
    "\"reason_tags\":[\"...\"]}}。看不出明显篡改则 risk_score 应低。基于图像证据，不要编造。"
)


def run(cmd): return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

def read_csv(p):
    t = Path(p).read_text(encoding="utf-8", errors="replace").replace("\x00", "")
    return list(csv.DictReader(t.splitlines()))

def render_first(path, out_dir, dpi):
    out_dir.mkdir(parents=True, exist_ok=True)
    pre = out_dir / "page"
    proc = run(["pdftoppm", "-r", str(dpi), "-png", "-f", "1", "-l", "1", str(path), str(pre)])
    pages = sorted(out_dir.glob("page-*.png"))
    return pages[0] if proc.returncode == 0 and pages else None

def encode(path):
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")

def parse_json(text):
    for cand in (text, text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        try:
            return json.loads(cand)
        except Exception:
            continue
    return {"tampered": 0, "risk_score": 0, "findings": [], "reason_tags": ["qwen_non_json"], "_raw": text[:400]}

def normalize_seal_candidates(value):
    normalized = []
    if not isinstance(value, list):
        return normalized
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        bbox = item.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        try:
            x0, y0, x1, y1 = [float(number) for number in bbox]
        except (TypeError, ValueError):
            continue
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            continue
        try:
            suspicion = max(0, min(100, int(float(item.get("pasted_suspicion") or 0))))
        except (TypeError, ValueError):
            suspicion = 0
        normalized.append({
            "bbox": [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)],
            "color": str(item.get("color") or "unknown")[:20],
            "text": str(item.get("text") or "")[:120],
            "pasted_suspicion": suspicion,
        })
    return normalized

def qwen_forensics(image, doc_type, model, timeout, max_tokens):
    key = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY")
    if not key:
        raise RuntimeError("missing DASHSCOPE_API_KEY/QWEN_API_KEY")
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": FORENSIC_PROMPT.format(doc_type=doc_type or "文档")},
            {"type": "image_url", "image_url": {"url": encode(image)}},
        ]}],
        "temperature": 0, "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    req = request.Request("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                          data=json.dumps(payload).encode(),
                          headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    with request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    return parse_json(d["choices"][0]["message"]["content"])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--render-dir", default="/tmp/qwen_fx_pages")
    ap.add_argument("--model", default="qwen-vl-max")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.3)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--max-tokens", type=int, default=1200)
    args = ap.parse_args()
    rows = [r for r in read_csv(args.manifest) if r.get("ext", "").lower() in {".pdf", ".jpg", ".jpeg", ".png"}]
    if args.limit > 0:
        rows = rows[: args.limit]
    recs = []
    for row in rows:
        src = Path(row["path"])
        if not src.is_absolute():
            src = Path.cwd() / src
        img, err = None, ""
        if row.get("ext", "").lower() == ".pdf":
            img = render_first(src, Path(args.render_dir) / row["document_id"], args.dpi) if src.exists() else None
            if not img: err = "render_failed_or_missing"
        else:
            img = src if src.exists() else None
            if not img: err = "image_missing"
        res = {}
        if img and not err:
            try:
                res = qwen_forensics(img, row.get("doc_type", ""), args.model, args.timeout, args.max_tokens)
            except error.HTTPError as e:
                err = f"http_{e.code}:{e.read().decode()[:120]}"
            except Exception as e:
                err = f"{type(e).__name__}:{str(e)[:120]}"
        score = int(float(res.get("risk_score") or 0)) if res else 0
        tags = res.get("reason_tags") if isinstance(res.get("reason_tags"), list) else []
        finds = res.get("findings") if isinstance(res.get("findings"), list) else []
        seals = normalize_seal_candidates(res.get("seal_candidates"))
        recs.append({**row, "qwen_fx_model": args.model,
                     "qwen_fx_tampered": int(res.get("tampered") or 0),
                     "qwen_fx_risk_score": max(0, min(100, score)),
                     "qwen_fx_reason_tags": "|".join(str(t)[:60] for t in tags),
                     "qwen_fx_findings": json.dumps(finds, ensure_ascii=False)[:800],
                     "qwen_fx_seal_count": len(seals),
                     "qwen_fx_seal_candidates": json.dumps(seals, ensure_ascii=False)[:1200],
                     "qwen_fx_error": err})
        if args.sleep > 0:
            time.sleep(args.sleep)
    cols = list(recs[0].keys()) if recs else []
    Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(args.out_csv).open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, quoting=csv.QUOTE_ALL, escapechar="\\"); w.writeheader(); w.writerows(recs)
    errs = Counter(r["qwen_fx_error"].split(":")[0] for r in recs if r["qwen_fx_error"])
    print(f"wrote {len(recs)} rows -> {args.out_csv}  errors={dict(errs)}")

if __name__ == "__main__":
    main()
