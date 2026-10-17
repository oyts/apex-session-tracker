"""Read only the visible squad-total glyphs, anchored by a reliable caption."""
import re

import numpy as np
from PIL import Image


def verify_header(image, lines, agree_number):
    damage_y=min((l['cy'] for l in lines if '造成伤害' in l['text']),default=image.height*.4)
    readings=[]
    for label in lines:
        text=re.sub(r'\s+','',label['text']).upper()
        if (text not in ('小队总击杀','SQUADKILLS') or not .94<=label.get('score',0)<=1 or
                not image.width*.6<label.get('x',0)<image.width or
                not 0<label['cy']<min(damage_y,image.height*.25)):
            continue
        h=max(1,label['bottom']-label['y'])
        box=tuple(round(v) for v in (max(0,label['right']-2),max(0,label['cy']-h*1.3),
                                     min(image.width,label['right']+h*4),min(image.height,label['cy']+h*1.3)))
        roi=image.crop(box)
        rgb=np.asarray(roi).astype(float)
        mask=(rgb[:,:,0]>=150)&(rgb[:,:,1]>=95)&(rgb[:,:,2]<=125)&(rgb[:,:,0]>rgb[:,:,2]*1.35)
        ys,xs=np.where(mask)
        if len(xs)<12 or ys.max()-ys.min()+1<h*.75:
            continue
        glyph=Image.fromarray(mask[ys.min():ys.max()+1,xs.min():xs.max()+1].astype('uint8')*255)
        # Preserve the original color/texture in one reading and isolate the
        # visible yellow ink in the other. Each uses gray AND inverse agreement.
        isolated=agree_number(glyph);original=agree_number(roi)
        if not isolated or not original or isolated[0]!=original[0]:
            continue
        readings.append(dict(value=isolated[0],score=min(isolated[1],original[1]),
                             method='caption-anchored-color-and-mask-agreement-v1',box=list(box)))
    if readings and len({r['value'] for r in readings})==1:
        return max(readings,key=lambda r:r['score'])
    return None
