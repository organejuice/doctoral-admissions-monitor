"""Explicit GitHub operations using the official gh CLI; no operation runs on import.

Credentials come only from gh's credential store or GH_TOKEN. Never print tokens,
mail recipients, push topics, file contents, or the unfiltered gh error response.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, stat, subprocess, tempfile, zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")
MAX_SNAPSHOT_BYTES = 512 * 1024 * 1024
MAX_PART_BYTES = 50 * 1024 * 1024
MAX_REMOTE_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
BLOCKED_PARTS = {"private", ".git", "node_modules", ".runtime", "__pycache__"}

class CloudError(RuntimeError): pass

def temporary_root():
    root=Path(os.environ.get("DOCTORAL_CLOUD_TMPDIR",str(Path.cwd()/".runtime/cloud-temp"))).resolve()
    root.mkdir(parents=True,exist_ok=True)
    return root

def long_path(path):
    """Use Windows' normal extended-length syntax for deeply nested SHA paths."""
    path=Path(path).resolve()
    if os.name=="nt" and not str(path).startswith("\\\\?\\"):
        value=str(path)
        path=Path("\\\\?\\UNC\\"+value.lstrip("\\") if value.startswith("\\\\") else "\\\\?\\"+value)
    return path

def sha(path):
    h = hashlib.sha256()
    with long_path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def safe_relative(value):
    p = PurePosixPath(value)
    if not value or "\\" in value or ":" in value or p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise CloudError("Unsafe archive/publication path")
    return p

def gh(arguments, payload=None, binary=False):
    command = [shutil.which("gh") or "gh", *arguments]
    try:
        p = subprocess.run(command, input=None if payload is None else json.dumps(payload).encode(), capture_output=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CloudError("GitHub CLI unavailable or request timed out") from exc
    if p.returncode:
        # Error payloads may contain private paths or values; do not echo them.
        raise CloudError(f"GitHub CLI operation failed (exit {p.returncode}); credentials, access or network require checking")
    return p.stdout if binary else p.stdout.decode("utf-8")

def api(endpoint, method="GET", payload=None):
    args = ["api", endpoint, "--method", method]
    if payload is not None: args += ["--input", "-"]
    out = gh(args, payload)
    return json.loads(out) if out.strip() else None

def repo_check(repo, visibility):
    if not REPO_RE.fullmatch(repo): raise CloudError("Expected OWNER/REPO")
    obj = api("repos/" + repo)
    if bool(obj.get("private")) != (visibility == "private"):
        raise CloudError("Repository visibility does not match the required free/private-data role")
    if obj.get("archived") or obj.get("disabled"): raise CloudError("Repository is inactive")
    return {"repository": obj["full_name"], "private": obj["private"], "default_branch": obj["default_branch"]}

def create_repositories(code_repo, data_repo):
    if code_repo == data_repo: raise CloudError("Code and private data repositories must be distinct")
    me = api("user")["login"]
    created = []
    for repo, private in [(code_repo, False), (data_repo, True)]:
        if not REPO_RE.fullmatch(repo) or repo.split("/")[0].lower() != me.lower():
            raise CloudError("Creation is restricted to the authenticated user's namespace")
    for repo, private in [(code_repo, False), (data_repo, True)]:
        # Existing names cause an API error; never change an existing repository's visibility.
        obj = api("user/repos", "POST", {"name": repo.split("/")[1], "private": private, "auto_init": True,
            "description": "Doctoral admissions monitoring software" if not private else "Private doctoral monitoring state and original-source snapshots"})
        created.append({"repository": obj["full_name"], "private": obj["private"]})
    return {"created": created, "software_uploaded": False}

def dispatch_workflow(repo,workflow,ref,bootstrap=False):
    repo_check(repo,"public")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.ya?ml",workflow):raise CloudError("Invalid workflow file name")
    payload={"ref":ref,"return_run_details":True}
    if workflow=="pilot.yml":payload["inputs"]={"bootstrap":str(bootstrap).lower()}
    elif bootstrap:raise CloudError("Bootstrap is only defined for pilot.yml")
    return api(f"repos/{repo}/actions/workflows/{workflow}/dispatches","POST",payload)

def publish(staging, repo):
    info = repo_check(repo, "public"); staging = Path(staging).resolve()
    manifest = json.loads((staging / ".publication-manifest.json").read_text("utf-8"))
    if not manifest["files"]: raise CloudError("Empty publication manifest")
    entries = []
    for item in manifest["files"]:
        rel = safe_relative(item["path"]); f = staging.joinpath(*rel.parts)
        if f.is_symlink() or not f.is_file() or f.stat().st_size != item["bytes"] or sha(f) != item["sha256"]:
            raise CloudError("Staged software differs from the review manifest")
        f.read_text("utf-8-sig")  # Publication is limited to UTF-8 source/configuration.
        entries.append((str(rel), f))
    allowed = {i["path"] for i in manifest["files"]} | {".publication-manifest.json"}
    actual = {p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()}
    if actual != allowed: raise CloudError("Staging contains unreviewed files")
    branch = info["default_branch"]
    ref = api(f"repos/{repo}/git/ref/heads/{branch}"); parent = ref["object"]["sha"]
    commit = api(f"repos/{repo}/git/commits/{parent}")
    tree = api(f"repos/{repo}/git/trees", "POST", {"base_tree": commit["tree"]["sha"], "tree": [
        {"path": rel, "mode": "100644", "type": "blob", "content": f.read_bytes().decode("utf-8")} for rel, f in entries]})
    new = api(f"repos/{repo}/git/commits", "POST", {"message": "Add reviewed doctoral monitoring pilot", "tree": tree["sha"], "parents": [parent]})
    # Normal non-forced update: concurrent remote changes fail rather than being overwritten.
    api(f"repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": new["sha"], "force": False})
    return {"repository": repo, "commit": new["sha"], "published_files": len(entries)}

def pack_snapshot(directory, bundle_dir):
    directory = Path(directory).resolve(); bundle_dir = Path(bundle_dir).resolve()
    if not directory.is_dir() or bundle_dir.exists(): raise CloudError("Source directory must exist and bundle directory must be new")
    if bundle_dir == directory or bundle_dir.is_relative_to(directory): raise CloudError("Bundle must be outside its input")
    source_files = []
    for f in sorted(directory.rglob("*")):
        rel = f.relative_to(directory)
        if f.is_symlink(): raise CloudError("Snapshot symlinks are not allowed")
        if any(x.lower() in BLOCKED_PARTS or x.lower().startswith(".env") for x in rel.parts):
            raise CloudError("Private configuration or environment directory selected for archive")
        if f.is_file():
            safe_relative(rel.as_posix())
            if f.stat().st_size > MAX_PART_BYTES: raise CloudError("A file exceeds the configured free-pilot shard budget")
            source_files.append(f)
    if sum(f.stat().st_size for f in source_files) > MAX_SNAPSHOT_BYTES: raise CloudError("Snapshot exceeds the configured pilot storage budget")
    bundle_dir.mkdir(parents=True)
    entries=[]; parts=[]; writer=None; part_size=0; part_name=None
    try:
        for f in source_files:
            if writer is None or part_size + f.stat().st_size > MAX_PART_BYTES:
                if writer is not None: writer.close()
                part_name=f"snapshot-{len(parts)+1:04d}.zip"; parts.append({"name":part_name})
                writer=zipfile.ZipFile(bundle_dir/part_name,"w",zipfile.ZIP_DEFLATED); part_size=0
            rel=f.relative_to(directory).as_posix(); expected=sha(f); size=f.stat().st_size
            writer.write(f,rel)
            if sha(f)!=expected or f.stat().st_size!=size: raise CloudError("Input changed while creating snapshot")
            entries.append({"path":rel,"bytes":size,"sha256":expected,"part":part_name}); part_size+=size
    finally:
        if writer is not None: writer.close()
    for p in parts:
        f=bundle_dir/p["name"];p.update(bytes=f.stat().st_size,sha256=sha(f))
    manifest={"schema_version":1,"created_at_utc":datetime.now(timezone.utc).isoformat(),"files":entries,"parts":parts,"total_uncompressed_bytes":sum(e["bytes"] for e in entries)}
    (bundle_dir/"snapshot-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),"utf-8")
    return manifest

def restore_snapshot(bundle_dir, destination):
    bundle_dir=Path(bundle_dir).resolve(); destination=long_path(destination)
    if destination.exists(): raise CloudError("Restore destination must not already exist")
    manifest=json.loads((bundle_dir/"snapshot-manifest.json").read_text("utf-8"))
    if manifest.get("schema_version")!=1: raise CloudError("Unknown snapshot schema")
    parts={p["name"]:p for p in manifest["parts"]}
    if len(parts)!=len(manifest["parts"]) or len({e["path"] for e in manifest["files"]})!=len(manifest["files"]): raise CloudError("Duplicate manifest entry")
    if sum(e["bytes"] for e in manifest["files"])>MAX_SNAPSHOT_BYTES: raise CloudError("Restore exceeds storage budget")
    for name,p in parts.items():
        if not re.fullmatch(r"snapshot-\d{4}\.zip",name): raise CloudError("Unsafe part name")
        f=bundle_dir/name
        if f.stat().st_size!=p["bytes"] or sha(f)!=p["sha256"]: raise CloudError("Archive part hash mismatch")
    for entry in manifest["files"]:
        safe_relative(entry["path"])
        if entry["part"] not in parts or entry["bytes"]<0 or entry["bytes"]>MAX_PART_BYTES: raise CloudError("Invalid file manifest")
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-restore-",dir=destination.parent) as temp:
        stage=Path(temp)/"data";stage.mkdir()
        for name in parts:
            expected={e["path"]:e for e in manifest["files"] if e["part"]==name}
            with zipfile.ZipFile(bundle_dir/name) as archive:
                names=archive.namelist()
                if set(names)!=set(expected) or len(names)!=len(expected): raise CloudError("Archive entries differ from the manifest")
                for info in archive.infolist():
                    rel=safe_relative(info.filename); e=expected[info.filename]
                    if stat.S_ISLNK(info.external_attr>>16) or info.file_size!=e["bytes"]: raise CloudError("Invalid archive entry")
                    target=stage.joinpath(*rel.parts);target.parent.mkdir(parents=True,exist_ok=True)
                    with archive.open(info) as src, target.open("wb") as dst:
                        remaining=e["bytes"]
                        while chunk:=src.read(min(1024*1024,remaining+1)):
                            remaining-=len(chunk)
                            if remaining<0: raise CloudError("Archive expands beyond its declared size")
                            dst.write(chunk)
                    if remaining or sha(target)!=e["sha256"]: raise CloudError("Restored file hash mismatch")
        os.replace(stage,destination)
    return {"restored_files":len(manifest["files"]),"bytes":sum(e["bytes"] for e in manifest["files"])}

def download_snapshot_assets(repo,tag,destination):
    """Use official asset API, including private draft assets, without github.com URLs."""
    release=None
    for page in range(1,101):
        releases=api(f"repos/{repo}/releases?per_page=100&page={page}")
        release=next((r for r in releases if r['tag_name']==tag),None)
        if release is not None or len(releases)<100:break
    if release is None:raise CloudError("Requested snapshot release not found")
    destination=long_path(destination);destination.mkdir(parents=True,exist_ok=True)
    if not any(a['name']=='snapshot-manifest.json' for a in release.get('assets',[])):raise CloudError("Snapshot manifest asset missing")
    for asset in release['assets']:
        name=asset['name']
        if name!='snapshot-manifest.json' and not re.fullmatch(r'snapshot-\d{4}\.zip',name):raise CloudError("Unexpected release asset")
        if asset['size']>MAX_SNAPSHOT_BYTES:raise CloudError("Release asset exceeds pilot budget")
        body=gh(['api',f'repos/{repo}/releases/assets/{asset["id"]}','--header','Accept: application/octet-stream'],binary=True)
        digest=hashlib.sha256(body).hexdigest()
        if len(body)!=asset['size'] or asset.get('digest') not in (None,'sha256:'+digest):raise CloudError("Release asset differs from GitHub digest")
        (destination/name).write_bytes(body)
    return release

def release_put(directory, repo, tag):
    repo_check(repo,"private")
    if not TAG_RE.fullmatch(tag) or not tag.startswith("snapshot-"): raise CloudError("Invalid immutable snapshot tag; use snapshot- prefix")
    with tempfile.TemporaryDirectory(prefix="doctoral-upload-",dir=temporary_root()) as tmp:
        bundle=Path(tmp)/"bundle"; manifest=pack_snapshot(directory,bundle)
        remote_bytes=0
        for page in range(1,101):
            releases=api(f"repos/{repo}/releases?per_page=100&page={page}")
            remote_bytes+=sum(a["size"] for release in releases for a in release.get("assets",[]))
            if len(releases)<100:break
        else:raise CloudError("Too many existing releases to audit the pilot storage budget")
        if remote_bytes+sum(f.stat().st_size for f in bundle.iterdir())>MAX_REMOTE_SNAPSHOT_BYTES:
            raise CloudError("Private release storage exceeds the self-imposed 2 GiB pilot budget; existing originals retained")
        gh(["release","create",tag,"--repo",repo,"--draft","--title",tag,"--notes","Doctoral monitoring state snapshot; files are identified by snapshot-manifest.json."])
        gh(["release","upload",tag,*[str(f) for f in sorted(bundle.iterdir())],"--repo",repo])
        # Binary download and content validation occur before marking the release published.
        downloaded=Path(tmp)/"downloaded";downloaded.mkdir()
        download_snapshot_assets(repo,tag,downloaded)
        roundtrip=restore_snapshot(downloaded,Path(tmp)/"restored")
        gh(["release","edit",tag,"--repo",repo,"--draft=false"])
        return {"repository":repo,"tag":tag,"files":len(manifest["files"]),"roundtrip_verified":True,"bytes":roundtrip["bytes"]}

def release_get(destination, repo, tag=None, bootstrap=False):
    repo_check(repo,"private")
    if tag is None:
        releases=api(f"repos/{repo}/releases?per_page=100")
        snapshots=[x for x in releases if not x["draft"] and x["tag_name"].startswith("snapshot-")]
        if not snapshots:
            if not bootstrap: raise CloudError("No prior snapshot; explicit bootstrap is required")
            p=Path(destination)
            if p.exists(): raise CloudError("Bootstrap destination already exists")
            p.mkdir(parents=True)
            return {"bootstrapped":True,"restored_files":0}
        tag=max(snapshots,key=lambda x:x["created_at"])["tag_name"]
    if not TAG_RE.fullmatch(tag): raise CloudError("Invalid snapshot tag")
    with tempfile.TemporaryDirectory(prefix="doctoral-download-",dir=temporary_root()) as tmp:
        download_snapshot_assets(repo,tag,tmp)
        result=restore_snapshot(tmp,destination);result.update(tag=tag,bootstrapped=False)
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);sp=p.add_subparsers(dest="command",required=True)
    c=sp.add_parser("repo-check");c.add_argument("--repo",required=True);c.add_argument("--visibility",choices=["public","private"],required=True)
    c=sp.add_parser("create-repositories");c.add_argument("--code-repo",required=True);c.add_argument("--data-repo",required=True)
    c=sp.add_parser("publish");c.add_argument("--repo",required=True);c.add_argument("--staging",type=Path,required=True)
    c=sp.add_parser("dispatch");c.add_argument("--repo",required=True);c.add_argument("--workflow",default="pilot.yml");c.add_argument("--ref",default="main");c.add_argument("--bootstrap",action="store_true")
    c=sp.add_parser("run-status");c.add_argument("--repo",required=True);c.add_argument("--run-id",type=int,required=True)
    c=sp.add_parser("release-put");c.add_argument("--repo",required=True);c.add_argument("--directory",type=Path,required=True);c.add_argument("--tag",required=True)
    c=sp.add_parser("release-get");c.add_argument("--repo",required=True);c.add_argument("--destination",type=Path,required=True);c.add_argument("--tag");c.add_argument("--bootstrap",action="store_true")
    args=p.parse_args()
    if hasattr(args,"repo") and not REPO_RE.fullmatch(args.repo):raise CloudError("Expected OWNER/REPO")
    if args.command=="repo-check":out=repo_check(args.repo,args.visibility)
    elif args.command=="create-repositories":out=create_repositories(args.code_repo,args.data_repo)
    elif args.command=="publish":out=publish(args.staging,args.repo)
    elif args.command=="dispatch":
        out=dispatch_workflow(args.repo,args.workflow,args.ref,args.bootstrap)
    elif args.command=="run-status":out=api(f"repos/{args.repo}/actions/runs/{args.run_id}");out={k:out.get(k) for k in ["id","status","conclusion","html_url","created_at","updated_at"]}
    elif args.command=="release-put":out=release_put(args.directory,args.repo,args.tag)
    elif args.command=="release-get":out=release_get(args.destination,args.repo,args.tag,args.bootstrap)
    print(json.dumps(out,ensure_ascii=False))

if __name__=="__main__":
    try:main()
    except (CloudError,ValueError,KeyError,OSError,zipfile.BadZipFile) as exc:
        raise SystemExit(f"Cloud operation failed: {exc}")
