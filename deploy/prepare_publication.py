"""Build an explicit, reviewable public-software staging tree; never upload it."""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil
from pathlib import Path

ROOT_FILES = {"README.md", "LICENSE", "pyproject.toml", "requirements.txt", "requirements.lock", "environment.yml", ".gitignore", ".gitattributes"}
DIR_EXTENSIONS = {
    "src": {".py"}, "doctoral_monitor": {".py"}, "tests": {".py", ".json", ".html", ".txt"},
    "deploy": {".py", ".json", ".md", ".yml", ".yaml", ".sh", ".txt"},
    "integrations/rsshub": {".js", ".mjs", ".ts", ".json", ".md", ".sh", ".yml"},
}
CONFIG_FILES = {"config/pilot_sources.json", "config/source_registry.json"}
LICENSE_FILES = {"integrations/rsshub/LICENSE.RSSHub"}
FORBIDDEN_PARTS = {"private", ".env", "data", "state", "archives", "raw", "audit", "reports", ".git", "node_modules", "__pycache__", ".runtime", "vendor"}
PRIVATE_KEYS = {"recipient", "recipient_email", "email_to", "smtp_password", "smtp_username", "smtp_user", "push_topic", "ntfy_topic", "token", "api_key", "password"}
SENSITIVE_HEADER_NAMES = {"cookie", "set-cookie", "authorization", "proxy-authorization"}

def sanitize_metadata(value):
    removed=0
    if isinstance(value,dict):
        for key in list(value):
            if key.lower() in SENSITIVE_HEADER_NAMES:
                del value[key];removed+=1
            else:removed+=sanitize_metadata(value[key])
    elif isinstance(value,list):
        for item in value:removed+=sanitize_metadata(item)
    return removed

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def reject_private_config(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in PRIVATE_KEYS and item not in (None, "", [], {}):
                raise ValueError(f"Personal/secret configuration key in public configuration: {key}")
            reject_private_config(item)
    elif isinstance(value, list):
        for item in value: reject_private_config(item)

def selected_files(root: Path):
    selected = {}
    candidates=[]
    for base, directories, names in os.walk(root, followlinks=False):
        directories[:]=[d for d in directories if d.lower() not in FORBIDDEN_PARTS and not d.lower().startswith(".env")]
        for d in directories:
            if (Path(base)/d).is_symlink():raise ValueError("Symlink directory in publication input")
        candidates.extend(Path(base)/name for name in names)
    for p in sorted(candidates):
        rel = p.relative_to(root).as_posix()
        if any(part.lower() in FORBIDDEN_PARTS or part.lower().startswith(".env") for part in p.relative_to(root).parts):
            continue
        if p.is_symlink():
            raise ValueError(f"Symlink is not allowed in publication input: {rel}")
        if not p.is_file(): continue
        allowed = rel in ROOT_FILES or rel in CONFIG_FILES or rel in LICENSE_FILES
        allowed |= any(rel.startswith(prefix + "/") and p.suffix in extensions for prefix, extensions in DIR_EXTENSIONS.items())
        if not allowed: continue
        if p.stat().st_size > 10 * 1024 * 1024:
            raise ValueError(f"Unexpected large source file: {rel}")
        text = p.read_text("utf-8-sig")
        if rel in CONFIG_FILES: reject_private_config(json.loads(text))
        if re.search(r"(?m)^-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?$",text) or re.search(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=\r\n ]{32,}-----END (?:[A-Z]+ )?PRIVATE KEY-----",text):
            raise ValueError(f"Private key material found: {rel}")
        selected[rel] = p
    workflow = root / "deploy/public-pilot.workflow.yml"
    if workflow.is_file(): selected[".github/workflows/pilot.yml"] = workflow
    probe=root/"deploy/public-network-probe.workflow.yml"
    if probe.is_file():selected[".github/workflows/network-probe.yml"]=probe
    return selected

def stage(root: Path, destination: Path, baseline_manifest: Path | None = None):
    root = root.resolve(); destination = destination.resolve()
    if destination == root or root.is_relative_to(destination):
        raise ValueError("Publication staging cannot be a source ancestor")
    if destination.exists(): raise ValueError("Staging destination must not already exist")
    files = selected_files(root)
    if not files: raise ValueError("No allowed software files found")
    destination.mkdir(parents=True)
    redactions=[]
    for rel, source in files.items():
        target = destination / rel; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if rel in CONFIG_FILES:
            value=json.loads(target.read_text("utf-8-sig"));removed=sanitize_metadata(value)
            if removed:
                target.write_text(json.dumps(value,ensure_ascii=False,indent=2),"utf-8")
                redactions.append({"path":rel,"sensitive_response_header_keys_removed":removed})
    if baseline_manifest is not None:
        baseline_manifest=baseline_manifest.resolve()
        contract=json.loads(baseline_manifest.read_text("utf-8-sig"))
        config_path=destination/"config/pilot_sources.json"
        registry_path=destination/"config/source_registry.json"
        config=json.loads(config_path.read_text("utf-8-sig"))
        if config["baseline_id"]!=contract["baseline_id"]:raise ValueError("Export baseline identity mismatch")
        exported=destination/"baseline/manifest.json";exported.parent.mkdir(parents=True)
        original_sha=digest(baseline_manifest)
        contract.pop("delivery_root",None)
        contract["original_manifest_sha256"]=original_sha
        exported.write_text(json.dumps(contract,ensure_ascii=False,indent=2),"utf-8")
        config["baseline"]={"validation_mode":"exported_registry","manifest_path":"../baseline/manifest.json",
            "source_registry_path":"source_registry.json","manifest_sha256":digest(exported),"source_registry_sha256":digest(registry_path),
            "original_manifest_sha256":original_sha,
            "scope":"Checks the exported registry and frozen manifest hashes; does not re-read the original seven catalogue files on the runner."}
        config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),"utf-8")
        files["baseline/manifest.json"]=exported
    entries=[]
    for rel in sorted(files):
        target=destination/rel
        entries.append({"path":rel,"bytes":target.stat().st_size,"sha256":digest(target)})
    manifest = {"schema_version": 1, "purpose": "Review-only public software allowlist; contains no raw admissions archives or personal configuration", "files": entries,"staging_only_redactions":redactions}
    (destination / ".publication-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
    return {"staged_files": len(entries), "bytes": sum(e["bytes"] for e in entries), "destination": str(destination), "uploaded": False}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True); ap.add_argument("--destination", type=Path, required=True)
    ap.add_argument("--baseline-manifest",type=Path)
    args = ap.parse_args()
    print(json.dumps(stage(args.root, args.destination,args.baseline_manifest), ensure_ascii=False))
