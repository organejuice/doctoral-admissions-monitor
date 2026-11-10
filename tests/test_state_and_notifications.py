import copy, hashlib, tempfile, unittest
from pathlib import Path
from doctoral_monitor.core import State,deadline_candidates,canonical
from doctoral_monitor.notifications import deliver,enqueue_test

def notice(text='博士招生报名时间和材料说明',asset_sha='aaa'):
    return {'url':'https://grad.example.edu.cn/2027.htm','title':'2027年博士招生章程','response':{'sha256':hashlib.sha256(text.encode()).hexdigest()},'document':{'text':text},'assets':[{'url':'https://grad.example.edu.cn/a.pdf','sha256':asset_sha,'status':'original_acquired'}],'classification':{'relevance':'doctoral_candidate'},'archive_status':'body_extracted_assets_acquired'}

class MonitoringStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);self.state=State(Path(self.tmp.name)/'state.sqlite3');self.source={'source_id':'src1','bindings':[{'institution_id':'T-01','training_code':'001'}]}
    def tearDown(self):self.state.db.close();self.tmp.cleanup()
    def count(self,table):return self.state.db.execute('SELECT count(*) FROM '+table).fetchone()[0]
    def test_repoll_is_idempotent_and_both_channels_queued(self):
        first=self.state.record(self.source,notice());again=self.state.record(self.source,notice())
        self.assertEqual(first['event_type'],'new_notice');self.assertEqual(again['event_type'],'unchanged');self.assertEqual(self.count('versions'),1);self.assertEqual(self.count('outbox'),2)
    def test_same_url_text_revision_is_retained(self):
        first=self.state.record(self.source,notice());later=self.state.record(self.source,notice('博士招生截止延期，材料变更'))
        self.assertNotEqual(first['version_id'],later['version_id']);self.assertEqual(later['event_type'],'revised_notice');self.assertEqual(self.count('versions'),2)
    def test_same_url_attachment_replacement_is_revision(self):
        first=self.state.record(self.source,notice());later=self.state.record(self.source,notice(asset_sha='bbb'))
        self.assertNotEqual(first['version_id'],later['version_id']);self.assertEqual(self.count('versions'),2)
    def test_parser_reprocessing_is_not_a_fake_official_revision_alert(self):
        first_record=notice();first=self.state.record(self.source,first_record)
        corrected=notice('修正正文解析结果');corrected['response']['sha256']=first_record['response']['sha256']
        later=self.state.record(self.source,corrected)
        self.assertEqual(later['event_type'],'metadata_reprocessed');self.assertEqual(self.count('outbox'),2)
    def test_main_pdf_replacement_with_identical_extracted_text_is_retained(self):
        first_record=notice();first_record['document']['document_kind']='pdf';first_record['response']['sha256']='original_pdf_bytes'
        first=self.state.record(self.source,first_record)
        replaced=copy.deepcopy(first_record);replaced['response']['sha256']='replacement_pdf_bytes'
        later=self.state.record(self.source,replaced)
        self.assertNotEqual(first['version_id'],later['version_id']);self.assertEqual(later['event_type'],'revised_notice')
    def test_shared_url_preserves_all_institution_bindings(self):
        self.state.record(self.source,notice());self.state.record({'source_id':'src2','bindings':[{'institution_id':'T-02','training_code':'002'}]},notice())
        self.assertEqual(self.count('notices'),1);self.assertEqual(self.count('bindings'),2);self.assertEqual(self.count('versions'),1)
    def test_not_admission_does_not_queue_alert(self):
        record=notice();record['classification']['relevance']='not_admission';self.state.record(self.source,record);self.assertEqual(self.count('outbox'),0)
    def test_single_channel_failure_does_not_block_other(self):
        enqueue_test(self.state)
        def fail(*args):raise RuntimeError('SMTP unavailable')
        def accepted(*args):return {'provider_accepted':True,'device_receipt_verified':False}
        results=deliver(self.state,{'email':{'enabled':True},'mobile':{'enabled':True}},senders={'email':fail,'mobile':accepted})
        self.assertEqual([r['status'] for r in results],['failed','provider_accepted']);self.assertFalse(results[1]['device_receipt_verified'])
    def test_successful_channel_is_not_resent_on_retry(self):
        enqueue_test(self.state);calls=[]
        def accepted(*args):calls.append(1);return {'provider_accepted':True,'device_receipt_verified':False}
        config={'email':{'enabled':True},'mobile':{'enabled':True}}
        deliver(self.state,config,senders={'email':accepted,'mobile':accepted});deliver(self.state,config,senders={'email':accepted,'mobile':accepted});self.assertEqual(len(calls),2)
    def test_secret_topic_redacted_on_provider_failure(self):
        enqueue_test(self.state)
        def fail(*args):raise RuntimeError('https://ntfy.sh/secret-topic')
        result=deliver(self.state,{'mobile':{'enabled':True,'topic':'secret-topic'}},senders={'mobile':fail})
        self.assertNotIn('secret-topic',str(result));self.assertIn('[redacted]',str(result))
    def test_date_ranges_keep_year_time_uncertainty_and_evidence(self):
        rows=deadline_candidates('博士招生预报名时间2026年10月20日至11月20日，材料必须按时提交。')
        self.assertEqual([r['date'] for r in rows],['2026-10-20','2026-11-20']);self.assertTrue(all(r['time'] is None and not r['automatic_reminder_eligible'] and r['official_quote'] for r in rows))
    def test_spa_notice_identity_not_collapsed_to_homepage(self):
        self.assertNotEqual(canonical('https://edu.example.edu.cn/#/view/id=001'),canonical('https://edu.example.edu.cn/#/view/id=002'))

if __name__=='__main__':unittest.main()
