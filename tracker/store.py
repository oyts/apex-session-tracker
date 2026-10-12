from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_name(value: str) -> str:
    from .identity import normalize_name as normalize
    return normalize(value)


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.screenshots = directory / "screenshots"
        self.screenshots.mkdir(exist_ok=True)
        self.db = sqlite3.connect(directory / "tracker.sqlite3")
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] < 14 and self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='captures'").fetchone():
            backups=directory/'backups';backups.mkdir(exist_ok=True)
            target=backups/'before-v14.sqlite3'
            if not target.exists():
                with closing(sqlite3.connect(target)) as backup:self.db.backup(backup)
        self.db.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS players(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS aliases(name TEXT NOT NULL, player_id INTEGER NOT NULL
              REFERENCES players(id), PRIMARY KEY(name,player_id));
            CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, title TEXT NOT NULL,
              started TEXT NOT NULL, ended TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active ON sessions((1)) WHERE ended IS NULL;
            CREATE TABLE IF NOT EXISTS captures(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL
              REFERENCES sessions(id), created TEXT NOT NULL, image TEXT NOT NULL, hash TEXT NOT NULL,
              result TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', duplicate_of INTEGER);
            CREATE TABLE IF NOT EXISTS stats(capture_id INTEGER NOT NULL REFERENCES captures(id),
              player_id INTEGER NOT NULL REFERENCES players(id), card INTEGER NOT NULL,
              damage INTEGER NOT NULL CHECK(damage>=0), kills INTEGER NOT NULL CHECK(kills>=0),
              assists INTEGER NOT NULL CHECK(assists>=0), PRIMARY KEY(capture_id,player_id),
              UNIQUE(capture_id,card));
            CREATE TABLE IF NOT EXISTS session_roster(session_id INTEGER NOT NULL REFERENCES sessions(id),
              player_id INTEGER NOT NULL REFERENCES players(id), name TEXT NOT NULL,
              PRIMARY KEY(session_id,player_id));
            CREATE TABLE IF NOT EXISTS session_aliases(session_id INTEGER NOT NULL REFERENCES sessions(id),
              name TEXT NOT NULL, player_id INTEGER NOT NULL REFERENCES players(id),
              PRIMARY KEY(session_id,name,player_id));
        """)
        # Existing records keep their original three-player roster.
        if "player_ids" not in {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}:
            self.db.execute("ALTER TABLE sessions ADD COLUMN player_ids TEXT NOT NULL DEFAULT '[1,2,3]'")
        if "mode" not in {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}:
            self.db.execute("ALTER TABLE sessions ADD COLUMN mode INTEGER NOT NULL DEFAULT 3")
            for row in self.db.execute("SELECT id,player_ids FROM sessions").fetchall():
                self.db.execute("UPDATE sessions SET mode=? WHERE id=?", (len(json.loads(row['player_ids'])), row['id']))
        if 'merged_into' not in {r[1] for r in self.db.execute('PRAGMA table_info(captures)')}:
            self.db.execute('ALTER TABLE captures ADD COLUMN merged_into INTEGER')
        with self.db:
            for player_id in range(1, 4):
                self.db.execute("INSERT OR IGNORE INTO players VALUES (?,?)",
                                (player_id, f"玩家{player_id}"))
            # Freeze existing names before future sessions can discover a different team.
            for session in self.sessions():
                for pid in json.loads(session['player_ids']):
                    self.db.execute('INSERT OR IGNORE INTO session_roster VALUES (?,?,?)',
                                    (session['id'], pid, self.players()[pid-1]['name']))
        if not self.setting("hotkey_v2", False):
            if self.setting("hotkey") in (None, "Ctrl+Alt+F8"):
                self.set_setting("hotkey", "Ctrl+End")
            self.set_setting("hotkey_v2", True)
        self.db.execute('CREATE INDEX IF NOT EXISTS capture_hash ON captures(session_id,hash)')
        self.db.commit()
        from .matches import migrate, MatchRepository
        migrate(self.db)
        self.matches=MatchRepository(self.db)
        for session in self.sessions():self.matches.sync(session['id'])
        self.storage_warning=''
        from .media import prepare_directory
        try:
            self.screenshots=prepare_directory(self.setting('image_directory',str(directory/'screenshots')))
        except OSError as error:
            self.screenshots=prepare_directory(directory/'screenshots')
            self.storage_warning=f'照片路径暂不可用，已暂存到默认目录：{error}'
        self._apply_verified_repairs()

    def _apply_verified_repairs(self):
        """Local authorized repair handoff, consumed by the new version on open.

        Old running versions ignore this setting, so they cannot undo the repair
        before the user restarts. Requests contain exact row IDs/hashes, not a
        general heuristic for equal numbers or nearby timestamps.
        """
        requests = self.setting('verified_match_repairs',[])
        remaining = []
        for request in requests:
            try:
                if request.get('reason')!='user_verified_same_match':
                    raise ValueError('缺少同一局的核实依据。')
                rows=[self.capture(cid) for cid in request['ids']]
                if any(r['hash']!=request['hashes'][str(r['id'])] for r in rows):
                    raise ValueError('记录已变化，未自动修改。')
                self.merge_verified_match(request['ids'])
            except Exception as error:
                import logging
                logging.exception('Verified duplicate repair not applied')
                self.storage_warning += ' 已核实重复的校正未完成：'+str(error)
                remaining.append(request)
        if requests:
            self.set_setting('verified_match_repairs',remaining)

    def set_image_directory(self,path):
        from .media import prepare_directory
        target=prepare_directory(path)
        roots=set(self.setting('image_roots',[])) | {str(self.screenshots),str(target)}
        with self.db:
            for key,value in [('image_directory',str(target)),('image_roots',sorted(roots))]:
                self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,json.dumps(value)))
        self.screenshots=target
        self.storage_warning=''

    def cleanup_plan(self,kind):
        from .media import cleanup_plan
        roots=set(self.setting('image_roots',[])) | {str(self.screenshots),str((self.directory/'screenshots').absolute())}
        captures=[dict(r) for r in self.db.execute('SELECT * FROM captures')]
        return cleanup_plan(self.directory,roots,captures,kind)

    def finish_cleanup(self,result):
        with self.db:
            self.db.executemany("UPDATE captures SET image='' WHERE image=?",[(path,) for path in result['removed']])
            if result['kind']=='history':
                for table in ('match_stats','reviews','ocr_results','lifecycle_events','stats','captures','matches','session_aliases','session_roster','sessions'):
                    self.db.execute(f'DELETE FROM {table}')
        if result['kind']=='history':
            self.db.execute('VACUUM')

    def setting(self, key, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def automatic_phase(self, session_id):
        phase = self.setting('automatic_phase_'+str(session_id))
        if phase is not None:
            if phase.get('version')==14 and phase.get('cycle',{}).get('match_key') and not phase['cycle'].get('completed'):
                from .automatic import is_summary_result
                for capture in self.captures(session_id):
                    data=json.loads(capture['result'])
                    if data.get('match_key')==phase['cycle']['match_key'] and (data.get('summary_candidate_preserved') or is_summary_result(data)):
                        phase['cycle']['completed']=True
                        break
            return phase
        # Conservative upgrade: the last automatic summary has already been
        # captured. Reopening it after installing an update is not a new game.
        captures = [c for c in self.captures(session_id)
                    if json.loads(c['result']).get('source') == 'automatic']
        if captures:
            last = max(captures,key=lambda c:c['id'])
            return dict(locked=True,epoch=json.loads(last['result']).get('automatic_epoch'))
        return dict(locked=False)

    def merge_verified_match(self, capture_ids):
        """Explicitly verified one-match copies only; never inferred from stats."""
        import uuid
        ids = list(dict.fromkeys(capture_ids))
        rows = [self.capture(cid) for cid in ids]
        if len(rows)<2 or len({r['session_id'] for r in rows})!=1:
            raise ValueError('只能合并同一次记录中已核实属于同一局的截图。')
        if any(r['result'].get('keep_separate') for r in rows):
            raise ValueError('已有明确保留为另一局的记录，请先核实。')
        group = uuid.uuid4().hex
        with self.db:
            for row in rows:
                result = row['result']; result['verified_match_group'] = group
                self.db.execute('UPDATE captures SET result=? WHERE id=?',
                                (json.dumps(result,ensure_ascii=False),row['id']))
        self.consolidate(rows[0]['session_id'])

    def players(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM players ORDER BY id")]

    def save_players(self, players):
        if len(players) != 3 or any(not name.strip() for _, name in players):
            raise ValueError("请填写三个玩家的显示名称。")
        with self.db:
            self.db.executemany("UPDATE players SET name=? WHERE id=?",
                                [(name.strip(), pid) for pid, name in players])
            active = self.active_session()
            if active:
                self.db.executemany("UPDATE session_roster SET name=? WHERE session_id=? AND player_id=?",
                                    [(name.strip(), active['id'], pid) for pid, name in players])

    def aliases(self, player_id):
        return [r[0] for r in self.db.execute("SELECT name FROM aliases WHERE player_id=? ORDER BY name",
                                            (player_id,))]

    def replace_aliases(self, player_id, names):
        with self.db:
            self.db.execute("DELETE FROM aliases WHERE player_id=?", (player_id,))
            for name in names:
                self.bind_alias(name, player_id)

    def bind_alias(self, name, player_id):
        name = normalize_name(name)
        if name:
            self.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?)", (name, player_id))

    def match_player(self, name, session_id=None):
        if session_id is not None:
            local = list(self.db.execute("SELECT player_id FROM session_aliases WHERE session_id=? AND name=?",
                                         (session_id, normalize_name(name))))
            if local:
                return local[0][0] if len(local) == 1 else None
        rows = list(self.db.execute("SELECT player_id FROM aliases WHERE name=?",
                                    (normalize_name(name),)))
        return rows[0][0] if len(rows) == 1 else None

    def active_session(self):
        row = self.db.execute("SELECT * FROM sessions WHERE ended IS NULL").fetchone()
        return dict(row) if row else None

    def sessions(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM sessions ORDER BY id DESC")]

    def session_player_ids(self, session_id):
        row = self.db.execute("SELECT player_ids FROM sessions WHERE id=?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else [1, 2, 3]

    def session_mode(self, session_id):
        row = self.db.execute("SELECT mode FROM sessions WHERE id=?", (session_id,)).fetchone()
        return row[0] if row else self.setting('mode', 3)

    def session_players(self, session_id):
        required = self.session_player_ids(session_id)
        names = {r['player_id']: r['name'] for r in self.db.execute(
            'SELECT player_id,name FROM session_roster WHERE session_id=?', (session_id,))}
        return [{**p, 'name': names.get(p['id'], p['name'])} for p in self.players() if p['id'] in required]

    def start_session(self, player_ids=None, mode=None):
        if self.active_session():
            raise ValueError("已有进行中的统计，请先结束。")
        # Providing a mode starts discovery; explicit IDs preserve legacy/manual sessions.
        player_ids = list(player_ids if player_ids is not None else ([] if mode is not None else [1, 2, 3]))
        mode = mode if mode is not None else len(player_ids)
        if type(mode) is not int or mode not in (1, 2, 3):
            raise ValueError('请选择 1／2／3 人模式。')
        if (player_ids and len(player_ids) != mode) or len(set(player_ids)) != len(player_ids) or not set(player_ids) <= {1, 2, 3}:
            raise ValueError("请选择 1–3 个不同玩家。")
        timestamp = now()
        moment=datetime.now()
        title=(f'{moment.month:02d}月{moment.day:02d}日 '
               f'{moment.hour:02d}:{moment.minute:02d} · {mode}人统计')
        with self.db:
            cursor = self.db.execute("INSERT INTO sessions(title,started,player_ids,mode) VALUES (?,?,?,?)",
                                     (title,
                                      timestamp, json.dumps(sorted(player_ids)), mode))
            for player in self.players():
                if player['id'] in player_ids:
                    self.db.execute('INSERT INTO session_roster VALUES (?,?,?)',
                                    (cursor.lastrowid, player['id'], player['name']))
        return cursor.lastrowid

    def _bind_session(self, session_id, player_id, name, use_configured=True):
        configured = self.players()[player_id-1]['name']
        display = configured if use_configured and configured != f'玩家{player_id}' else name or configured
        self.db.execute('INSERT OR REPLACE INTO session_roster VALUES (?,?,?)', (session_id, player_id, display))
        if normalize_name(name):
            self.db.execute('INSERT OR IGNORE INTO session_aliases VALUES (?,?,?)',
                            (session_id, normalize_name(name), player_id))

    def discovery(self, session_id, resolve=False):
        """Infer new names from complete frames; reuse explicit bindings in partial frames."""
        mode = self.session_mode(session_id)
        if self.session_player_ids(session_id):
            return {'resolved': True, 'mode': mode, 'observations': 0, 'candidates': [],
                    'message': '已确定：' + '、'.join(p['name'] for p in self.session_players(session_id))}
        observations, evidence, names, seen = [], [], {}, set()
        for capture in reversed(self.visible_captures(session_id)):
            if capture['status'] == 'discarded' or capture['duplicate_of'] or capture['hash'] in seen:
                continue
            seen.add(capture['hash'])
            cards = json.loads(capture['result']).get('cards', [])
            if json.loads(capture['result']).get('attribution')=='needs_review':continue
            if not mode <= len(cards) <= 3:
                continue
            identities, frame_names = [], {}
            for card in cards:
                name = normalize_name(card.get('name', ''))
                if not name or card.get('name_score', 0) < 0.85:
                    continue
                aliases = list(self.db.execute('SELECT player_id FROM aliases WHERE name=?', (name,)))
                if len(aliases) > 1:
                    break
                key = f'p:{aliases[0][0]}' if aliases else 'n:' + name
                identities.append(key)
                frame_names[key] = card['name']
            else:
                # Ambiguous aliases and repeated identities invalidate the frame.
                # An unreadable stranger supplies no evidence of absence.
                if len(set(identities)) == len(identities) and len(identities) >= mode:
                    frame = set(identities)
                    evidence.append(frame)
                    if len(identities) == len(cards):
                        observations.append(frame)
                    names.update(frame_names)
        from .identity import infer_roster
        common,enough,partial_count,bound_repeat=infer_roster(mode,observations,evidence)
        candidates = [{'key': key, 'name': names[key]} for key in sorted(common)]
        if resolve and enough and len(common) == mode:
            used = {int(key[2:]) for key in common if key.startswith('p:')}
            free = iter(pid for pid in (1, 2, 3) if pid not in used)
            assigned = [(int(item['key'][2:]) if item['key'].startswith('p:') else next(free), item)
                        for item in candidates]
            with self.db:
                self.db.execute('UPDATE sessions SET player_ids=? WHERE id=?',
                                (json.dumps(sorted(pid for pid, _ in assigned)), session_id))
                for pid, item in assigned:
                    self._bind_session(session_id, pid, item['name'], use_configured=item['key'].startswith('p:'))
            return self.discovery(session_id)
        if partial_count and not bound_repeat:
            message = (f'已保留 {len(evidence)} 局，其中 {partial_count} 局有昵称未读全；'
                       '等待完整昵称对比，或在设置绑定参与玩家的识别昵称。')
        elif not observations:
            message = '等待首局有效昵称；未读全或无法区分的昵称不用于自动推断。'
        elif not enough:
            message = f'首局已保留，等待下一局确定 {mode} 位玩家。'
        elif len(common) > mode:
            message = f'已对比 {len(observations)} 局，仍有 {len(common)} 位共同玩家；继续截图或手动确认。'
        elif len(common) == mode:
            message = f'已找到 {mode} 位可靠共同玩家，正在确定名单。'
        else:
            message = f'共同昵称不足 {mode} 人，请核对昵称或手动确认。'
        return {'resolved': False, 'mode': mode, 'observations': len(observations),
                'partial_observations': partial_count,
                'candidates': candidates, 'message': message}

    def end_session(self, session_id):
        with self.db:
            self.db.execute("UPDATE sessions SET ended=? WHERE id=? AND ended IS NULL",
                            (now(), session_id))

    def add_capture(self, session_id, image, digest, result):
        # Serialize the check and insert across separate Store instances as well.
        self.db.execute('BEGIN IMMEDIATE')
        with self.db:
            duplicate = self.find_duplicate(session_id, digest, result)
            if duplicate:
                return duplicate
            result=json.loads(json.dumps(result))
            mid=self.matches.attach(session_id,result)
            cursor = self.db.execute("INSERT INTO captures(session_id,created,image,hash,result,duplicate_of,match_id) "
                                    "VALUES (?,?,?,?,?,?,?)", (session_id, now(), image, digest,
                                    json.dumps(result, ensure_ascii=False), None,mid))
            self.matches.record_ocr(cursor.lastrowid,result)
        return cursor.lastrowid

    def duplicate_index(self, session_id):
        hashes,groups = {},{}
        for capture in sorted(self.captures(session_id), key=self._duplicate_priority):
            # Do not trust a legacy OCR-based merged_into chain here: its target
            # may have different pixels. Only a complete decoded image hash counts.
            if capture['hash']:
                hashes.setdefault(capture['hash'], capture['id'])
            group = self._match_group(json.loads(capture['result']))
            if group: groups.setdefault(group,capture['id'])
        return hashes, groups

    @staticmethod
    def _match_group(result):
        if result.get('keep_separate'): return None
        if result.get('verified_match_group'): return ('verified',result['verified_match_group'])
        if result.get('source')=='automatic' and result.get('automatic_epoch') and result.get('observation_version')!=14:
            return ('automatic',result['automatic_epoch'])
        if result.get('match_key'): return ('match',result['match_key'])
        if result.get('source')=='automatic' and result.get('automatic_epoch'):
            return ('automatic',result['automatic_epoch'])
        return None

    @staticmethod
    def _duplicate_priority(capture):
        data=json.loads(capture['result'])
        manual=data.get('manual_revision','') if capture['status']=='confirmed' else ''
        quality=sum(c.get('score',0)>=.94 and c.get('name_score',0)>=.85 and
                    all(c.get(k) is not None for k in ('damage','kills','assists'))
                    for c in data.get('cards',[])) if capture['status']=='pending' else 0
        return (0 if manual else 1 if capture['status']=='confirmed' else
                2 if data.get('manual_discarded') else 3 if capture['status']=='pending' else 4,
                -manual if isinstance(manual,int) else 0,-quality,capture['id'])

    def find_duplicate(self, session_id, digest, result=None):
        hashes, groups = self.duplicate_index(session_id)
        cid = hashes.get(digest) if digest else None
        group = self._match_group(result or {})
        # v14 preserves distinct observations of a known match. Consolidation
        # chooses the best evidence only after OCR/confirmation, never before.
        cid = cid or (groups.get(group) if group and result.get('observation_version')!=14 else None)
        return self.canonical_id(cid) if cid else None

    def canonical_id(self,capture_id):
        seen=set()
        while capture_id not in seen:
            seen.add(capture_id)
            row=self.db.execute('SELECT merged_into FROM captures WHERE id=?',(capture_id,)).fetchone()
            if not row or not row[0]:
                return capture_id
            capture_id=row[0]
        raise ValueError('重复记录关系异常。')

    def visible_captures(self,session_id):
        return [c for c in self.captures(session_id) if not c['merged_into']]

    def consolidate(self,session_id):
        """Retain every original; hide proven copies and count only the canonical.

        A manual correction wins over automatic values. Keep-separate records from
        explicit new-match confirmation remain independent. Only exact image
        hashes or an explicitly verified match group merge; OCR text, diagnostic
        footer, survival time and totals never do.
        """
        captures=self.captures(session_id)
        if any(c['match_id'] is None for c in captures):
            from .matches import migrate
            migrate(self.db)
            captures=self.captures(session_id)
        data={c['id']:json.loads(c['result']) for c in captures}
        hashes,groups,changes={},{},{}
        parents = {c['id']:c['id'] for c in captures}
        ordered = sorted(captures,key=self._duplicate_priority)
        priority = {c['id']:i for i,c in enumerate(ordered)}
        def root(cid):
            while parents[cid]!=cid:
                parents[cid]=parents[parents[cid]]; cid=parents[cid]
            return cid
        for capture in ordered:
            cid=capture['id']; result=data[cid]
            if result.get('keep_separate'):
                changes[cid]=None
                continue
            canonical=hashes.get(capture['hash']) if capture['hash'] else None
            group = self._match_group(result)
            related = [r for r in (canonical,groups.get(group) if group else None) if r]
            if related:
                # Earlier items have higher manual/confirmation priority.
                winners = {root(r) for r in related}
                winner = min(winners,key=priority.get)
                parents[cid] = winner
                for other in winners: parents[other] = winner
            if capture['hash']: hashes[capture['hash']]=cid
            if group: groups[group]=cid
        for capture in captures:
            cid=capture['id']; parent=root(cid)
            changes[cid]=parent if parent!=cid else None
        with self.db:
            for capture in captures:
                if capture['merged_into']!=changes[capture['id']]:
                    self.db.execute('UPDATE captures SET merged_into=? WHERE id=?',(changes[capture['id']],capture['id']))
                if capture['duplicate_of']:
                    original=next((c for c in captures if c['id']==capture['duplicate_of']),None)
                    if not original or not capture['hash'] or original['hash']!=capture['hash']:
                        # Old hints must not keep genuinely different screenshots
                        # pending after the OCR-based rules have been removed.
                        self.db.execute('UPDATE captures SET duplicate_of=NULL WHERE id=?',(capture['id'],))
        self.matches.sync(session_id)
        return {cid:parent for cid,parent in changes.items() if parent}

    def update_recognition(self,capture_id,result):
        previous=self.capture(capture_id)['result']
        for key in ('source','manual_revision','keep_separate','manual_discarded','automatic_epoch','verified_match_group',
                    'match_key','attribution','episode_key','observation_version'):
            if key in previous: result[key]=previous[key]
        with self.db:
            self.db.execute('UPDATE captures SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),capture_id))
            self.matches.record_ocr(capture_id,result)

    def restore(self, capture_id):
        with self.db:
            self.db.execute("UPDATE captures SET status='pending' WHERE id=? AND status='discarded'", (capture_id,))

    def capture(self, capture_id):
        row = self.db.execute("SELECT * FROM captures WHERE id=?", (capture_id,)).fetchone()
        result = dict(row)
        result["result"] = json.loads(result["result"])
        result["stats"] = [dict(r) for r in self.db.execute(
            "SELECT * FROM stats WHERE capture_id=? ORDER BY card", (capture_id,))]
        return result

    def captures(self, session_id):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM captures WHERE session_id=? ORDER BY id DESC", (session_id,))]

    def confirm(self, capture_id, rows, remember=False, allow_duplicate=False, manual=False):
        capture = self.capture(capture_id)
        if capture['result'].get('attribution')=='needs_review' and not manual:
            raise ValueError('比赛归属未确认，不能自动计入。')
        required = set(self.session_player_ids(capture["session_id"]))
        unresolved = not required
        if unresolved:
            if not manual:
                raise ValueError('尚未确定参与玩家，请继续识别下一局或手动确认。')
            required = {r['player_id'] for r in rows}
            if len(required) != self.session_mode(capture['session_id']) or not required <= {1, 2, 3}:
                raise ValueError('手动选择的人数须与当前模式一致。')
        if len(rows) != len(required) or {r["player_id"] for r in rows} != required:
            raise ValueError("每位参与统计的玩家须各对应一张卡片，其他卡片可不统计。")
        cards = {r["card"] for r in rows}
        if len(cards) != len(rows) or any(type(i) is not int or not 0 <= i < len(capture["result"]["cards"]) for i in cards):
            raise ValueError("卡片编号无效或重复。")
        for row in rows:
            for field in ("damage", "kills", "assists"):
                if type(row[field]) is not int or row[field] < 0 or row[field] > 999999:
                    raise ValueError("伤害、击杀、助攻须为非负整数（最大999999）。")
        if capture["status"] == "discarded":
            raise ValueError("此截图已忽略。")
        if capture["status"] != "confirmed" and capture["duplicate_of"] and not allow_duplicate:
            raise ValueError("此截图与已有截图相同，请确认它是否属于新的一局。")
        with self.db:
            if unresolved:
                self.db.execute('UPDATE sessions SET player_ids=? WHERE id=?',
                                (json.dumps(sorted(required)), capture['session_id']))
            if unresolved or (manual and remember):
                for row in rows:
                    name = capture['result']['cards'][row['card']].get('name', '')
                    self.db.execute('DELETE FROM session_aliases WHERE session_id=? AND name=?',
                                    (capture['session_id'], normalize_name(name)))
            self.db.execute("DELETE FROM stats WHERE capture_id=?", (capture_id,))
            for row in rows:
                self.db.execute("INSERT INTO stats VALUES (?,?,?,?,?,?)", (capture_id, row["player_id"],
                                row["card"], row["damage"], row["kills"], row["assists"]))
                if remember:
                    name = capture["result"].get("cards", [{}] * 3)[row["card"]].get("name", "")
                    self.bind_alias(name, row["player_id"])
                if unresolved or (manual and remember):
                    card = capture['result']['cards'][row['card']]
                    self._bind_session(capture['session_id'], row['player_id'], card.get('name', ''))
            self.db.execute("UPDATE captures SET status='confirmed' WHERE id=?", (capture_id,))
            if manual:
                import time
                result=capture['result']
                result['manual_revision']=time.time_ns()
                self.db.execute('UPDATE captures SET result=?,merged_into=NULL WHERE id=?',
                                (json.dumps(result,ensure_ascii=False),capture_id))
                self.db.execute('INSERT INTO reviews(capture_id,created,action,payload) VALUES (?,?,?,?)',
                                (capture_id,now(),'manual_stats',json.dumps(rows)))
        self.consolidate(capture['session_id'])

    def assign_match(self,capture_id,match_id=None):
        capture=self.capture(capture_id)
        if match_id is not None:
            target=self.db.execute('SELECT * FROM matches WHERE id=? AND session_id=?',
                                   (match_id,capture['session_id'])).fetchone()
            if not target:raise ValueError('请选择同一次开黑统计中的比赛。')
            key=target['match_key']
        else:key='review:'+__import__('uuid').uuid4().hex
        result=capture['result'];result['match_key']=key;result['attribution']='reviewed'
        # Explicit attribution overrides old episode hints for this observation.
        result.pop('keep_separate',None);result.pop('automatic_epoch',None);result.pop('verified_match_group',None)
        with self.db:
            mid=self.matches.attach(capture['session_id'],result)
            self.db.execute('UPDATE captures SET match_id=?,result=?,merged_into=NULL WHERE id=?',
                            (mid,json.dumps(result,ensure_ascii=False),capture_id))
            self.db.execute('INSERT INTO reviews(capture_id,created,action,payload) VALUES (?,?,?,?)',
                            (capture_id,now(),'assign_match',json.dumps(dict(match_id=mid))))
        self.consolidate(capture['session_id'])

    def discard(self, capture_id):
        capture_id=self.canonical_id(capture_id)
        with self.db:
            self.db.execute("UPDATE captures SET status='discarded' WHERE id=? OR merged_into=?", (capture_id,capture_id))
            result=self.capture(capture_id)['result']; result['manual_discarded']=True
            self.db.execute('UPDATE captures SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),capture_id))

    def totals(self, session_id):
        self.matches.sync(session_id)
        count = self.db.execute("SELECT count(*) FROM matches WHERE session_id=? AND state='confirmed'",
                                (session_id,)).fetchone()[0]
        result = []
        for player in self.session_players(session_id):
            row = self.db.execute("SELECT COALESCE(sum(s.damage),0),COALESCE(sum(s.kills),0),"
                                  "COALESCE(sum(s.assists),0) FROM match_stats s JOIN matches c ON c.id=s.match_id "
                                  "WHERE c.session_id=? AND c.state='confirmed' AND s.player_id=?",
                                  (session_id, player["id"])).fetchone()
            result.append({**player, "damage": row[0], "kills": row[1], "assists": row[2],
                           "average_damage": row[0] / count if count else None})
        return count, result

    def close(self):
        self.db.close()
