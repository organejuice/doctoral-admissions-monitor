import argparse,json,os
from pathlib import Path
from .core import Discovery,start_bridge,run,ROOT

def main():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    for command in ['bridge','run']:
        p=sub.add_parser(command);p.add_argument('--config',default='config/pilot_sources.json');p.add_argument('--data-dir',default=os.environ.get('DOCTORAL_MONITOR_DATA_DIR','data'));p.add_argument('--run-id',default='pilot-20261110-01')
    b=sub.choices['bridge'];b.add_argument('--port',type=int,default=8765);b.add_argument('--max-pages',type=int,default=2)
    r=sub.choices['run'];r.add_argument('--rsshub',default='http://127.0.0.1:1200');r.add_argument('--max-notices',type=int,default=2);r.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if args.command=='bridge':
        discovery=Discovery(args.config,args.data_dir,args.run_id,args.max_pages)
        server=start_bridge(discovery,args.port);print(json.dumps({'bridge':'127.0.0.1','port':args.port,'sources':len(discovery.sources)},ensure_ascii=False),flush=True);server.serve_forever()
    else:print(json.dumps(run(args.config,args.data_dir,args.run_id,args.rsshub,args.max_notices,args.workers),ensure_ascii=False),flush=True)

if __name__=='__main__':main()
