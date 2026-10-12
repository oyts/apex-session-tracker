"""Passive summary detection. No hooks, game input, video encoding or process access."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image



class SummaryDetector:
    def __init__(self):
        data = json.loads(Path(__file__).with_name('summary_templates.json').read_text('utf-8'))
        self.templates = {}
        self.sources = {}
        self.aspect_templates = {}
        for name, item in data.items():
            source = np.frombuffer(base64.b64decode(item['pixels']), dtype=np.uint8).reshape(item['shape'])
            self.sources[name] = source
            self.templates[name] = [(scale, self._template(source,scale,scale))
                                    for scale in (.75, .85, 1., 1.15, 1.3, 1.5, 1.75)]
        self.banks=[('damage','triple')]
        if 'damage_native16x9' in self.templates and 'triple_native16x9' in self.templates:
            self.banks.append(('damage_native16x9','triple_native16x9'))

    @staticmethod
    def _template(source,xscale,yscale):
        return cv2.GaussianBlur(cv2.resize(source,None,fx=xscale,fy=yscale,
                                         interpolation=cv2.INTER_NEAREST),(3,3),.65)

    @staticmethod
    def _hits(area, template, threshold=.62):
        if area.shape[0] < template.shape[0] or area.shape[1] < template.shape[1]:
            return []
        scores = cv2.matchTemplate(area, template, cv2.TM_CCOEFF_NORMED)
        hits = []
        for _ in range(4):
            _, score, _, (x, y) = cv2.minMaxLoc(scores)
            if score < threshold:
                break
            hits.append((x, y, score))
            scores[max(0,y-template.shape[0]):y+template.shape[0],
                   max(0,x-template.shape[1]):x+template.shape[1]] = -1
        return hits

    def detect(self, image):
        height = round(image.height * 1000 / image.width)
        if height < 400 or height > 1000:
            return None
        small = image.resize((1000, height), Image.Resampling.BILINEAR).convert('L')
        # Tiny text's edge pixels change with native aspect/UI rasterization.
        # Smooth both sides lightly, retaining the same similarity thresholds.
        binary = cv2.GaussianBlur((np.asarray(small) > 110).astype(np.uint8)*255,(3,3),.65)
        # Uniform sizes cover native UI scaling; an aspect variant also tolerates
        # stretched/recorded frames. Cache only the current resolution, bounded.
        if height not in self.aspect_templates:
            self.aspect_templates.clear()
            self.aspect_templates[height] = {name: variants + [
                (scale,self._template(self.sources[name],scale,scale*height/625)) for scale,_ in variants]
                for name,variants in self.templates.items()}
        templates = self.aspect_templates[height]
        header = binary[:round(height*.16)]
        if not any(self._hits(header, template, .65) for _, template in templates['heading']):
            return None
        top = round(height*.25)
        area = binary[top:round(height*.84)]
        best = None
        for damage_name,triple_name in self.banks:
            for (scale, damage), (_, triple) in zip(templates[damage_name], templates[triple_name]):
                ds = self._hits(area, damage)
                ts = self._hits(area, triple)
                pairs = [(x,y,min(score,t_score)) for x,y,score in ds for tx,ty,t_score in ts
                         if abs(tx-x) < 12*scale and 18*scale < y-ty < 56*scale]
                if pairs:
                    confidence = max(pair[2] for pair in pairs)
                    if best is None or confidence > best['confidence']:
                        best = {'confidence': confidence, 'labels': len(pairs)}
        return best


def is_summary_result(result):
    """Full OCR checks the page independently of the cheap candidate detector."""
    lines = result.get('lines', [])
    headers = [line for line in lines if line['text'].strip() == '总结']
    damage = [line for line in lines if '造成伤害' in line['text']]
    triples = [line for line in lines if all(word in line['text'] for word in ('击杀','助攻','击倒'))]
    return bool(headers and damage and triples and
                min(line['cy'] for line in headers) < min(line['cy'] for line in damage) and
                any(abs(d['x']-t['x']) < 40 and 0 < d['cy']-t['cy'] < 180
                    for d in damage for t in triples))


def __getattr__(name):
    if name == "SummaryWatcher":
        from .monitor import SummaryWatcher
        return SummaryWatcher
    raise AttributeError(name)
