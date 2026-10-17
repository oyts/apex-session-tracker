"""Cheap frame stability gate; OCR must still explicitly read the squad total."""
import hashlib
import numpy as np
from .domain import POLICY


class SummaryReadiness:
    def __init__(self):
        self.first_seen=None
        self.stable_since=None
        self.mask=None
        self.ready=False
        self.reason='waiting-for-header'

    def observe(self,found,now):
        if self.first_seen is None:self.first_seen=now
        current=found.get('header_mask') if found.get('header_visible') else None
        if current is None:
            self.stable_since=None;self.mask=None;self.ready=False
            self.reason=found.get('header_reason','header-not-visible')
            return False
        same=False
        if self.mask is not None and self.mask.shape==current.shape:
            union=np.count_nonzero(self.mask|current)
            same=bool(union and np.count_nonzero(self.mask^current)/union<=POLICY.summary_header_mask_difference)
        # Keep the stable run's anchor: small edge flicker is tolerated, gradual
        # shape changes cannot accumulate by comparing only adjacent frames.
        if not same:
            self.stable_since=now
            self.mask=current.copy()
        self.ready=bool(now-self.first_seen>=POLICY.summary_min_visible_seconds and
                        now-self.stable_since>=POLICY.summary_header_stable_seconds)
        self.reason='complete' if self.ready else 'waiting-for-stable-number'
        return self.ready

    def evidence(self):
        return dict(state='complete',method='visible-squad-label-and-stable-number-v2',
                    min_visible_seconds=POLICY.summary_min_visible_seconds,
                    min_header_stable_seconds=POLICY.summary_header_stable_seconds,
                    max_mask_difference=POLICY.summary_header_mask_difference,
                    header_mask_digest=hashlib.sha256(self.mask.tobytes()).hexdigest() if self.mask is not None else None)
