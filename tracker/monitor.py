"""Sampling orchestration: never discard summaries because a match is closed.

    Domain cycle attribution is separate from the bounded summary episode.
    On-disk spool precedes phase persistence and background OCR dispatch.
"""
import logging
import queue
import threading
import time
import uuid
from .domain import MatchCycle, POLICY
from .capture import WindowsFrameSource
from .automatic import SummaryDetector
from .dedup import image_digest
from .windows import physical_pixels, apex_foreground, window_display, apex_capture_bbox, display_label
from PIL import ImageGrab


class SummaryWatcher:
    def __init__(self,emit,bbox,detector=None,foreground=None,capture=None,interval=None,
                 resolve_display=None,initial_phase=None,spool=None,session_id=None):
        self.emit,self.bbox=emit,bbox
        self.detector=detector or SummaryDetector()
        self.interval=interval or POLICY.sample_seconds
        self.source=WindowsFrameSource(bbox)
        self.foreground=foreground
        self.capture=capture
        self.resolve_display=resolve_display
        self.follow_window=bbox is None or resolve_display is not None
        self.target=None
        self.spool,self.session_id=spool,session_id
        phase=initial_phase or {}
        self.cycle=MatchCycle.restore(phase.get('cycle'))
        if phase.get('version')!=14 and phase.get('locked'):
            self.cycle.completed=True
        self.episode_key=phase.get('episode_key')
        self.episode_context=phase.get('episode_context')
        self.episode_hashes=set(phase.get('episode_hashes',[]))
        self.episode_count=len(self.episode_hashes)
        self.recent_hashes=list(phase.get('recent_hashes',[]))
        self.episode_last=-100.
        self.first=self.best=None
        self.score=-1.;self.matches=0;self.misses=0
        self.available=threading.Event();self.available.set()
        self.stop_event=threading.Event()
        self.thread=threading.Thread(target=self._run,name='SummarySampler',daemon=True)
        self.probe_pending=None;self.probe_generation=0;self.probe_last=-100.
        self.probe_results=queue.Queue()
        self.state=None
        self.cycle_lock=threading.RLock()

    def preserve_manual_summary(self,context):
        """The UI receives this only after a template-positive PNG is durable."""
        with self.cycle_lock:
            if context.get('match_key')!=self.cycle.match_key:return
            self.cycle.summary_preserved(context)
            self.probe_generation+=1;self.probe_pending=None

    @property
    def locked(self):return self.cycle.completed

    @property
    def epoch(self):return self.cycle.match_key

    def phase(self):
        with self.cycle_lock:
            return dict(version=14,cycle=self.cycle.snapshot(),episode_key=self.episode_key,
                        episode_context=self.episode_context,episode_hashes=list(self.episode_hashes),
                        recent_hashes=self.recent_hashes)

    def start(self):self.thread.start()

    def _status(self,text):
        if self.target:text+=' · '+display_label(self.target)
        if text!=self.state:self.state=text;self.emit('state',text)

    def submit_gameplay(self,token,observation):self.probe_results.put((token,observation))

    def reject_candidate(self,epoch):
        # Rejection never invents a new match. Retry the same preserved episode.
        self.episode_count=max(0,self.episode_count-1)

    def _gameplay_results(self):
        while not self.probe_results.empty():
            token,observation=self.probe_results.get_nowait()
            if token!=self.probe_pending:continue
            self.probe_pending=None
            generation,stamp=token
            if generation!=self.probe_generation or time.monotonic()-stamp>POLICY.probe_expiry_seconds:continue
            if not isinstance(observation,dict):continue
            with self.cycle_lock:
                event=self.cycle.observe_start(observation.get('kind'),observation.get('evidence'))
            if event:
                self.episode_key=None;self.episode_context=None;self.episode_hashes.clear();self.episode_count=0
                self.emit('phase',self.phase())
                self.emit('lifecycle',dict(event,entry_kind=event['kind'],kind='match_started',observation=observation))
                self._status('已确认新局入场，等待总结页；相同数字也可分别记录')

    def _flush(self):
        if self.first is None:return
        image=self.best or self.first
        digest=image_digest(image)
        if digest in self.episode_hashes or digest in self.recent_hashes:
            self.first=self.best=None;return
        context=dict(self.episode_context or self.cycle.summary_context(),
                     episode_key=self.episode_key,source='automatic',observation_version=14)
        if self.spool:
            payload=self.spool.preserve(image,self.session_id,context)
        elif not self.available.is_set():return
        else:payload=dict(image=image,**context)
        # Only after durable preservation can lifecycle advance.
        with self.cycle_lock:self.cycle.summary_preserved(context)
        self.episode_hashes.add(digest);self.episode_count+=1
        self.recent_hashes=(self.recent_hashes+[digest])[-32:]
        self.episode_last=time.monotonic();self.first=self.best=None
        self.available.clear()
        self.emit('phase',self.phase())
        self.emit('frame',payload)

    def _read(self):
        if self.capture is None:
            image=self.source.read();self.target=self.source.target
            if self.target:self.bbox=self.target['bbox']
            return image
        window=self.foreground(None if self.follow_window else self.bbox) if self.foreground else 42
        if not window:return None
        resolver=self.resolve_display or (window_display if self.follow_window else None)
        target=resolver(window) if resolver else None
        if resolver and target is None:return None
        if target:self.target=target;self.bbox=target['bbox']
        image=self.capture()
        if (self.foreground and self.foreground(None if self.follow_window else self.bbox)!=window or
                resolver and resolver(window)!=target):return None
        if image.width*image.height*3>32*1024*1024:raise OSError('画面超过32MB')
        return image

    def step(self):
        with physical_pixels():self._step()

    def _step(self):
        if self.matches>=2:self._flush()
        image=self._read()
        if image is None:
            self._flush();self.probe_generation+=1
            self._status('等待 Apex 位于所选屏幕前台；已保留的截图继续后台处理');return
        self._gameplay_results()
        found=self.detector.detect(image)
        if found:
            self.misses=0
            self.probe_generation+=1
            if self.episode_key is None:
                self.episode_key=uuid.uuid4().hex
                self.episode_context=self.cycle.summary_context()
                self.episode_hashes.clear();self.episode_count=0
            if self.episode_count>=POLICY.max_episode_observations:return
            if self.episode_count and time.monotonic()-self.episode_last<POLICY.summary_retry_seconds:return
            if self.first is None:self.first=image;self.matches=0;self.score=-1.
            confidence=found['confidence']
            if confidence>=self.score-.01:self.best=image;self.score=max(confidence,self.score)
            self.matches+=1
            if self.matches>=2:self._flush()
            self._status('总结证据已保留，后台识别；等待下一局加载或选人')
        else:
            self._flush();self.misses+=1;self.matches=0
            if self.misses>=2 and self.episode_key:
                self.episode_key=None;self.episode_context=None;self.episode_hashes.clear();self.episode_count=0
                self.emit('phase',self.phase())
            now=time.monotonic()
            if self.probe_pending is None and now-self.probe_last>=POLICY.page_probe_seconds:
                self.probe_last=now;self.probe_pending=(self.probe_generation,now)
                self.emit('probe',dict(token=self.probe_pending,image=image))

    def _run(self):
        while not self.stop_event.is_set():
            stamp=time.monotonic()
            try:self.step()
            except Exception:
                logging.exception('Sampler failed; retained evidence will retry')
                self._status('自动采样暂时失败，将重试；可快捷键补录')
            self.stop_event.wait(max(.05,self.interval-(time.monotonic()-stamp)))

    def stop(self):
        self.stop_event.set()
        if self.thread.ident is not None:self.thread.join(timeout=3)
        if not self.thread.is_alive():self._flush()
        return None
