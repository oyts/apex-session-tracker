from __future__ import annotations

import re
import time
import logging
from pathlib import Path

from PIL import Image, ImageOps


def open_image(path):
    with Image.open(path) as image:
        if image.width * image.height > 50_000_000:
            raise ValueError("图片过大，请使用单个显示器的截图。")
        return ImageOps.exif_transpose(image).convert("RGB")


def parse_triple(text):
    compact = text.replace(" ", "").replace("／", "/").replace("|", "/")
    match = re.fullmatch(r"(\d{1,3})/(\d{1,3})/(\d{1,3})", compact)
    return tuple(map(int, match.groups())) if match else None


def parse_damage(text):
    value = text.strip().replace(",", "").replace("，", "").replace(" ", "")
    return int(value) if re.fullmatch(r"\d{1,6}", value) else None


class Recognizer:
    def __init__(self):
        from rapidocr import RapidOCR
        self.engine = RapidOCR(params={"Global.text_score": 0.45, "Global.log_level": "error",
                                       "EngineConfig.onnxruntime.intra_op_num_threads": 2,
                                       "EngineConfig.onnxruntime.inter_op_num_threads": 1})

    def lines(self, image):
        import numpy as np
        output = self.engine(np.array(image), use_det=True, use_cls=False)
        if output.boxes is None:
            return []
        lines = []
        for box, text, score in zip(output.boxes, output.txts, output.scores):
            xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
            lines.append({"text": str(text), "score": float(score), "x": min(xs), "y": min(ys),
                          "right": max(xs), "bottom": max(ys),
                          "cx": (min(xs) + max(xs)) / 2, "cy": (min(ys) + max(ys)) / 2})
        return lines

    def hud_lines(self,image):
        # RapidOCR's default "min" policy upscales a narrow HUD crop's short
        # side to 736px, making a tiny strip several megapixels. The single OCR
        # worker permits a temporary policy; always restore full-page settings.
        detector=self.engine.text_det
        previous=detector.limit_type
        try:
            detector.limit_type='max'
            return self.lines(image)
        finally:
            detector.limit_type=previous

    def verify_kills_assists(self, image, line, triple):
        """Re-read just the requested fields; disagreement never raises confidence."""
        import numpy as np
        compact = line['text'].replace(' ', '').replace('／', '/').replace('|', '/')
        prefix = '/'.join(compact.split('/')[:2])
        width = line['right'] - line['x']
        crop = image.crop((max(0, line['x']-4), max(0, line['y']-4),
                           min(image.width, line['x']+width*len(prefix)/len(compact)+4),
                           min(image.height, line['bottom']+4)))
        crop = crop.resize((crop.width*3, crop.height*3))
        gray = ImageOps.autocontrast(crop.convert('L'))
        scores = []
        for variant in (gray, ImageOps.invert(gray)):
            output = self.engine(np.array(variant.convert('RGB')), use_det=False, use_cls=False)
            if not output.txts or not output.scores:
                return None
            text = output.txts[0].replace(' ', '').replace('／', '/')
            match = re.fullmatch(r'(\d{1,3})/(\d{1,3})/?', text)
            if not match or tuple(map(int, match.groups())) != triple[:2] or output.scores[0] < .94:
                return None
            scores.append(float(output.scores[0]))
        return {'method': 'gray-and-inverse-prefix-agreement', 'scores': scores,
                'raw_score': line['score']}

    def agree_number(self, crop):
        """Two high-confidence independent presentations must agree; no substitutions."""
        import numpy as np
        gray = ImageOps.autocontrast(crop.convert('L'))
        gray = ImageOps.expand(gray.resize((gray.width*3,gray.height*3)),border=8,fill=0)
        reads=[]
        for variant in (gray,ImageOps.invert(gray)):
            out=self.engine(np.asarray(variant.convert('RGB')),use_det=False,use_cls=False)
            if not out.txts or not out.scores or out.scores[0] < .94:
                return None
            number=parse_damage(out.txts[0])
            if number is None:
                return None
            reads.append((number,float(out.scores[0])))
        return (reads[0][0],min(r[1] for r in reads)) if reads[0][0]==reads[1][0] else None

    def separated_triple(self, image, label, width):
        """Read fields separated by actual orange slash geometry, never inferred text."""
        import cv2
        import numpy as np
        h=max(12,label['bottom']-label['y'])
        crop=image.crop(tuple(round(v) for v in (max(0,label['x']-8),max(0,label['y']-h*2),
                                                 min(image.width,label['x']+width),label['y']-4)))
        rgb=np.asarray(crop).astype(float)
        mask=((rgb[:,:,0]>130)&(rgb[:,:,1]>45)&(rgb[:,:,1]<210)&(rgb[:,:,2]<120)
              &(rgb[:,:,0]>rgb[:,:,1]*1.1)).astype('uint8')
        _,components,stats,_=cv2.connectedComponentsWithStats(mask)
        slashes=[]
        for idx,(x,y,w,hh,area) in enumerate(stats[1:],1):
            if hh < h*.55 or hh > h*1.5 or w < hh*.18 or w > hh*.6 or area < 15:
                continue
            yy,xx=np.where(components==idx)
            slope,offset=np.polyfit(yy,xx,1)
            residual=float(np.sqrt(np.mean((xx-(slope*yy+offset))**2)))
            if -.65 < slope < -.22 and residual < 2:
                slashes.append((int(x),int(x+w),int(y),int(y+hh)))
        slashes.sort()
        if len(slashes)!=2:
            return None
        top=min(s[2] for s in slashes)-2; bottom=max(s[3] for s in slashes)+2
        ranges=((0,slashes[0][0]),(slashes[0][1],slashes[1][0]),(slashes[1][1],mask.shape[1]))
        numbers=[]; scores=[]
        for left,right in ranges:
            field=mask[max(0,top):min(mask.shape[0],bottom),left:right]
            ys,xs=np.where(field)
            if len(xs)<10:
                return None
            isolated=Image.fromarray(field[ys.min():ys.max()+1,xs.min():xs.max()+1]*255)
            read=self.agree_number(isolated)
            if not read or read[0]>999:
                return None
            numbers.append(read[0]); scores.append(read[1])
        return tuple(numbers),{'method':'orange-slashes-and-isolated-fields','scores':scores}

    def recognize(self, path):
        started = time.perf_counter()
        image = open_image(path)
        # Detection follows visible labels, so cards can move and 16:9/16:10 need no fixed pixel positions.
        if image.width > 2000:
            image = image.resize((2000, round(image.height * 2000 / image.width)))
        lines = self.lines(image)
        damage_labels = sorted([line for line in lines if "伤害" in line["text"]
                                and line["cy"] > image.height * 0.25], key=lambda line: line["x"])
        cards, warnings = [], []
        if not 1 <= len(damage_labels) <= 3:
            warnings.append("未找到有效的总结数据，请检查截图是否是小队总结页。")
            # Preserve partial OCR text for manual input, rather than inventing zero statistics.
        else:
            gaps = [damage_labels[i + 1]["x"] - damage_labels[i]["x"] for i in range(len(damage_labels)-1)]
            width = min(gaps) * 0.48 if gaps else image.width * 0.18
            for i, label in enumerate(damage_labels):
                left = max(0, label["x"] - width * 0.10)
                right = min(image.width, label["x"] + width)
                height = max(12, label["bottom"] - label["y"])
                column = [line for line in lines if left <= line["x"] <= right]
                damages = [line for line in column if line["cy"] > label["bottom"]
                           and line["cy"] < label["bottom"] + height * 2.8
                           and parse_damage(line["text"]) is not None]
                damages.sort(key=lambda line: line["cy"])
                triples = [line for line in column if label["y"] - height * 4.8 < line["cy"] < label["y"]
                           and parse_triple(line["text"]) is not None]
                triples.sort(key=lambda line: -line["cy"])
                triple_line = triples[0] if triples else None
                damage_line = damages[0] if damages else None
                # Name sits above the K/A/K label; bounded by each card's visible data strip.
                triple_y = triple_line["y"] if triple_line else label["y"] - height * 2.5
                name_lines = [line for line in column
                              if triple_y - height * 6 < line["cy"] < triple_y - height * 1.6
                              and not any(word in line["text"] for word in
                                          ("击杀", "助攻", "击倒", "总结", "伤害", "排名", "全军覆没"))]
                name_lines.sort(key=lambda line: -line["cy"])
                name_line = name_lines[0] if name_lines else None
                triple = parse_triple(triple_line["text"]) if triple_line else None
                verification = None
                if triple and .85 <= triple_line['score'] < .94:
                    try:
                        verification = self.verify_kills_assists(image, triple_line, triple)
                    except Exception:
                        logging.exception('Numeric refinement failed; retaining original confidence')
                triple_score = min(verification['scores']) if verification else triple_line['score'] if triple_line else 0
                if not triple or triple_score < .94:
                    try:
                        refined=self.separated_triple(image,label,min(width,220))
                        if refined and (triple is None or refined[0][:2]==triple[:2]):
                            triple,verification=refined
                            triple_score=min(verification['scores'][:2])
                    except Exception:
                        logging.exception('Isolated numeric refinement failed')
                damage=parse_damage(damage_line['text']) if damage_line else None
                damage_score=damage_line['score'] if damage_line else 0
                damage_verification=None
                if damage is None or damage_score < .94:
                    try:
                        box=(label['x']-8,label['bottom']+2,label['x']+min(width,220),label['bottom']+height*1.6)
                        refined=self.agree_number(image.crop(tuple(round(v) for v in box)))
                        if refined and (damage is None or refined[0]==damage):
                            damage,damage_score=refined
                            damage_verification={'method':'gray-and-inverse-number-agreement','score':damage_score}
                    except Exception:
                        logging.exception('Damage refinement failed')
                card = {"card": i, "name": name_line["text"] if name_line else "",
                        "name_score": name_line["score"] if name_line else 0,
                        "damage": damage,
                        "kills": triple[0] if triple else None,
                        "assists": triple[1] if triple else None,
                        "score": min(damage_score,
                                     triple_score)}
                if verification:
                    card['kills_assists_verification'] = verification
                if damage_verification:
                    card['damage_verification']=damage_verification
                if None in (card["damage"], card["kills"], card["assists"]):
                    warnings.append(f"第{i+1}张卡片数据不完整，需手动补充。")
                cards.append(card)
        if not cards:
            cards = [{"card": i, "name": "", "name_score": 0,
                          "damage": None, "kills": None, "assists": None, "score": 0}
                     for i in range(3)]
        result={"cards": cards, "warnings": warnings,
                "elapsed": round(time.perf_counter() - started, 3), "lines": lines,'recognition_version':6}
        return result


def auto_rows(result, store, player_ids=None, session_id=None):
    from .identity import admitted_rows
    return admitted_rows(result,lambda name:store.match_player(name,session_id),
                         player_ids if player_ids is not None else [1,2,3])
