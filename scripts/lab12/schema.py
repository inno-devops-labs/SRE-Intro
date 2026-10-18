"""Read schema/index/date evidence using credentials supplied to the tools pod."""
from datetime import datetime, timezone
import json
import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

database=sys.argv[1] if len(sys.argv)>1 else 'quickticket'
if database!='quickticket' and not database.startswith('lab12_'):
    raise ValueError('Unexpected database')
with psycopg2.connect(host=os.environ['PGHOST'],user=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],dbname=database) as conn:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        result={'timestamp':datetime.now(timezone.utc).isoformat(),'database':database}
        for name,query in [
            ('columns',"SELECT column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name='events' ORDER BY column_name"),
            ('indexes',"SELECT c.relname,i.indisvalid,i.indisready,pg_get_indexdef(i.indexrelid) definition FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE i.indrelid='events'::regclass ORDER BY c.relname"),
            ('counts','SELECT (SELECT count(*) FROM events) events,(SELECT count(*) FROM orders) orders'),
            ('revision','SELECT version_num FROM alembic_version')]:
            cur.execute(query); result[name]=cur.fetchall()
        columns={c['column_name'] for c in result['columns']}
        dates=','.join(c for c in ('event_date','scheduled_at') if c in columns)
        cur.execute('SELECT id,'+dates+' FROM events ORDER BY id')
        result['dates']=cur.fetchall()
        print(json.dumps(result,default=str))
