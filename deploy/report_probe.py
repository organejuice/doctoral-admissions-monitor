"""Print an allowlisted, one-line cloud-probe audit without original content."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE = re.compile(r"token|password|secret|authorization|api[_-]?key|smtp|recipient", re.I)


def public_text(value, limit=500):
    text = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    text = re.sub(r"https?://[^\s\"'<>]+", lambda match: public_url(match.group(0)) or "[invalid-url]", text)
    text = re.sub(r"(?i)\bauthorization\s*[:=]\s*(?:bearer\s+)?[^\s,;]+", "authorization=[redacted]", text)
    text = re.sub(r"(?i)\b(token|password|secret|authorization|api[_-]?key|smtp_password|recipient_email)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    return text[:limit]


def public_url(value):
    try:
        parts = urlsplit(str(value or ""))
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            return None
        host = parts.hostname
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host += f":{parts.port}"
        query = [(key, "[redacted]" if SENSITIVE.search(key) else val) for key, val in parse_qsl(parts.query, keep_blank_values=True)]
        fragment = "[redacted]" if SENSITIVE.search(parts.fragment) else parts.fragment
        return urlunsplit((parts.scheme, host, parts.path, urlencode(query), fragment))
    except (TypeError, ValueError):
        return None


def http_code(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599 else None


def make_detail(report, report_path=None):
    if not isinstance(report, dict) or not isinstance(report.get("sources_detail"), list):
        raise ValueError("report_has_no_sources_detail")
    sources = []
    for source in report["sources_detail"]:
        notices = source.get("notices", [])
        assets = [asset for notice in notices for asset in notice.get("assets", [])]
        reasons = []
        reasons.extend(source.get("failures", []))
        reasons.extend(item.get("reason", "") if isinstance(item, dict) else item for item in source.get("list_failures", []))
        reasons.extend(notice.get("reason", "") for notice in notices if notice.get("status") == "failed")
        reasons.extend(asset.get("error") or asset.get("status", "") for asset in assets if asset.get("status") != "original_acquired")
        reasons = list(dict.fromkeys(public_text(reason) for reason in reasons if reason))
        official_codes = {http_code(notice.get("response", {}).get("http_status")) for notice in notices}
        list_codes = set()
        source_id = str(source.get("source_id", ""))
        # Only numerical transport observations are taken from the bridge record.
        # Its response bodies, headers, bindings and other configuration are never emitted.
        if report_path is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", source_id):
            bridge_path = Path(report_path).resolve().parents[2] / "processed" / "rsshub_inputs" / f"{source_id}.json"
            if bridge_path.is_file():
                try:
                    bridge = json.loads(bridge_path.read_text("utf-8-sig"))
                    list_codes = {http_code(item.get("http_status")) for item in bridge.get("observations", []) if isinstance(item, dict)}
                except (OSError, ValueError, TypeError):
                    reasons.append("bridge_transport_observation_unreadable")
        official_codes.discard(None)
        list_codes.discard(None)
        sources.append({
            "source_id": public_text(source_id, 128),
            "title": public_text(source.get("title", ""), 200),
            "official_url": public_url(source.get("url")),
            "status": public_text(source.get("status", "unknown"), 40),
            "rsshub_status": public_text(source.get("rsshub_status", "unknown"), 40),
            "rsshub_http_code": http_code(source.get("rsshub_http_status")),
            "official_list_http_codes": sorted(list_codes) if list_codes else None,
            "notice_http_codes": sorted(official_codes) if official_codes else None,
            "notice_attempts": len(notices),
            "notice_originals_acquired": sum(notice.get("status") == "acquired" for notice in notices),
            "asset_attempts": len(assets),
            "asset_originals_acquired": sum(asset.get("status") == "original_acquired" for asset in assets),
            "asset_failures": sum(asset.get("status") != "original_acquired" for asset in assets),
            "pending_assets": sum(len(notice.get("pending_assets", [])) for notice in notices),
            "notices_deferred": source.get("notices_deferred") if isinstance(source.get("notices_deferred"), int) else None,
            "continuation_pages_remaining": len(source["continuation_urls"]) if isinstance(source.get("continuation_urls"), list) else None,
            "backfill_complete": source.get("backfill_complete") is True,
            "failure_reasons": reasons[:40],
            "failure_reasons_omitted": max(0, len(reasons) - 40),
        })
    sources.sort(key=lambda source: source["source_id"])
    return {
        "schema_version": 1,
        "run_id": public_text(report.get("run_id", ""), 160),
        "source_count": len(sources),
        "sources": sources,
        "scope": "Safe public-source probe observations only; no body, headers, credentials, private configuration or persistent originals.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        if not args.report.is_file():
            raise ValueError("probe_report_missing")
        report = json.loads(args.report.read_text("utf-8-sig"))
        if args.run_id is not None and report.get("run_id") != args.run_id:
            raise ValueError("probe_report_run_id_mismatch")
        detail = make_detail(report, args.report)
        if not detail["source_count"]:
            raise ValueError("probe_report_has_no_sources")
        print("CLOUD_PROBE_DETAIL=" + json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("CLOUD_PROBE_ERROR=" + json.dumps({"reason": public_text(error), "report_available": args.report.is_file()}, ensure_ascii=False, separators=(",", ":")))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
