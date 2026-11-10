"""Independent delivery attempts; acceptance is never device receipt or reading."""
import json,os,ssl,smtplib
from email.message import EmailMessage
from pathlib import Path
import requests
from .core import State,load_json,now

def send_email(payload,config):
    password=os.environ.get(config.get('password_env','DOCTORAL_SMTP_PASSWORD'))
    if not password:raise ValueError('smtp_credential_not_configured')
    msg=EmailMessage();msg['From']=config['username'];msg['To']=config['recipient'];msg['Subject']='[博士招生监控试点] '+payload.get('title','新公告')[:100]
    msg.set_content('\n'.join([payload.get('event_type','new_notice'),payload.get('title',''),payload.get('url',''),'归档状态：'+payload.get('archive_status','待核验'),'报名日期和适用范围请核对官方原文；这是一条试点通知。','事件ID：'+payload['event_id']]))
    with smtplib.SMTP_SSL(config.get('host','smtp.163.com'),config.get('port',465),timeout=25,context=ssl.create_default_context()) as smtp:
        smtp.login(config['username'],password);rejected=smtp.send_message(msg)
        if rejected:raise ValueError('smtp_recipient_rejected')
    return {'provider_accepted':True,'device_receipt_verified':False,'user_read_verified':False}

def send_mobile(payload,config):
    topic=config.get('topic') or os.environ.get(config.get('topic_env','DOCTORAL_NTFY_TOPIC'))
    if not topic:raise ValueError('mobile_topic_not_configured')
    headers={}
    token=os.environ.get(config.get('token_env','DOCTORAL_NTFY_TOKEN'))
    if token:headers['Authorization']='Bearer '+token
    response=requests.post(config.get('server','https://ntfy.sh').rstrip('/'),json={'topic':topic,'title':'博士招生监控试点','message':payload.get('title','新公告')+'\n'+payload.get('url','')+'\n日期/对象请核对原文；事件 '+payload['event_id'][:16],'priority':3,'tags':['mortar_board']},headers=headers,timeout=25)
    response.raise_for_status();data=response.json()
    return {'provider_accepted':True,'provider_id':data.get('id'),'device_receipt_verified':False,'user_read_verified':False}

def deliver(state,config,limit=10,senders=None):
    senders=senders or {'email':send_email,'mobile':send_mobile};results=[]
    rows=state.db.execute("SELECT * FROM outbox WHERE status IN ('queued','failed') ORDER BY rowid LIMIT ?",(limit,)).fetchall()
    for row in rows:
        channel=row['channel'];channel_config=config.get(channel,{})
        if not channel_config.get('enabled'):
            results.append({'event_id':row['event_id'],'channel':channel,'status':'not_configured'});continue
        try:
            result=senders[channel](json.loads(row['payload']),channel_config);status='provider_accepted';error=None
        except Exception as exc:
            status='failed';error=type(exc).__name__+':'+str(exc)[:300];result={'provider_accepted':False,'device_receipt_verified':False}
            for secret_key in ['password','topic','token']:
                secret=channel_config.get(secret_key)
                if secret:error=error.replace(str(secret),'[redacted]')
        with state.db:
            state.db.execute('UPDATE outbox SET status=?,attempts=attempts+1,error=?,accepted_at=? WHERE event_id=? AND channel=?',(status,error,now() if status=='provider_accepted' else None,row['event_id'],channel))
        results.append({'event_id':row['event_id'],'channel':channel,'status':status,'error':error,**result})
    return results

def enqueue_test(state):
    payload={'event_id':'pilot-channel-test-20261110','title':'通道连通测试：不是招生截止通知','event_type':'channel_test','url':'https://docs.ntfy.sh/subscribe/phone/','archive_status':'test_only'}
    with state.db:
        for channel in ['email','mobile']:
            state.db.execute('INSERT OR IGNORE INTO outbox(event_id,channel,status,payload) VALUES(?,?,?,?)',(payload['event_id'],channel,'queued',json.dumps(payload,ensure_ascii=False)))
    return payload['event_id']

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--data-dir',default=os.environ.get('DOCTORAL_MONITOR_DATA_DIR','data'));p.add_argument('--private-config',default='private/notifications.json');p.add_argument('--test-only',action='store_true');p.add_argument('--send',action='store_true');p.add_argument('--limit',type=int,default=2);a=p.parse_args()
    state=State(Path(a.data_dir)/'state/monitor.sqlite3')
    if a.test_only:enqueue_test(state)
    if a.send:
        config=load_json(a.private_config)
        if a.test_only:
            rows=state.db.execute("SELECT event_id,channel,status FROM outbox WHERE event_id='pilot-channel-test-20261110'").fetchall()
            # Test runs use an isolated temporary queue so admissions notices cannot leak out.
            import tempfile
            with tempfile.TemporaryDirectory(dir=Path(a.data_dir)/'state') as tmp:
                isolated=State(Path(tmp)/'test.sqlite3');enqueue_test(isolated)
                try:result=deliver(isolated,config,limit=2)
                finally:isolated.db.close()
                with state.db:
                    for r in result:
                        if r['status']!='not_configured':state.db.execute('UPDATE outbox SET status=?,attempts=attempts+1,error=?,accepted_at=? WHERE event_id=? AND channel=?',(r['status'],r.get('error'),now() if r['status']=='provider_accepted' else None,r['event_id'],r['channel']))
        else:result=deliver(state,config,limit=a.limit)
        print(json.dumps(result,ensure_ascii=False))
    else:print(json.dumps({'dry_run':True,'queued':state.db.execute("SELECT COUNT(*) FROM outbox WHERE status='queued'").fetchone()[0],'messages_sent':0}))

if __name__=='__main__':main()
