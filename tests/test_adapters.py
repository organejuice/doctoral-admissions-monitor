"""Portable acceptance cases for official-source adapters; no public network."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
import base64
import hashlib
import io
import json
import unittest

from pypdf import PdfWriter
from doctoral_monitor.core import (
    Discovery, Fetcher, archive_notice, canonical, classify,
    detail_target, extract_document,
)

ORIGIN = 'https://admissions.example.edu.cn'
INTRO = '博士招生公告与报名材料。' * 12


def source(**kwargs):
    item = {'source_id': 'fixture', 'url': ORIGIN + '/doctoral/list.html',
            'allowed_hosts': ['admissions.example.edu.cn'],
            'link_selector': '.notices a[href]', 'body_selectors': ['article'],
            'bindings': [{'institution_id': 'UNIT-001', 'role': 'doctoral_publications'}]}
    item.update(kwargs)
    return item


def listing(inner):
    return ('<!doctype html><html><head><meta charset="utf-8"><title>招生公告</title>'
            '</head><body><header>' + INTRO + '</header>'
            '<div class="notices">' + inner + '</div></body></html>').encode()


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.requested = []

    def fetch(self, url, _source, headers=None):
        self.requested.append((url, headers))
        status, data = self.pages[url]
        return {'requested_url': url, 'final_url': url, 'http_status': status,
                'error': None, 'complete_response': True,
                'sha256': hashlib.sha256(data).hexdigest(),
                'raw_path': 'raw/fixture.body', 'metadata_path': 'raw/fixture.json'}, data


class Response:
    def __init__(self, url, status, body=b'', headers=None):
        self.url, self.status_code, self.body = url, status, body
        self.headers = headers or {'Content-Type': 'text/html; charset=utf-8'}
        self.is_redirect = status in (301, 302, 303, 307, 308)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_content(self, _size):
        yield self.body


class AdapterAcceptance(unittest.TestCase):
    def setUp(self):
        # Windows app-container Temp may permit mkdir but deny child writes.
        # Keep all temporary cases under this writable project-owned test area.
        test_area = Path(__file__).resolve().parent
        self.tmp = TemporaryDirectory(prefix='doctoral-adapter-', dir=test_area)
        self.root = Path(self.tmp.name).resolve()
        self.assertEqual(self.root.parent, test_area)
        self.assertTrue(self.root.name.startswith('doctoral-adapter-'))
        self.addCleanup(self.tmp.cleanup)

    def discovery(self, spec, pages, max_pages=2):
        cfg = self.root / 'sources.json'
        cfg.write_text(json.dumps({'sources': [spec]}), encoding='utf-8')
        result = Discovery(cfg, self.root / 'data', 'fixture-run', max_pages=max_pages)
        result.fetcher = FakeFetcher(pages)
        return result

    def test_unknown_source_never_fetches(self):
        spec = source()
        engine = self.discovery(spec, {})
        with self.assertRaises(KeyError):
            engine.discover('unregistered-url')
        self.assertEqual(engine.fetcher.requested, [])

    def test_http202_is_failure_even_with_admission_text(self):
        spec = source()
        engine = self.discovery(spec, {spec['url']: (202, listing('<a href="/fake">博士招生</a>'))})
        result = engine.discover('fixture')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['item'], [])
        self.assertEqual(result['observations'][0]['http_status'], 202)

    def test_known_http200_placeholder_cannot_be_archived_as_original(self):
        body = listing('<p>1</p>')
        spec = source(extraction={'reject_body_sha256': [hashlib.sha256(body).hexdigest()]})
        engine = self.discovery(spec, {spec['url']: (200, body)})
        state = Mock()
        result = archive_notice({'link': spec['url'], 'title': '博士招生简章'}, spec, engine, state)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('placeholder', result['reason'])
        state.record.assert_not_called()

    def test_cdata_published_records_are_discovered_without_javascript(self):
        spec = source(link_selector='li a[href]', extraction={'embedded_datastore': True})
        body = listing('<script type="application/xml"><datastore><recordset>'
                       '<record><![CDATA[<li><a href="/2027.html">2027博士招生章程</a></li>]]></record>'
                       '</recordset></datastore></script>')
        engine = self.discovery(spec, {spec['url']: (200, body)})
        result = engine.discover('fixture')
        self.assertEqual([row['link'] for row in result['item']], [ORIGIN + '/2027.html'])

    def test_json_list_uses_explicit_fields(self):
        spec = source(transport='json', extraction={'list_path': 'data.records',
                      'title_key': 'subject', 'link_key': 'href', 'date_key': 'released'})
        body = json.dumps({'data': {'records': [{'subject': '2027年博士研究生申请考核招生简章',
                            'href': '/apply/2027', 'released': '2026-11-10'}]},
                           'description': INTRO}, ensure_ascii=False).encode()
        engine = self.discovery(spec, {spec['url']: (200, body)})
        result = engine.discover('fixture')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['item'][0]['link'], ORIGIN + '/apply/2027')
        self.assertEqual(result['item'][0]['pubDate'], '2026-11-10')

    def test_cags_fragment_identity_maps_to_published_detail_api(self):
        public = ORIGIN + '/#/dky/view/id=abc123/barId=zs_bss'
        rule = {'id_regex': '/id=([^/]+)',
                'url_template': ORIGIN + '/api/cms/cmsNews/get/{id}',
                'headers': {'Accept': 'application/json'}, 'content_key': 'content'}
        target, headers, _ = detail_target(canonical(public), source(extraction={'detail_api': rule}))
        self.assertEqual(target, ORIGIN + '/api/cms/cmsNews/get/abc123')
        self.assertEqual(headers, {'Accept': 'application/json'})
        self.assertNotEqual(canonical(public), canonical(public.replace('abc123', 'different')))
        # A valid public API response can still contain navigation rather than a charter.
        spec = source(extraction={'detail_api': rule})
        navigation = json.dumps({'title': '2027博士招生章程',
                                 'content': '<html><body><nav>' + INTRO * 3 + '</nav></body></html>'},
                                ensure_ascii=False).encode()
        engine = self.discovery(spec, {target: (200, navigation)})
        state = Mock()
        state.record.side_effect = lambda _s, r: {**r, 'notice_id': 'nav-only',
                                                  'version_id': 'nav-only-v1', 'event_type': 'new_notice'}
        result = archive_notice({'link': public, 'title': '2027博士招生章程'}, spec, engine, state)
        self.assertEqual(result['status'], 'failed')
        state.record.assert_not_called()

    def test_body_and_pdf_and_lazy_scan_are_kept_without_navigation(self):
        body = ('<html><head><title>博士招生章程</title></head><body>'
                '<nav>导航并非招生正文</nav><article><h1>2027年博士招生章程</h1>'
                '<p>一、招生计划。二、报名条件。三、申请材料。</p><p>' + INTRO + '</p>'
                '<a href="../files/rules.pdf">附件：实施细则</a>'
                '<img data-src="/files/charter-scan.jpg" alt="博士简章原图">'
                '<img src="/images/logo.png" alt="学校标志">'
                '</article></body></html>').encode()
        result = extract_document(body, ORIGIN + '/notices/current.html', source())
        self.assertNotIn('导航并非招生正文', result['text'])
        self.assertIn('一、招生计划', result['text'])
        urls = {a['url'] for a in result['assets']}
        self.assertIn(ORIGIN + '/files/rules.pdf', urls)
        self.assertIn(ORIGIN + '/files/charter-scan.jpg', urls)
        self.assertNotIn(ORIGIN + '/images/logo.png', urls)

    def test_scan_and_legacy_office_do_not_claim_extracted_text(self):
        pdf = PdfWriter()
        pdf.add_blank_page(width=100, height=100)
        buffer = io.BytesIO()
        pdf.write(buffer)
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=')
        for data, expected in [(buffer.getvalue(), 'scanned_needs_ocr'),
                               (png, 'image_needs_ocr'),
                               (b'\xd0\xcf\x11\xe0' + bytes(128), 'legacy_document_needs_conversion')]:
            with self.subTest(expected=expected):
                doc = extract_document(data, ORIGIN + '/attachment', source())
                self.assertEqual(doc['body_status'], expected)
                self.assertEqual(doc['text'], '')

    def test_old_date_insert_on_next_page_is_not_discarded(self):
        spec = source(pagination={'next_selector': 'a.next'})
        first = listing('<a href="/current">2027博士招生简章</a><a class="next" href="?page=2">下一页</a>')
        second = listing('<a href="/inserted">2024年博士招生简章补充报名通知（补发）</a>')
        engine = self.discovery(spec, {spec['url']: (200, first), spec['url'] + '?page=2': (200, second)})
        result = engine.discover('fixture')
        self.assertIn(ORIGIN + '/inserted', {x['link'] for x in result['item']})
        self.assertEqual(len(result['pages_visited']), 2)

    def test_page_budget_retains_unread_cursor(self):
        spec = source(pagination={'next_selector': 'a.next'})
        page = listing('<a href="/first">博士报名通知</a><a class="next" href="?page=2">下一页</a>')
        engine = self.discovery(spec, {spec['url']: (200, page)}, max_pages=1)
        result = engine.discover('fixture')
        self.assertEqual(result['continuation_urls'], [spec['url'] + '?page=2'])
        self.assertFalse(result['backfill_complete'])

    def test_short_registration_title_remains_candidate(self):
        spec = source()
        engine = self.discovery(spec, {spec['url']: (200, listing('<a href="/brief">补充报名通知</a>'))})
        result = engine.discover('fixture')
        self.assertEqual([x['link'] for x in result['item']], [ORIGIN + '/brief'])

    def test_scope_and_nonadmission_remain_distinct(self):
        cases = [('博士后招聘通知', '博士后岗位', 'not_admission', 'excluded_nonadmission'),
                 ('2027年港澳台博士招生简章', '报名条件', 'doctoral_candidate', '港澳台'),
                 ('2027年面向香港、澳门、台湾地区招收博士研究生招生简章',
                  '报名条件', 'doctoral_candidate', '港澳台'),
                 ('2027年硕士招生简章', '硕士研究生招生', 'uncertain', 'needs_review')]
        for title, text, relevance, scope in cases:
            with self.subTest(title=title):
                result = classify(title, text)
                self.assertEqual((result['relevance'], result['scope']), (relevance, scope))
        ordinary = classify('2027年普通招考博士研究生招生章程',
                            '本章程规定普通招考考生的报考条件和申请材料。港澳台招生另行通知。')
        self.assertNotEqual(ordinary['scope'], '港澳台')

    def test_transport_keeps_actual_failure_response_bytes_and_metadata(self):
        spec = source()
        body = b'upstream temporary error; preserve this exact response\n'
        fetcher = Fetcher(self.root / 'capture', 'failure-observation', interval=0)
        with patch('doctoral_monitor.core.requests.get', return_value=Response(spec['url'], 503, body)):
            meta, saved = fetcher.fetch(spec['url'], spec)
        self.assertEqual(saved, body)
        self.assertEqual(meta['http_status'], 503)
        self.assertEqual((fetcher.data / meta['raw_path']).read_bytes(), body)
        recorded = json.loads((fetcher.data / meta['metadata_path']).read_text())
        self.assertEqual(recorded['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(recorded['http_status'], 503)

    def test_redirect_outside_official_hosts_is_not_followed(self):
        spec = source()
        response = Response(spec['url'], 302, headers={'Location': 'https://unrelated.invalid/collect'})
        fetcher = Fetcher(self.root / 'redirect', 'redirect-observation', interval=0)
        with patch('doctoral_monitor.core.requests.get', return_value=response) as request:
            meta, body = fetcher.fetch(spec['url'], spec)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(body, b'')
        self.assertIn('redirect_host_not_in_source_allowlist', meta['error'])


if __name__ == '__main__':
    unittest.main()
