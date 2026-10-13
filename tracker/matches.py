"""Match persistence and recovery; existing captures remain immutable evidence.

    Migration groups only already-proven merged records. It never compares
    damage or guesses new historical match IDs.
"""
import json
import uuid
from .store_time import timestamp


def migrate(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS matches(
            id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
            match_key TEXT NOT NULL, created TEXT NOT NULL,
            attribution TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
            canonical_capture INTEGER, UNIQUE(session_id,match_key));
        CREATE TABLE IF NOT EXISTS ocr_results(
            id INTEGER PRIMARY KEY, capture_id INTEGER NOT NULL REFERENCES captures(id),
            created TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reviews(
            id INTEGER PRIMARY KEY, capture_id INTEGER NOT NULL REFERENCES captures(id),
            created TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS match_stats(
            match_id INTEGER NOT NULL REFERENCES matches(id), player_id INTEGER NOT NULL REFERENCES players(id),
            damage INTEGER NOT NULL, kills INTEGER NOT NULL, assists INTEGER NOT NULL,
            PRIMARY KEY(match_id,player_id));
        CREATE TABLE IF NOT EXISTS lifecycle_events(
            id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
            created TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
    ''')
    if 'match_id' not in {r[1] for r in db.execute('PRAGMA table_info(captures)')}:
        db.execute('ALTER TABLE captures ADD COLUMN match_id INTEGER REFERENCES matches(id)')
    rows=db.execute('SELECT * FROM captures ORDER BY id').fetchall()
    with db:
        for row in rows:
            if row['match_id'] is not None: continue
            canonical=row['id']
            current=row
            seen=set()
            while current['merged_into'] and canonical not in seen:
                seen.add(canonical)
                parent=db.execute('SELECT * FROM captures WHERE id=?',(current['merged_into'],)).fetchone()
                a,b=json.loads(current['result']),json.loads(parent['result']) if parent else {}
                proven=parent and current['session_id']==parent['session_id'] and (
                    current['hash'] and current['hash']==parent['hash'] or
                    a.get('verified_match_group') and a.get('verified_match_group')==b.get('verified_match_group') or
                    a.get('source')==b.get('source')=='automatic' and a.get('automatic_epoch') and a.get('automatic_epoch')==b.get('automatic_epoch'))
                if not proven:break
                canonical=parent['id'];current=parent
            key='legacy:'+str(canonical)
            db.execute('INSERT OR IGNORE INTO matches(session_id,match_key,created,attribution) VALUES (?,?,?,?)',
                       (row['session_id'],key,row['created'],'legacy_preserved'))
            mid=db.execute('SELECT id FROM matches WHERE session_id=? AND match_key=?',(row['session_id'],key)).fetchone()[0]
            db.execute('UPDATE captures SET match_id=? WHERE id=?',(mid,row['id']))
            db.execute('INSERT INTO ocr_results(capture_id,created,version,payload) VALUES (?,?,?,?)',
                       (row['id'],row['created'],json.loads(row['result']).get('recognition_version',0),row['result']))
        db.execute('PRAGMA user_version=14')


class MatchRepository:
    def __init__(self,db): self.db=db

    def begin(self,session_id,event):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO matches(session_id,match_key,created,attribution,state) VALUES (?,?,?,?,'open')",
                            (session_id,event['match_key'],timestamp(),'start_observed'))
        self.audit(session_id,'match_started',event)

    def attach(self,session_id,result):
        key=result.get('match_key')
        episode=result.get('episode_key')
        reviewed=set()
        if episode and result.get('attribution')!='reviewed' and not result.get('keep_separate'):
            for row in self.db.execute('SELECT result FROM captures WHERE session_id=?',(session_id,)):
                data=json.loads(row[0])
                if data.get('episode_key')==episode and data.get('attribution')=='reviewed' and not data.get('keep_separate'):
                    reviewed.add(data.get('match_key'))
            if len(reviewed)==1:
                key=next(iter(reviewed));result.update(match_key=key,attribution='episode_reviewed')
        if not key and result.get('source')=='automatic' and result.get('automatic_epoch'):
            key='oldautomatic:'+result['automatic_epoch']
        attribution=result.get('attribution','manual')
        if not key and result.get('observation_version')==14:
            key='episode:'+result['episode_key']
            existing=self.db.execute('SELECT id FROM matches WHERE session_id=? AND match_key=?',(session_id,key)).fetchone()
            any_match=self.db.execute('SELECT 1 FROM matches WHERE session_id=? LIMIT 1',(session_id,)).fetchone()
            attribution='bootstrap' if not any_match or existing and self.db.execute(
                'SELECT attribution FROM matches WHERE id=?',existing).fetchone()[0]=='bootstrap' else 'needs_review'
            if len(reviewed)>1:attribution='needs_review'
            result['attribution']=attribution
        if not key: key='manual:'+uuid.uuid4().hex
        result['match_key']=key
        self.db.execute('INSERT OR IGNORE INTO matches(session_id,match_key,created,attribution) VALUES (?,?,?,?)',
                        (session_id,key,timestamp(),attribution))
        return self.db.execute('SELECT id FROM matches WHERE session_id=? AND match_key=?',(session_id,key)).fetchone()[0]

    def record_ocr(self,cid,result):
        self.db.execute('INSERT INTO ocr_results(capture_id,created,version,payload) VALUES (?,?,?,?)',
                        (cid,timestamp(),result.get('recognition_version',0),json.dumps(result,ensure_ascii=False)))

    def sync(self,session_id):
        """Materialize final stats only from the selected canonical evidence."""
        with self.db:
            mids=[r[0] for r in self.db.execute('SELECT id FROM matches WHERE session_id=?',(session_id,))]
            for mid in mids:
                self.db.execute('DELETE FROM match_stats WHERE match_id=?',(mid,))
                row=self.db.execute('SELECT id,status,result FROM captures WHERE match_id=? AND merged_into IS NULL ORDER BY id LIMIT 1',(mid,)).fetchone()
                if row is None:
                    if not self.db.execute('SELECT 1 FROM captures WHERE match_id=?',(mid,)).fetchone():
                        continue  # A verified start can exist before its first capture.
                    self.db.execute("UPDATE matches SET state='superseded',canonical_capture=NULL WHERE id=?",(mid,));continue
                result=json.loads(row['result'])
                status=row['status']
                if result.get('attribution')=='needs_review' and status!='confirmed':status='unattributed'
                self.db.execute('UPDATE matches SET state=?,canonical_capture=? WHERE id=?',(status,row['id'],mid))
                if status=='confirmed':
                    self.db.execute('''INSERT INTO match_stats SELECT ?,player_id,damage,kills,assists FROM stats WHERE capture_id=?''',(mid,row['id']))

    def audit(self,session_id,kind,payload):
        with self.db:
            self.db.execute('INSERT INTO lifecycle_events(session_id,created,kind,payload) VALUES (?,?,?,?)',
                            (session_id,timestamp(),kind,json.dumps(payload,ensure_ascii=False)))

    def list(self,session_id):
        return [dict(r) for r in self.db.execute('SELECT * FROM matches WHERE session_id=? AND state!=? ORDER BY id DESC',(session_id,'superseded'))]
