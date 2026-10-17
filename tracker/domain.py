"""Pure domain policies. No Windows, GUI, images or database dependencies."""
from dataclasses import dataclass, field, asdict
import uuid


@dataclass(frozen=True)
class CapturePolicy:
    sample_seconds: float = .5
    page_probe_seconds: float = 2.
    summary_retry_seconds: float = 8.
    max_episode_observations: int = 3
    probe_expiry_seconds: float = 30.
    memory_pending: int = 2
    start_text_score: float = .85
    number_score: float = .94
    identity_score: float = .85
    summary_gap_seconds: float = 12.
    summary_return_seconds: float = 120.
    summary_min_visible_seconds: float = 1.5
    summary_header_stable_seconds: float = 1.
    summary_header_mask_difference: float = .015
    dropship_confirm_seconds: float = 2.


POLICY = CapturePolicy()


@dataclass
class MatchCycle:
    """A start opens a cycle; repeated selection/respawn never opens another.

    A summary is evidence, not a database commit. Missing starts never veto
    preservation: an unattributed observation goes to review instead.
    """
    match_key: str | None = None
    completed: bool = False
    start_kind: str = ''
    generation: int = 0
    summary_pending: bool = False
    game_mode: str = 'unknown'

    def observe_start(self, kind, evidence, game_mode='unknown'):
        if kind not in ('selection', 'loading', 'dropship'):
            return None
        if self.summary_pending:return None  # cheap template candidate is not a validated end
        if self.match_key and not self.completed:
            if self.start_kind!='bootstrap':return None  # reconnect and mid-match changes
            self.start_kind=kind;self.generation+=1
            self.game_mode=game_mode
            return dict(match_key=self.match_key,kind=kind,evidence=evidence,game_mode=self.game_mode)
        self.match_key = uuid.uuid4().hex
        self.completed = False
        self.summary_pending = False
        self.start_kind = kind
        self.game_mode = game_mode
        self.generation += 1
        return dict(match_key=self.match_key, kind=kind, evidence=evidence,game_mode=self.game_mode)

    def summary_context(self):
        if self.match_key is None and not self.completed:
            self.match_key=uuid.uuid4().hex
            self.start_kind='bootstrap'
        if self.match_key and not self.completed:
            return dict(match_key=self.match_key, attribution='bootstrap' if self.start_kind=='bootstrap' else 'start_observed',game_mode=self.game_mode)
        return dict(match_key=None, attribution='start_not_observed')

    def summary_preserved(self, context, provisional=False):
        if context.get('match_key') == self.match_key and self.match_key:
            self.completed = True
            self.summary_pending = provisional

    def snapshot(self):
        return asdict(self)

    @classmethod
    def restore(cls, data):
        return cls(**{k:v for k,v in (data or {}).items() if k in cls.__dataclass_fields__})


def pending_reason(result, roster_resolved, rows_ready, auto_save=True):
    if result.get('attribution') == 'needs_review':
        return '未观察到新局开始：原图已保留，请确认属于新比赛还是已有比赛。'
    if result.get('summary_readiness',{}).get('state')=='incomplete':
        return '总结画面尚未确认加载完整：候选已保留，请打开完整总结或手动核对。'
    from .participation import header_required, squad_kills
    if header_required(result) and squad_kills(result)[0] is None:
        return '尚未读到明确的小队总击杀：等待完整总结，或对照完整原图手动补充。'
    if not roster_resolved:
        return '等待跨局确定参与玩家；原图和识别结果已保存。'
    if not rows_ready:
        return '昵称归属或数字置信度未达标，请查看原图并修正。'
    if not auto_save:
        return '已关闭自动计入，请核对后保存。'
    return ''
