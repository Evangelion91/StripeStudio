import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import app
import batch
import json
import threading
import urllib.request
import urllib.error


class BatchTests(unittest.TestCase):
    def test_api_routes_and_token(self):
        server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        url=f'http://127.0.0.1:{server.server_address[1]}'
        state={'id':'test','status':'running','completed':0,'total':1,'files':[],'error':None,
               'cancel':threading.Event(),'current':None}
        try:
            with patch.object(app,'BATCHES',{'test':state}),patch.object(batch,'start_batch',return_value=state) as start:
                req=urllib.request.Request(url+'/api/batches',data=b'{}',headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as err:
                    urllib.request.urlopen(req)
                self.assertEqual(err.exception.code,403)
                start.assert_not_called()
                req.add_header('X-Template-Token',app.TOKEN)
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(json.load(response)['id'],'test')
                with urllib.request.urlopen(url+'/api/batches/test') as response:
                    self.assertEqual(json.load(response)['total'],1)
                req=urllib.request.Request(url+'/api/batches/test/cancel',data=b'{}',headers={'X-Template-Token':app.TOKEN})
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(response.status,200)
                self.assertTrue(state['cancel'].is_set())
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_heic_photo(self):
        import pillow_heif
        pillow_heif.register_heif_opener()
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,'DATA',Path(tmp)/'data'):
            path=Path(tmp)/'photo.heic'
            Image.new('RGB',(128,256),(220,40,20)).save(path)
            record=app.prepare_media(path,'photo')
            self.assertEqual((record['width'],record['height']),(128,256))

    def test_strict_frame_boundaries(self):
        for fps in (24,25,30,60):
            plan=batch.split_plan(34*fps,fps,10)
            self.assertEqual(sum(n for _,n in plan),34*fps)
            self.assertTrue(all(0<n/fps<10 for _,n in plan))
            self.assertEqual([o for o,_ in plan],[sum(n for _,n in plan[:i]) for i in range(len(plan))])
        with self.assertRaises(ValueError):
            batch.split_plan(30,30,.01)

    def test_cartesian_split_metadata_and_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            photos=root/'photos'; photos.mkdir()
            videos=root/'videos'; videos.mkdir()
            for name in ('b.png','a.png'):
                Image.new('RGB',(128,128),(220,40,20)).save(photos/name)
            for name,duration in [('b.mp4',1.6),('a.mp4',.5)]:
                result=app.command([app.binary('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=blue:s=128x128:r=30',
                    '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t',str(duration),
                    '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-metadata','title=PRIVATE TEST',
                    '-metadata','location=+55.0+037.0/',str(videos/name)],60)
                self.assertEqual(result.returncode,0)
            with patch.object(app,'DATA',root/'data'),patch.object(app,'BATCHES',{}),patch.object(app,'JOBS',{}),patch.object(app,'MEDIA',{}):
                payload={'photoDir':str(photos),'videoDir':str(videos),'outputDir':str(root/'out'),
                         'template':{'width':128,'height':128,'encoder':'cpu','stripeMode':'thirds'},
                         'split':True,'splitThreshold':1,'maxPartSeconds':.7}
                state=batch.start_batch(app,payload)
                with self.assertRaises(ValueError):
                    batch.start_batch(app,payload)
                deadline=time.monotonic()+60
                while state['status']=='running' and time.monotonic()<deadline:
                    time.sleep(.1)
                self.assertEqual(state['status'],'done',state['error'])
                self.assertEqual(state['completed'],4)
                self.assertEqual(len(state['files']),8)
                for name in state['files']:
                    info=batch.verify_clean(app,root/'out'/name)
                    self.assertTrue(any(s['codec_type']=='audio' for s in info['streams']))
                    if '_part' in name:
                        self.assertLess(float(info['format']['duration']),.7)
                for i in (1,2):
                    frames=0
                    for file in (root/'out').glob(f'{i}_2_part*.mp4'):
                        info=batch.media_info(app,file)
                        frames+=int(next(s for s in info['streams'] if s['codec_type']=='video')['nb_frames'])
                    self.assertEqual(frames,48)
                with self.assertRaises(ValueError):
                    batch.start_batch(app,payload)
                payload.update(outputDir=str(root/'unsplit'),split=False)
                state=batch.start_batch(app,payload)
                while state['status']=='running':
                    time.sleep(.1)
                self.assertEqual(state['status'],'done',state['error'])
                self.assertEqual(set(state['files']),{'1_1.mp4','1_2.mp4','2_1.mp4','2_2.mp4'})


if __name__=='__main__': unittest.main()
