"""Official-source adapters, immutable responses and versioned notice storage.

Adapted from this project's public_sources.py/site_probe.py: UTF-8/GB18030
decoding, official base/constant links, per-host locks, raw SHA256 and actual
transport status. New monitoring state is separate from frozen catalogues.
"""
from __future__ import annotations
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, urljoin, urldefrag, parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib, io, json, os, re, shutil, sqlite3, subprocess, tempfile, threading, time, uuid, zipfile
import xml.etree.ElementTree as ET
import requests
import yaml
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT=Path(__file__).resolve().parents[1]
SIGNAL=re.compile(r'博士|申请.?考核|申请审核|招生简章|招生章程|报名|网报|补招|复试|延期|更正|推荐信')
NON_ADMISSION=re.compile(r'博士后|博士招聘|招聘博士|学位英语|毕业答辩|导师资格')
ASSET=re.compile(r'\.(?:pdf|docx?|xlsx?|zip|rar|jpe?g|png|gif|webp)(?:[?#]|$)|(?:download|fileDown|wbfile|downfile)',re.I)
DEFAULT_BODIES=['#js_content','.articleTitle02','.b-abody','.yxk-news-contain.UEditor','.v_news_content','.wp_articlecontent','.TRS_Editor','.article-content','.article_content','.Article_Content','.content-detail','.news_content','.newscontent','#zoom','.text','.content','article']

def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(b):return hashlib.sha256(b).hexdigest()
def compact(t):return re.sub(r'\s+',' ',t or '').strip()
def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.pending')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def load_json(path):return json.loads(Path(path).read_text('utf-8-sig'))
def canonical(url):
    p=urlsplit(url)
    frag=p.fragment if ('id=' in p.fragment or p.fragment.startswith('/')) else ''
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path or '/',p.query,frag))
def decode(body):
    match=re.search(br'charset\s*=\s*["\']?([A-Za-z0-9_-]+)',body[:12000])
    for enc in ([match.group(1).decode()] if match else [])+['utf-8','gb18030']:
        try:return body.decode(enc)
        except (LookupError,UnicodeError):pass
    return body.decode('utf-8',errors='replace')
def soup_for(body):return BeautifulSoup(decode(body),'lxml')
def select_links(node,base):
    result=[];seen=set()
    for a in node.select('a[href],a[onclick],[data-link],[data-url]'):
        href=a.get('href','')
        if not href or href.startswith(('javascript:','#')):
            href=a.get('data-link') or a.get('data-url') or ''
            if not href:
                m=re.search(r'(?:window\s*\.\s*open\s*\(|(?:window\s*\.)?location(?:\s*\.\s*href)?\s*=)\s*([\'\"])(.*?)\1',a.get('onclick',''))
                if m:href=m.group(2)
        if not href:continue
        href=canonical(urljoin(base,href))
        if urlsplit(href).scheme not in ['http','https'] or href in seen:continue
        seen.add(href)
        title=compact(a.get('title') or a.get_text(' ',strip=True) or ' '.join(i.get('alt','') for i in a.select('img')))
        result.append({'title':title,'link':href})
    return result
def barrier(soup,body,status):
    title=compact(soup.title.get_text() if soup.title else '')
    text=compact(soup.get_text(' ',strip=True))
    if status!=200:return f'http_{status}'
    if re.search(r'Access Denied|Forbidden|人机验证|安全验证|访问过于频繁|访问受限',title,re.I):return 'access_barrier'
    if len(text)<2500 and re.search(r'请完成.{0,12}验证|verify you are human|安全检查中|Just a moment|WAF拦截',text,re.I):return 'access_barrier'
    if len(text)<80 and not soup.select('img[src],img[data-src],[pdfsrc],iframe[src],embed[src],object[data]'):return 'empty_or_placeholder'
    return None
def host_allowed(url,source):
    host=(urlsplit(url).hostname or '').lower()
    hosts=source.get('allowed_hosts') or [urlsplit(source['url']).hostname]
    suffixes=source.get('allowed_host_suffixes',[])
    return any(host==str(h).lower() for h in hosts) or any(host==s.lstrip('.') or host.endswith('.'+s.lstrip('.')) for s in suffixes)

class Fetcher:
    def __init__(self,data_dir,run_id,limit=16777216,timeout=25,interval=.5):
        self.data=Path(data_dir);self.run_id=run_id;self.limit=limit;self.timeout=timeout;self.interval=interval
        self.locks={};self.guard=threading.Lock();self.memo={};self.sequence=0
    def fetch(self,url,source,headers=None):
        url=canonical(url);key=(url,json.dumps(headers or {},sort_keys=True))
        with self.guard:
            if key in self.memo:return self.memo[key]
            lock=self.locks.setdefault(urlsplit(url).hostname,threading.Lock())
        with lock:
            with self.guard:
                if key in self.memo:return self.memo[key]
            m={'requested_url':url,'final_url':url,'checked_at_utc':now(),'http_status':None,'method':'requests normal GET, TLS verification enabled','error':None,'headers':{},'raw_path':None,'sha256':None,'complete_response':False}
            body=b''
            if not host_allowed(url,source):m['error']='host_not_in_source_allowlist'
            else:
                time.sleep(self.interval)
                try:
                    current=urldefrag(url)[0]
                    for redirect in range(6):
                        with requests.get(current,headers={'User-Agent':'DoctoralAdmissionsMonitor/0.1 (public admissions notices)','Accept':'text/html,application/json,application/pdf,*/*',**(headers or {})},timeout=(10,self.timeout),allow_redirects=False,stream=True) as r:
                            m.update(http_status=r.status_code,final_url=r.url,headers={k:v for k,v in r.headers.items() if k.lower() in ['content-type','content-length','content-disposition','last-modified','etag','retry-after','location']})
                            if r.is_redirect:
                                target=urljoin(current,r.headers.get('Location',''))
                                if not host_allowed(target,source):raise ValueError('redirect_host_not_in_source_allowlist')
                                current=target;continue
                            chunks=[];size=0
                            for chunk in r.iter_content(65536):
                                size+=len(chunk)
                                if size>self.limit:raise ValueError('response_exceeds_byte_budget')
                                chunks.append(chunk)
                            body=b''.join(chunks);m['complete_response']=True;break
                    else:raise ValueError('too_many_redirects')
                except Exception as exc:m['error']=str(exc)[:1000]
            if body:
                sha=digest(body);path=self.data/'raw/http'/f'{sha}.body';path.parent.mkdir(parents=True,exist_ok=True)
                if not path.exists():
                    if shutil.disk_usage(path.parent).free<len(body)+16777216:raise OSError('archive_storage_capacity_low; original not marked acquired')
                    path.write_bytes(body)
                else:assert digest(path.read_bytes())==sha
                m.update(raw_path=str(path.relative_to(self.data)).replace('\\','/'),sha256=sha,bytes=len(body))
            with self.guard:self.sequence+=1;seq=self.sequence
            meta=self.data/'raw/runs'/self.run_id/'http'/f'{time.time_ns()}_{digest(url.encode())[:16]}.json'
            if meta.exists():raise FileExistsError('immutable raw observation collision')
            write_json(meta,m);m['metadata_path']=str(meta.relative_to(self.data)).replace('\\','/')
            with self.guard:self.memo[key]=(m,body)
            return m,body

class Discovery:
    def __init__(self,config,data_dir,run_id,max_pages=2):
        conf=load_json(config);sources=conf.get('sources',conf.get('pilot_sources',[]))
        if isinstance(sources,dict):sources=list(sources.values())
        self.sources={s['source_id']:s for s in sources};self.data=Path(data_dir);self.run_id=run_id;self.max_pages=max_pages
        self.fetcher=Fetcher(data_dir,run_id);self.cache={};self.lock=threading.Lock();self.source_locks={i:threading.Lock() for i in self.sources}
    def discover(self,source_id):
        if source_id not in self.sources:raise KeyError(source_id)
        with self.source_locks[source_id]:
            if source_id in self.cache:return self.cache[source_id]
            s=self.sources[source_id];queue=[s['url']];visited=[];items={};observations=[];failures=[]
            while queue and len(visited)<self.max_pages:
                url=queue.pop(0)
                if url in visited:continue
                visited.append(url);m,body=self.fetcher.fetch(url,s);observations.append(m)
                if m['error'] or m['http_status']!=200:
                    failures.append({'url':url,'reason':m['error'] or f"http_{m['http_status']}"});continue
                soup=soup_for(body);bad=barrier(soup,body,m['http_status'])
                if bad:failures.append({'url':url,'reason':bad});continue
                ext=s.get('extraction',{});transport=s.get('transport',s.get('kind','html'))
                links=[]
                if transport=='json':
                    try:
                        obj=json.loads(decode(body));arr=obj
                        for part in ext.get('list_path','').split('.'):
                            if part:arr=arr[int(part)] if isinstance(arr,list) else arr[part]
                        for row in arr:
                            links.append({'title':compact(str(row.get(ext.get('title_key','title'),''))),'link':canonical(urljoin(url,str(row.get(ext.get('link_key','url'),'')))),'pubDate':row.get(ext.get('date_key','date'))})
                    except Exception as exc:failures.append({'url':url,'reason':'json_list_parse:'+str(exc)});continue
                else:
                    base=urljoin(m['final_url'],soup.select_one('base[href]').get('href')) if soup.select_one('base[href]') else m['final_url']
                    selector=s.get('link_selector') or ext.get('link_selector') or s.get('list_selector') or 'a[href]'
                    for node in soup.select(selector):
                        if node.name=='a':
                            wrapper=BeautifulSoup(str(node),'lxml');links.extend(select_links(wrapper,base))
                        else:links.extend(select_links(node,base))
                    # Some official columns embed their published static links in script CDATA.
                    if not links and (ext.get('embedded_html') or ext.get('embedded_datastore')):
                        for script in soup.select('script'):
                            raw=script.string or script.get_text()
                            for fragment in re.findall(r'<!\[CDATA\[(.*?)\]\]>',raw,re.S):links.extend(select_links(BeautifulSoup(fragment,'lxml'),base))
                    pagination=s.get('pagination') or {}
                    if isinstance(pagination,dict):
                        next_selector=pagination.get('next_selector') or pagination.get('selector')
                        if next_selector:
                            for a in soup.select(next_selector):
                                label=compact(a.get_text(' ',strip=True));href=a.get('href')
                                if href and ('下' in label or 'next' in label.lower() or len(soup.select(next_selector))==1):
                                    nxt=canonical(urljoin(base,href))
                                    if nxt not in visited and nxt not in queue and host_allowed(nxt,s):queue.append(nxt)
                        explicit=pagination.get('verified_urls') or pagination.get('urls') or []
                        for nxt in explicit:
                            if nxt not in queue and nxt not in visited and host_allowed(nxt,s):queue.append(nxt)
                candidates=[a for a in links if SIGNAL.search(a['title']) and not NON_ADMISSION.search(a['title']) and host_allowed(a['link'],s)]
                for a in candidates:
                    a['description']=a['title'];a['guid']=a['link'];a['discovery_url']=url;items.setdefault(a['link'],a)
                if not candidates:failures.append({'url':url,'reason':'no_verified_admission_items_in_selected_list'})
            result={'source_id':source_id,'title':s.get('title') or s.get('name') or source_id,'link':s['url'],'description':'官方博士招生候选；具体年度、对象、正文与附件状态另行核验。','item':list(items.values()),'status':'ok' if items and not failures else 'partial' if items else 'failed','observations':observations,'failures':failures,'pages_visited':visited,'backfill_complete':False,'backfill_scope':'pilot bounded pages; current-cycle historical coverage not certified','continuation_urls':queue,'bindings':s.get('bindings',[]),'run_id':self.run_id}
            write_json(self.data/'processed/rsshub_inputs'/f'{source_id}.json',result)
            self.cache[source_id]=result;return result

def start_bridge(discovery,port=8765):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            match=re.fullmatch(r'/sources/([A-Za-z0-9_-]+)\.json',self.path)
            if not match or match.group(1) not in discovery.sources:self.send_error(404);return
            try:
                result=discovery.discover(match.group(1));status=200 if result['item'] else 502
                body=json.dumps(result,ensure_ascii=False).encode('utf-8')
                self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            except Exception as exc:
                body=json.dumps({'status':'failed','error':str(exc)}).encode();self.send_response(502);self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    return ThreadingHTTPServer(('127.0.0.1',port),Handler)

def extract_document(body,url,source):
    """Preserve status distinctions: bytes, extracted text, OCR, and completeness."""
    result={'text':'','title':'','body_selector':None,'body_status':'unverified','assets':[],'pages':[]}
    if body.startswith(b'%PDF-'):
        reader=PdfReader(io.BytesIO(body));pages=[]
        if reader.is_encrypted:
            result.update(document_kind='pdf',body_status='encrypted_original_text_not_extracted');return result
        for n,page in enumerate(reader.pages):
            content=page.get_contents()
            if content is not None and len(content.get_data())>20000000:raise ValueError('pdf_page_stream_over_budget')
            text=page.extract_text() or '';pages.append({'page':n+1,'text':text,'ocr_review_required':len(text.strip())<40})
        text_length=sum(len(x['text']) for x in pages)
        status='scanned_needs_ocr' if text_length<=100 else 'text_extracted_ocr_partial' if any(x['ocr_review_required'] for x in pages) else 'text_extracted'
        result.update(text='\n'.join(x['text'] for x in pages),pages=pages,document_kind='pdf',body_status=status);return result
    if body.startswith(b'PK\x03\x04'):
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            if 'word/document.xml' not in z.namelist():
                result.update(document_kind='office_or_zip_archive',body_status='original_only_text_not_extracted');return result
            info=z.getinfo('word/document.xml')
            if info.file_size>20000000:raise ValueError('docx_xml_over_budget')
            root=ET.fromstring(z.read(info));text='\n'.join(''.join(p.itertext()) for p in root.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'))
        result.update(text=text,document_kind='docx',body_status='text_extracted');return result
    if body.startswith((b'\xff\xd8\xff',b'\x89PNG\r\n\x1a\n',b'GIF87a',b'GIF89a')) or (body[:4]==b'RIFF' and body[8:12]==b'WEBP'):
        result.update(document_kind='image',body_status='image_needs_ocr');return result
    if body.startswith(b'\xd0\xcf\x11\xe0'):
        result.update(document_kind='legacy_office',body_status='legacy_document_needs_conversion');return result
    soup=soup_for(body)
    if barrier(soup,body,200):raise ValueError('html_access_barrier_or_placeholder')
    result['title']=compact(soup.title.get_text() if soup.title else '')
    base=urljoin(url,soup.select_one('base[href]').get('href')) if soup.select_one('base[href]') else url
    candidates=[]
    for sel in source.get('body_selectors',[])+DEFAULT_BODIES:
        for node in soup.select(sel):
            text=compact(node.get_text(' ',strip=True))
            if len(text)>=100 or (node.select('img[src],img[data-src],[pdfsrc],iframe[src],embed[src],object[data]') and sel not in ['.content','.text']):candidates.append((len(text),sel,node,text))
        if candidates:break
    if candidates:
        _,sel,node,text=max(candidates,key=lambda x:x[0]);result.update(text=text,body_selector=sel,body_status='text_extracted')
    else:
        node=soup.body or soup;result['body_status']='body_selector_not_matched'
        for element in node.select('script,style,nav,header,footer,noscript'):element.decompose()
        result['text']=compact(node.get_text(' ',strip=True))
    assets=[]
    for link in select_links(node,base):
        if ASSET.search(link['link']) or re.search(r'附件|下载|申请表|推荐书',link['title']):assets.append({'url':link['link'],'label':link['title'],'kind':'linked_attachment'})
    for image in node.select('img[src],img[data-src]'):
        src=urljoin(base,image.get('data-src') or image.get('src',''))
        if src.startswith(('http://','https://')) and not re.search(r'logo|icon|loading|spacer|share|qrcode',src,re.I):assets.append({'url':src,'label':image.get('alt',''),'kind':'inline_image'})
    for media in node.select('[pdfsrc],iframe[src],embed[src],object[data]'):
        src=media.get('pdfsrc') or media.get('src') or media.get('data') or ''
        src=urljoin(base,src)
        file_param=parse_qs(urlsplit(src).query).get('file',[])
        if file_param:src=urljoin(base,file_param[0])
        if ASSET.search(src):assets.append({'url':src,'label':'官网嵌入文档','kind':'embedded_document'})
    for src in re.findall(r'https?://[^\s<>\"\'）)\]]+',result['text']):
        src=src.rstrip('。，,;；')
        if ASSET.search(src):assets.append({'url':src,'label':'正文明确列出的文件网址','kind':'plain_text_file_url'})
    result.update(assets=list({canonical(a['url']):a for a in assets}.values()),document_kind='html')
    if result['body_selector'] and len(result['text'])<100 and result['assets']:result['body_status']='image_article_needs_ocr'
    if any(a['kind']=='embedded_document' for a in result['assets']) and len(result['text'])<100:result['body_status']='attachment_wrapper'
    return result

def classify(title,text):
    value=title+' '+text
    if NON_ADMISSION.search(title) or (NON_ADMISSION.search(text[:1200]) and not re.search(r'报考条件|招生计划|报名时间|申请材料',text[:1200])):return {'relevance':'not_admission','scope':'excluded_nonadmission','years':[]}
    if '博士' not in value:return {'relevance':'uncertain','scope':'needs_review','years':sorted(set(re.findall(r'20\d{2}',title)))}
    scope='港澳台' if re.search(r'港澳台|香港.{0,8}澳门.{0,8}台湾|港.{0,2}澳.{0,2}台',title) else '留学生' if re.search(r'留学|国际学生',title) else '合作/联合项目待核验' if re.search(r'合作|联合',title) else '普通/申请考核/专项待区分'
    return {'relevance':'doctoral_candidate','scope':scope,'scope_verification':'rule_based_needs_review','years':sorted(set(re.findall(r'20\d{2}',title)))}

def deadline_candidates(text):
    out=[];seen=set()
    pattern=re.compile(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日(?:(?:\s|至|到|—|-){1,8}(?:(20\d{2})\s*年\s*)?(\d{1,2})\s*月\s*(\d{1,2})\s*日)?')
    for m in pattern.finditer(text):
        quote=text[max(0,m.start()-70):min(len(text),m.end()+90)]
        if not re.search(r'报名|网报|申请|截止|材料|寄|缴费|推荐|复试|考核|审核',quote):continue
        for endpoint,(year,month,day) in enumerate([(m[1],m[2],m[3])]+([(m[4] or m[1],m[5],m[6])] if m[5] else [])):
            try:date=dt.date(int(year),int(month),int(day)).isoformat()
            except ValueError:continue
            key=(date,quote)
            if key in seen:continue
            seen.add(key)
            kind=next((label for term,label in [('预报名','预报名'),('推荐','推荐信'),('缴费','缴费'),('寄','纸质材料'),('材料','材料'),('网报','网报'),('报名','报名')] if term in quote),'其他招考节点')
            out.append({'date':date,'time':None,'timezone':'Asia/Shanghai','precision':'date_only','kind':kind,'endpoint':'range_end' if endpoint else 'range_start_or_single','official_quote':quote,'text_offset':m.start(),'verification':'needs_review','automatic_reminder_eligible':False})
    return out

class State:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(path,timeout=30,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.lock=threading.Lock()
        self.db.executescript('''PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY,url TEXT NOT NULL,title TEXT,first_seen TEXT,last_seen TEXT);
CREATE TABLE IF NOT EXISTS versions(id TEXT PRIMARY KEY,notice_id TEXT NOT NULL,body_sha TEXT NOT NULL,asset_sha TEXT NOT NULL,observed_at TEXT NOT NULL,record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bindings(notice_id TEXT,source_id TEXT,binding_json TEXT,PRIMARY KEY(notice_id,source_id,binding_json));
CREATE TABLE IF NOT EXISTS outbox(event_id TEXT,channel TEXT,status TEXT,attempts INTEGER DEFAULT 0,payload TEXT,error TEXT,accepted_at TEXT,PRIMARY KEY(event_id,channel));
CREATE TABLE IF NOT EXISTS source_runs(run_id TEXT,source_id TEXT,status TEXT,last_success TEXT,record_json TEXT,PRIMARY KEY(run_id,source_id));''');self.db.commit()
    def record(self,source,record):
        notice_id=digest(canonical(record['url']).encode())
        body_sha=digest(record['document']['text'].encode()) if record['document'].get('document_kind','html') in ['html','official_json_html_body'] and record['document']['text'] else record['response']['sha256']
        asset_sha=digest(json.dumps(sorted([(a['url'],a.get('sha256'),a['status']) for a in record['assets']]),ensure_ascii=False).encode())
        ver=digest((notice_id+body_sha+asset_sha).encode())
        with self.lock,self.db:
            previous=self.db.execute('SELECT * FROM versions WHERE notice_id=? ORDER BY observed_at DESC LIMIT 1',(notice_id,)).fetchone()
            self.db.execute('INSERT INTO notices VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,last_seen=excluded.last_seen',(notice_id,record['url'],record['title'],now(),now()))
            self.db.execute('INSERT OR IGNORE INTO bindings VALUES(?,?,?)',(notice_id,source['source_id'],json.dumps(source.get('bindings',[]),ensure_ascii=False,sort_keys=True)))
            existing=self.db.execute('SELECT id FROM versions WHERE id=?',(ver,)).fetchone()
            event_type='unchanged' if existing else 'new_notice' if previous is None else 'revised_notice'
            if not existing and previous is not None:
                prev=json.loads(previous['record_json'])
                if prev['response'].get('sha256')==record['response'].get('sha256') and previous['asset_sha']==asset_sha:event_type='metadata_reprocessed'
            record.update(notice_id=notice_id,version_id=ver,event_type=event_type)
            if not existing:
                self.db.execute('INSERT INTO versions VALUES(?,?,?,?,?,?)',(ver,notice_id,body_sha,asset_sha,now(),json.dumps(record,ensure_ascii=False)))
                payload=json.dumps({'event_id':ver,'event_type':event_type,'title':record['title'],'url':record['url'],'version_id':ver,'relevance':record['classification']['relevance'],'archive_status':record['archive_status'],'date_status':'machine_candidates_need_review'},ensure_ascii=False)
                for channel in ([] if record['classification']['relevance']=='not_admission' or event_type=='metadata_reprocessed' else ['email','mobile']):
                    self.db.execute('INSERT OR IGNORE INTO outbox(event_id,channel,status,payload) VALUES(?,?,?,?)',(ver,channel,'queued',payload))
        return record

def detail_target(url,source):
    ext=source.get('extraction',{});api=ext.get('detail_api')
    if not api:return url,None,None
    match=re.search(api.get('id_regex',r'id=([^/&]+)'),url)
    if not match:return url,None,None
    return api['url_template'].replace('{id}',match.group(1)),api.get('headers',{'Accept':'application/json'}),api

def archive_notice(item,source,discovery,state,max_assets=8):
    url=item['link'];fetch_url,headers,api=detail_target(url,source);m,body=discovery.fetcher.fetch(fetch_url,source,headers)
    if m['error'] or m['http_status']!=200 or not m['complete_response']:return {'url':url,'title':item.get('title',''),'status':'failed','response':m,'reason':m['error'] or f"http_{m['http_status']}"}
    try:
        if digest(body) in source.get('extraction',{}).get('reject_body_sha256',[]):raise ValueError('known_http200_placeholder_body')
        if api:
            obj=json.loads(decode(body))
            for key in api.get('root_path','').split('.'):
                if key:obj=obj[key]
            doc=extract_document(str(obj[api.get('content_key','content')]).encode(),url,source)
            doc['title']=str(obj.get(api.get('title_key','title'),item.get('title','')));doc['document_kind']='official_json_html_body';doc['api_response_path']=m['raw_path']
        else:doc=extract_document(body,m['final_url'],source)
        if doc['document_kind'] in ['html','official_json_html_body']:
            if doc['body_status'] not in ['image_article_needs_ocr','attachment_wrapper'] and (len(doc['text'])<100 or ('博士' not in doc['text'] and doc['body_status']!='text_extracted')):raise ValueError('notice_body_not_verified')
            if doc['body_status']=='body_selector_not_matched':
                if len(doc['text'])<600 or len(re.findall(r'报考条件|报名时间|申请材料|报名程序|考核方式|招生计划|招生专业|一、|二、|三、',doc['text']))<3:raise ValueError('notice_body_selector_not_matched_or_navigation_only')
                doc['body_status']='body_fallback_validated'
    except Exception as exc:return {'url':url,'title':item.get('title',''),'status':'failed','response':m,'reason':str(exc)}
    assets=[];pending=[]
    for n,a in enumerate(doc['assets']):
        if n>=max_assets:pending.append(a);continue
        am,ab=discovery.fetcher.fetch(a['url'],source)
        asset={**a,**{k:am.get(k) for k in ['http_status','sha256','bytes','raw_path','metadata_path','checked_at_utc']},'status':'failed','error':am.get('error')}
        if not am['error'] and am['http_status']==200 and am['complete_response']:
            try:
                adoc=extract_document(ab,am['final_url'],source)
                if adoc['document_kind']=='html':asset.update(status='unexpected_html_not_attachment',error='download returned HTML; attachment not acquired')
                else:
                    asset.update(status='original_acquired',document_kind=adoc['document_kind'],text_status=adoc['body_status'],pages=adoc.get('pages',[]),text=adoc['text'])
            except Exception as exc:asset['error']=str(exc)
        assets.append(asset)
    title=item.get('title') or doc.get('title') or url
    if doc['body_status']=='attachment_wrapper':
        if not any(a['status']=='original_acquired' for a in assets):return {'url':url,'title':title,'status':'failed','response':m,'assets':assets,'pending_assets':pending,'reason':'embedded_original_not_acquired'}
        doc['body_status']='body_in_attachment' if any(len(a.get('text',''))>=100 for a in assets) else 'attachment_original_text_pending'
    full_text=doc['text']+'\n'+'\n'.join(a.get('text','') for a in assets)
    record={'url':canonical(url),'title':title,'source_id':source['source_id'],'observed_at':now(),'publication_date':item.get('pubDate'),'publication_date_verification':'unknown' if not item.get('pubDate') else 'feed_value_not_independently_verified','response':m,'document':doc,'assets':assets,'pending_assets':pending,'classification':classify(title,full_text),'deadline_candidates':deadline_candidates(full_text),'archive_status':'original_acquired_text_pending' if doc['body_status'] not in ['text_extracted','body_fallback_validated','body_in_attachment'] else 'body_extracted_assets_partial' if pending or any(a['status']!='original_acquired' for a in assets) else 'body_extracted_assets_acquired','coverage_limit':'automated extraction; appendix applicability and date candidates require review','status':'acquired'}
    record=state.record(source,record)
    version_path=discovery.data/'processed/notices'/record['notice_id']/(record['version_id']+'.json')
    if not version_path.exists():write_json(version_path,record)
    write_json(discovery.data/'processed/observations'/discovery.run_id/(source['source_id']+'_'+record['notice_id']+'_'+uuid.uuid4().hex[:8]+'.json'),record)
    return record

def parse_feed(body):
    root=ET.fromstring(body);items=[]
    for item in root.findall('./channel/item'):
        link=item.findtext('link')
        if link:items.append({'title':item.findtext('title') or '', 'link':canonical(link),'guid':item.findtext('guid') or link})
    if root.tag!='rss':raise ValueError('unexpected_feed_format')
    return items

def verify_baseline(config):
    """Check bound input hashes; cloud export may carry only manifest plus CSVs."""
    conf=load_json(config);contract=conf.get('baseline',{})
    baseline_path=contract.get('manifest_path')
    if not baseline_path:
        baseline_path=ROOT.parent/'catalogue_baseline/manifest.json'
    else:
        baseline_path=Path(baseline_path)
        if not baseline_path.is_absolute():baseline_path=(Path(config).resolve().parent/baseline_path).resolve()
    manifest=load_json(baseline_path);root=baseline_path.parent.parent
    manifest_sha=digest(baseline_path.read_bytes())
    if contract.get('validation_mode')=='exported_registry':
        registry=(Path(config).resolve().parent/contract['source_registry_path']).resolve()
        if manifest_sha!=contract['manifest_sha256'] or digest(registry.read_bytes())!=contract['source_registry_sha256']:raise ValueError('exported_baseline_integrity_mismatch')
        if manifest['baseline_id']!=conf['baseline_id']:raise ValueError('exported_baseline_id_mismatch')
        return {'baseline_id':manifest['baseline_id'],'manifest_sha256':manifest_sha,'validation_mode':'exported_registry','files_checked':2,'original_seven_tables_rechecked':False,'scope':'registry and original manifest hashes verified; originals were checked before publication'}
    for entry in manifest['files']:
        path=root/entry['relative_path']
        if not path.is_file() or digest(path.read_bytes())!=entry['sha256']:raise ValueError('frozen_baseline_mismatch:'+entry['relative_path'])
    return {'baseline_id':manifest['baseline_id'],'manifest_sha256':manifest_sha,'validation_mode':'original_seven_files','files_checked':len(manifest['files'])}

def run(config,data_dir,run_id,rsshub,max_notices=2,workers=4):
    baseline=verify_baseline(config)
    data=Path(data_dir);data.mkdir(parents=True,exist_ok=True)
    if (data/'reports'/run_id/'report.json').exists():raise ValueError('completed_run_id_already_exists; use a new run id')
    provenance={'run_id':run_id,'started_at_utc':now(),'config_sha256':digest(Path(config).read_bytes()),'core_sha256':digest(Path(__file__).read_bytes()),'baseline':baseline,'limits':{'max_pages_per_source':2,'max_notices_per_source':max_notices,'max_assets_per_notice':8,'response_byte_limit':16777216},'data_dir':str(data.resolve()),'notification_send_enabled':False}
    proof=data/'raw/runs'/run_id/'runtime_provenance.json'
    if proof.exists():raise ValueError('run already started; preserve its evidence and use a new run id')
    write_json(proof,provenance)
    state=State(data/'state/monitor.sqlite3')
    # The separately running bridge performs official discovery and writes inputs.
    config_obj=load_json(config);sources=config_obj.get('sources',config_obj.get('pilot_sources',[]));results=[]
    archive_discovery=Discovery(config,data,run_id,max_pages=2)
    def process(source):
        sid=source['source_id'];feed_url=rsshub.rstrip('/')+'/doctoral/'+sid
        result={'source_id':sid,'title':source.get('title',source.get('name',sid)),'url':source['url'],'rsshub_url':feed_url,'rsshub_status':'failed','notices':[],'failures':[],'checked_at_utc':now(),'backfill_complete':False}
        items=[]
        try:
            response=requests.get(feed_url,timeout=(10,150));result['rsshub_http_status']=response.status_code
            path=data/'raw/runs'/run_id/'feeds'/(sid+'_'+digest(response.content)+'.xml');path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists():path.write_bytes(response.content)
            result.update(feed_path=str(path.relative_to(data)),feed_sha256=digest(response.content))
            response.raise_for_status();items=parse_feed(response.content);result['rsshub_items']=len(items)
            if not items:raise ValueError('rsshub_empty_feed_not_success')
            result['rsshub_status']='ok'
        except Exception as exc:result['failures'].append('rsshub:'+str(exc)[:800])
        bridge_path=data/'processed/rsshub_inputs'/(sid+'.json')
        if bridge_path.exists():
            evidence=load_json(bridge_path);result['list_status']=evidence['status'];result['list_failures']=evidence['failures'];result['pages_visited']=evidence['pages_visited'];result['continuation_urls']=evidence['continuation_urls'];result['bindings']=evidence.get('bindings',[])
        seeds=[]
        for seed in source.get('seed_notices',[]):
            if isinstance(seed,str):seed={'link':seed,'title':''}
            else:seed={**seed,'link':seed.get('link') or seed.get('url')}
            if seed.get('link'):seeds.append(seed)
        priority=lambda i:(('2027' in i.get('title',''))*10+('博士' in i.get('title',''))*4+('简章' in i.get('title','') or '章程' in i.get('title',''))*3)
        candidates=list({canonical(i['link']):i for i in sorted(items,key=priority,reverse=True)+seeds}.values())
        selected=candidates[:max_notices]
        # Always attempt a configured primary seed when it is absent from the feed.
        if seeds and seeds[0]['link'] not in [i['link'] for i in selected]:selected=(selected[:max(0,max_notices-1)]+seeds[:1])
        for item in selected:result['notices'].append(archive_notice(item,source,archive_discovery,state))
        result['candidate_count']=len(candidates);result['notices_deferred']=max(0,len(candidates)-len(selected));result['status']='passed' if result['rsshub_status']=='ok' and result['notices'] and all(n['status']=='acquired' for n in result['notices']) else 'partial' if any(n['status']=='acquired' for n in result['notices']) else 'failed'
        with state.lock,state.db:
            state.db.execute('INSERT OR REPLACE INTO source_runs VALUES(?,?,?,?,?)',(run_id,sid,result['status'],now() if result['rsshub_status']=='ok' else None,json.dumps(result,ensure_ascii=False)))
        write_json(data/'processed/runs'/run_id/(sid+'.json'),result);return result
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(process,s):s for s in sources}
        for future in as_completed(futures):
            s=futures[future]
            try:results.append(future.result())
            except Exception as exc:results.append({'source_id':s['source_id'],'url':s['url'],'status':'failed','rsshub_status':'failed','notices':[],'failures':[str(exc)]})
    results.sort(key=lambda x:x['source_id']);notices=[n for s in results for n in s.get('notices',[])];acquired=[n for n in notices if n.get('status')=='acquired'];assets=[a for n in acquired for a in n['assets']]
    summary={'run_id':run_id,'completed_at_utc':now(),'baseline':baseline,'sources':len(sources),'rsshub_passed':sum(s['rsshub_status']=='ok' for s in results),'source_results':{k:sum(s['status']==k for s in results) for k in ['passed','partial','failed']},'notice_attempts':len(notices),'notice_originals_acquired':len(acquired),'assets_discovered':sum(len(n['document']['assets']) for n in acquired),'assets_originals_acquired':sum(a['status']=='original_acquired' for a in assets),'assets_needing_ocr_or_conversion':sum(a.get('text_status','').endswith(('ocr','conversion')) for a in assets),'date_candidates_needing_review':sum(len(n['deadline_candidates']) for n in acquired),'full_current_cycle_backfill_complete':False,'cloud_execution_verified':False,'mobile_device_receipt_verified':False,'sources_detail':results}
    write_json(data/'reports'/run_id/'report.json',summary)
    write_json(data/'reports/latest.json',summary)
    return {k:v for k,v in summary.items() if k!='sources_detail'}
