#!/usr/bin/env python3
"""Creative provider bridge for ad images/banners.

Supports:
  - api: generic HTTP JSON provider returning local-file-ready assets
  - mcp: agent-mediated mode; emits a manifest the agent can pass to any MCP tool

The goal is to keep the Yandex.Direct skill usable across agents while still
having a concrete runtime for API-based image generation.
"""

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


class CreativeProviderError(Exception):
    pass


def _slug(text):
    text = (text or "creative").strip().lower()
    text = re.sub(r"[^a-z0-9а-яё]+", "-", text, flags=re.IGNORECASE)
    text = text.strip("-")
    return text or "creative"


def ensure_provider_config(cfg):
    cp = cfg.get("creative_provider") or {}
    mode = (cp.get("mode") or "").strip().lower()
    if not mode:
        raise CreativeProviderError(
            "creative_provider.mode is required: use 'api' or 'mcp'."
        )
    if mode not in ("api", "mcp"):
        raise CreativeProviderError(
            f"Unsupported creative_provider.mode={mode!r}; expected 'api' or 'mcp'."
        )
    return cp, mode


def build_prompt(brief, asset_type, index=1):
    offer = brief.get("offer") or brief.get("product") or brief.get("title") or "offer"
    audience = brief.get("audience") or "relevant audience"
    geo = brief.get("geo") or "target region"
    cta = brief.get("cta") or "Leave a request"
    style = brief.get("style") or "clean commercial ad creative"
    size = brief.get("size") or "1200x628"
    required = ", ".join(brief.get("required_elements") or []) or "focus on the offer"
    forbidden = ", ".join(brief.get("forbidden_elements") or []) or "watermarks, distorted text"
    return (
        f"Create a high-quality {asset_type} for Yandex.Direct / RSYA. "
        f"Offer: {offer}. Audience: {audience}. Geo: {geo}. "
        f"Style: {style}. CTA: {cta}. Target size: {size}. "
        f"Required elements: {required}. Forbidden elements: {forbidden}. "
        f"Variant #{index}. Keep it conversion-focused, commercially realistic, "
        f"and suitable for paid advertising."
    )


def _default_headers(api_key):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _fetch_url(url, headers):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read()


def _write_asset_bytes(dest_dir, filename, data):
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, filename)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _normalize_api_assets(payload, dest_dir, headers):
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise CreativeProviderError("Creative API response must contain non-empty 'assets'.")

    out = []
    for i, item in enumerate(assets, 1):
        if not isinstance(item, dict):
            raise CreativeProviderError("Each asset in API response must be an object.")
        name = item.get("filename") or f"creative_{i}.png"
        mime = item.get("mime_type") or "image/png"
        if item.get("base64"):
            try:
                data = base64.b64decode(item["base64"])
            except Exception as e:
                raise CreativeProviderError(f"Invalid base64 asset at index {i}: {e}")
            path = _write_asset_bytes(dest_dir, name, data)
        elif item.get("url"):
            try:
                data = _fetch_url(item["url"], {})
            except urllib.error.URLError as e:
                raise CreativeProviderError(f"Failed to download asset URL at index {i}: {e.reason}")
            path = _write_asset_bytes(dest_dir, name, data)
        else:
            raise CreativeProviderError(
                "API asset must contain either 'base64' or 'url'."
            )
        out.append({
            "path": path,
            "mime_type": mime,
            "title": item.get("title") or "",
            "text": item.get("text") or "",
            "href": item.get("href") or "",
            "asset_type": item.get("asset_type") or "banner",
        })
    return out


def generate_via_api(provider_cfg, brief, output_dir):
    endpoint = provider_cfg.get("base_url")
    if not endpoint:
        raise CreativeProviderError("creative_provider.base_url is required for api mode.")
    body = {
        "service": provider_cfg.get("service_name") or "",
        "asset_types": brief.get("asset_types") or provider_cfg.get("asset_types") or ["banner"],
        "brief": brief,
        "variants": int(brief.get("variants") or 1),
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_default_headers(provider_cfg.get("api_key")),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise CreativeProviderError(f"Creative API HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise CreativeProviderError(f"Creative API network error: {e.reason}")
    except json.JSONDecodeError as e:
        raise CreativeProviderError(f"Creative API returned invalid JSON: {e}")
    return _normalize_api_assets(payload, output_dir, _default_headers(provider_cfg.get("api_key")))


def generate_via_mcp(provider_cfg, brief, output_dir):
    os.makedirs(output_dir, exist_ok=True)
    tool_name = provider_cfg.get("mcp_tool") or provider_cfg.get("service_name") or "MCP_TOOL_REQUIRED"
    variants = int(brief.get("variants") or 1)
    manifest = {
        "mode": "mcp",
        "tool_name": tool_name,
        "service_name": provider_cfg.get("service_name") or "",
        "asset_types": brief.get("asset_types") or provider_cfg.get("asset_types") or ["banner"],
        "variants": variants,
        "brief": brief,
        "requests": [],
        "output_dir": output_dir,
        "instructions": (
            "Agent must call the named MCP creative tool, generate the requested assets, "
            "save them into output_dir, then continue with image_ads.py upload/create."
        ),
    }
    for asset_type in manifest["asset_types"]:
        for index in range(1, variants + 1):
            manifest["requests"].append({
                "asset_type": asset_type,
                "variant": index,
                "prompt": build_prompt(brief, asset_type, index=index),
                "suggested_filename": f"{_slug(asset_type)}_{index}.png",
            })
    path = os.path.join(output_dir, "mcp_manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return {"manifest_path": path, "requests": manifest["requests"]}


def generate_assets(cfg, brief, output_dir):
    provider_cfg, mode = ensure_provider_config(cfg)
    if mode == "api":
        return {"mode": "api", "assets": generate_via_api(provider_cfg, brief, output_dir)}
    mcp = generate_via_mcp(provider_cfg, brief, output_dir)
    return {"mode": "mcp", **mcp}
