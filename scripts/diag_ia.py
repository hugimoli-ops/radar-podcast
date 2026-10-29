# Diagnóstico temporal de la IA de GitHub Models (se borrará)
import json, os, urllib.request
tok = os.environ.get("GITHUB_TOKEN", "")
out = []
for url, accept, extra in [
    ("https://models.github.ai/inference/chat/completions", "application/vnd.github+json", {}),
    ("https://models.github.ai/inference/chat/completions", "application/json", {"response_format": {"type": "json_object"}}),
    ("https://models.github.ai/catalog/models", "application/vnd.github+json", None),
]:
    body = None if extra is None else json.dumps({"model": "openai/gpt-4o-mini", "messages": [{"role": "user", "content": "Di hola en JSON {\"x\":\"hola\"}"}], **extra}).encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"})
    try:
        r = urllib.request.urlopen(req, timeout=60)
        out.append({"url": url, "accept": accept, "status": r.status, "final": r.geturl(), "ctype": r.headers.get("Content-Type"), "body": r.read().decode("utf-8", "replace")[:400]})
    except Exception as e:
        out.append({"url": url, "accept": accept, "error": str(e), "body": (e.read().decode("utf-8", "replace")[:400] if hasattr(e, "read") else "")})
out.append({"token_len": len(tok), "proxy": {k: bool(v) for k, v in os.environ.items() if "proxy" in k.lower()}})
open("data/diag.json", "w").write(json.dumps(out, ensure_ascii=False, indent=1))
