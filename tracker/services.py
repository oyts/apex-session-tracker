"""Application recognition service. UI consumes events, not OCR rules."""
import logging
import time
import uuid
from pathlib import Path
from .ocr import open_image
from .dedup import image_digest
from .automatic import is_summary_result


class RecognitionService:
    def __init__(self,engine,image_directory,data_directory,emit):
        self.engine=engine
        self.image_directory=Path(image_directory)
        self.data_directory=Path(data_directory)
        self.emit=emit

    def process(self,sid,image,context,known_hashes,live=False):
        started=time.perf_counter()
        destination=self.image_directory/(uuid.uuid4().hex+'.png')
        digest=image_digest(image)
        if digest in known_hashes:
            self.emit('duplicate',(sid,known_hashes[digest],'相同图像像素',live));return
        image.save(destination)
        self.emit('saved',dict(elapsed=time.perf_counter()-started,live=live))
        try:
            result=self.engine.recognize(destination)
            if context.get('source')=='automatic' and not is_summary_result(result):
                from .candidates import retain_rejected
                retain_rejected(self.data_directory,destination,result,sid,context.get('match_key'))
                self.emit('auto_rejected',(sid,context.get('match_key')));return
        except Exception as error:
            logging.exception('Evidence OCR failed; original preserved')
            result=dict(cards=[dict(card=i,name='',name_score=0,damage=None,kills=None,assists=None,score=0)
                               for i in range(3)],warnings=[f'识别失败：{error}；原图已保留，可手动填写。'],elapsed=0)
        result.update(context)
        result['capture_elapsed']=round(time.perf_counter()-started,3)
        self.emit('result',(sid,str(destination),digest,result))
