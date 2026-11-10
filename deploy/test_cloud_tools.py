"""Offline tests for public staging and private snapshot integrity boundaries."""
import hashlib, json, os, subprocess, tempfile, unittest, zipfile
from pathlib import Path
import shutil
from unittest.mock import patch
import cloud_control as cloud
import prepare_publication as publication

def local_test_directory():
    root=Path(__file__).resolve().parents[1]/".runtime/cloud-test-temp"
    root.mkdir(parents=True,exist_ok=True)
    root=cloud.long_path(root)
    return tempfile.TemporaryDirectory(dir=root)

class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp=local_test_directory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.data=self.root/"data";self.data.mkdir()
        (self.data/"state").mkdir();(self.data/"raw").mkdir()
        (self.data/"state/checkpoint.json").write_text('{"training_code":"013"}',"utf-8")
        (self.data/"raw/简章.bin").write_bytes(b"original\x00bytes\xff")
        (self.data/"empty.txt").write_bytes(b"")
        self.bundle=self.root/"bundle";self.manifest=cloud.pack_snapshot(self.data,self.bundle)

    def test_roundtrip_and_leading_zero(self):
        output=self.root/"restored";r=cloud.restore_snapshot(self.bundle,output)
        self.assertEqual(r["restored_files"],3)
        self.assertEqual((output/"raw/简章.bin").read_bytes(),b"original\x00bytes\xff")
        self.assertEqual(json.loads((output/"state/checkpoint.json").read_text())["training_code"],"013")

    def test_tampered_archive_rejected(self):
        part=self.bundle/self.manifest["parts"][0]["name"]
        with part.open("ab") as f:f.write(b"tamper")
        with self.assertRaises(cloud.CloudError):cloud.restore_snapshot(self.bundle,self.root/"restored")

    def test_parent_path_rejected(self):
        self.manifest["files"][0]["path"]="../outside"
        (self.bundle/"snapshot-manifest.json").write_text(json.dumps(self.manifest),"utf-8")
        with self.assertRaises(cloud.CloudError):cloud.restore_snapshot(self.bundle,self.root/"restored")
        self.assertFalse((self.root/"outside").exists())

    def test_unlisted_zip_entry_rejected(self):
        p=self.bundle/self.manifest["parts"][0]["name"]
        with zipfile.ZipFile(p,"a") as archive:archive.writestr("unlisted.txt","x")
        self.manifest["parts"][0].update(bytes=p.stat().st_size,sha256=cloud.sha(p))
        (self.bundle/"snapshot-manifest.json").write_text(json.dumps(self.manifest),"utf-8")
        with self.assertRaises(cloud.CloudError):cloud.restore_snapshot(self.bundle,self.root/"restored")

    def test_private_configuration_not_packaged(self):
        (self.data/"private").mkdir();(self.data/"private/config.json").write_text("{}")
        with self.assertRaises(cloud.CloudError):cloud.pack_snapshot(self.data,self.root/"second-bundle")

    def test_existing_destination_not_replaced(self):
        dest=self.root/"existing";dest.mkdir();(dest/"keep").write_text("preserve")
        with self.assertRaises(cloud.CloudError):cloud.restore_snapshot(self.bundle,dest)
        self.assertEqual((dest/"keep").read_text(),"preserve")

    def test_deep_sha_style_destination_roundtrip(self):
        dest=self.root/('a'*64)/('b'*64)/'restored'
        result=cloud.restore_snapshot(self.bundle,dest)
        self.assertEqual(result['restored_files'],3)
        self.assertEqual(cloud.sha(dest/'raw/简章.bin'),cloud.sha(self.data/'raw/简章.bin'))

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp=local_test_directory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/"source";self.source.mkdir()
        for path,content in {"README.md":"software","doctoral_monitor/core.py":"x=1\n","private/notify.json":"personal",
              "raw/original.body":"not public","audit/local.json":"not public","integrations/rsshub/node_modules/huge.js":"not public",
              "config/pilot_sources.json":'{"baseline_id":"test","sources":[],"headers":{"Set-Cookie":"temporary-probe-cookie","Accept":"application/json"}}',"config/source_registry.json":'{"institutions":[]}',"integrations/rsshub/LICENSE.RSSHub":"upstream license"}.items():
            p=self.source/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content,"utf-8")

    def test_public_allowlist_excludes_local_data(self):
        target=self.root/"stage";publication.stage(self.source,target)
        self.assertTrue((target/"doctoral_monitor/core.py").is_file())
        self.assertFalse((target/"private").exists());self.assertFalse((target/"raw").exists());self.assertFalse((target/"audit").exists())
        self.assertFalse((target/"integrations/rsshub/node_modules").exists())
        self.assertTrue((target/"integrations/rsshub/LICENSE.RSSHub").is_file())
        config=json.loads((target/"config/pilot_sources.json").read_text("utf-8"))
        self.assertNotIn("Set-Cookie",config["headers"])
        self.assertEqual(config["headers"]["Accept"],"application/json")

    def test_personal_configuration_key_rejected(self):
        (self.source/"config/pilot_sources.json").write_text('{"recipient_email":"personal@example.invalid"}',"utf-8")
        with self.assertRaises(ValueError):publication.stage(self.source,self.root/"stage")

    def test_actual_pem_key_block_rejected(self):
        block="-----BEGIN PRIVATE KEY-----\n"+("A"*64)+"\n-----END PRIVATE KEY-----\n"
        (self.source/"README.md").write_text(block,"utf-8")
        with self.assertRaises(ValueError):publication.stage(self.source,self.root/"stage")

    def test_exported_baseline_is_staging_only(self):
        original=(self.source/"config/pilot_sources.json").read_bytes()
        manifest=self.root/"baseline.json";manifest.write_text('{"baseline_id":"test","files":[],"delivery_root":"private-local-path"}',"utf-8")
        target=self.root/"stage";publication.stage(self.source,target,manifest)
        config=json.loads((target/"config/pilot_sources.json").read_text("utf-8"))
        self.assertEqual(config["baseline"]["validation_mode"],"exported_registry")
        self.assertEqual(config["baseline"]["source_registry_sha256"],publication.digest(target/"config/source_registry.json"))
        self.assertEqual((self.source/"config/pilot_sources.json").read_bytes(),original)
        exported=json.loads((target/"baseline/manifest.json").read_text("utf-8"))
        self.assertNotIn("delivery_root",exported)
        self.assertEqual(exported["original_manifest_sha256"],publication.digest(manifest))
        self.assertEqual(config["baseline"]["original_manifest_sha256"],publication.digest(manifest))

    def test_cli_error_does_not_echo_secret(self):
        result=subprocess.CompletedProcess([],1,stdout=b"",stderr=b"private-value-that-must-not-be-printed")
        with patch("cloud_control.subprocess.run",return_value=result):
            with self.assertRaises(cloud.CloudError) as cm:cloud.gh(["api","user"])
        self.assertNotIn("private-value",str(cm.exception))

class RemoteControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=local_test_directory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.data=self.root/"data";self.data.mkdir();(self.data/"checkpoint.json").write_text('{"cursor":"previous-success"}',"utf-8")

    def fake_api(self,endpoint,*args,**kwargs):
        if endpoint=="repos/example/data":return {"private":True,"archived":False,"disabled":False,"full_name":"example/data","default_branch":"main"}
        if "/releases?" in endpoint:return []
        raise AssertionError("Unexpected network endpoint in test")

    def fake_gh(self,args,*unused,**kwargs):
        self.calls.append(args[:2])
        if args[:2]==["release","upload"]:
            self.uploaded=[Path(x) for x in args[3:args.index("--repo")]]
        if args[:2]==["release","download"]:
            target=Path(args[args.index("--dir")+1]);target.mkdir(exist_ok=True)
            for file in self.uploaded:shutil.copyfile(file,target/file.name)
            if self.tamper:
                with next(target.glob("*.zip")).open("ab") as f:f.write(b"bad-download")
        return ""

    def fake_download(self,repo,tag,destination):
        return self.fake_gh(['release','download',tag,'--repo',repo,'--dir',str(destination)])

    def test_release_stays_draft_until_download_verified(self):
        self.calls=[];self.tamper=False
        with patch("cloud_control.api",side_effect=self.fake_api),patch("cloud_control.gh",side_effect=self.fake_gh),patch("cloud_control.download_snapshot_assets",side_effect=self.fake_download),patch("cloud_control.temporary_root",return_value=self.root):
            result=cloud.release_put(self.data,"example/data","snapshot-test")
        self.assertTrue(result["roundtrip_verified"])
        self.assertEqual(self.calls,[["release","create"],["release","upload"],["release","download"],["release","edit"]])

    def test_bad_download_does_not_publish_release(self):
        self.calls=[];self.tamper=True
        with patch("cloud_control.api",side_effect=self.fake_api),patch("cloud_control.gh",side_effect=self.fake_gh),patch("cloud_control.download_snapshot_assets",side_effect=self.fake_download),patch("cloud_control.temporary_root",return_value=self.root):
            with self.assertRaises(cloud.CloudError):cloud.release_put(self.data,"example/data","snapshot-test")
        self.assertNotIn(["release","edit"],self.calls)

    def test_no_state_requires_explicit_bootstrap(self):
        with patch("cloud_control.api",side_effect=self.fake_api):
            with self.assertRaises(cloud.CloudError):cloud.release_get(self.root/"restored","example/data")
            result=cloud.release_get(self.root/"bootstrap","example/data",bootstrap=True)
        self.assertTrue(result["bootstrapped"])

    def test_connection_failure_cannot_bootstrap_over_missing_state(self):
        def failed(endpoint,*args,**kwargs):
            if "/releases?" in endpoint:raise cloud.CloudError("Network unavailable")
            return self.fake_api(endpoint)
        with patch("cloud_control.api",side_effect=failed):
            with self.assertRaises(cloud.CloudError):cloud.release_get(self.root/"restored","example/data",bootstrap=True)
        self.assertFalse((self.root/"restored").exists())

    def test_archive_upload_refuses_public_repository(self):
        with patch("cloud_control.api",return_value={"private":False}),patch("cloud_control.gh") as sender:
            with self.assertRaises(cloud.CloudError):cloud.release_put(self.data,"example/data","snapshot-test")
        sender.assert_not_called()

    def test_network_probe_dispatch_does_not_send_archive_inputs(self):
        with patch("cloud_control.repo_check"),patch("cloud_control.api",return_value={"workflow_run_id":1}) as request:
            cloud.dispatch_workflow("example/code","network-probe.yml","main")
        payload=request.call_args.args[2]
        self.assertNotIn("inputs",payload)
        self.assertEqual(payload["ref"],"main")

if __name__=="__main__":unittest.main()
