"""Synthetic-only render checks: python -m unittest -v test_render."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import app


class RenderTests(unittest.TestCase):
    def test_encoder_fallback(self):
        with patch.object(app, 'nvenc_available', return_value=False):
            self.assertEqual(app.choose_encoder(), 'cpu')
            with self.assertRaises(ValueError):
                app.choose_encoder('nvenc')
        with patch.object(app, 'nvenc_available', return_value=True):
            self.assertEqual(app.choose_encoder(), 'nvenc')
            self.assertEqual(app.choose_encoder('cpu'), 'cpu')

    def test_alpha_and_photo_framing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            photo=root/'photo.png'
            mask=root/'mask.png'
            image=Image.new('RGB',(256,128),(230,20,10))
            image.paste((10,20,230),(128,0,256,128))
            image.save(photo)
            Image.new('L',(128,128),255).save(mask)
            cfg={'width':128,'height':128,'videoOpacity':.8,
                 'photo':{'zoom':1,'x':0,'y':.5,'mirror':True}}
            target=root/'layer.png'
            app.prepare_static_layer(photo,mask,cfg,target)
            with Image.open(target) as layer:
                self.assertEqual(layer.size,(128,128))
                self.assertEqual(layer.getpixel((64,64))[3],51)
                self.assertGreater(layer.getpixel((64,64))[0],220)
            Image.new('L',(128,128),0).save(mask)
            app.prepare_static_layer(photo,mask,cfg,root/'opaque.png')
            with Image.open(root/'opaque.png') as layer:
                self.assertEqual(layer.getpixel((64,64))[3],255)

    def test_synthetic_cpu_and_auto_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with patch.object(app,'DATA',root/'data'):
                photo=app.prepare_media(Path('assets/demo-photo.png'),'photo')
                video=app.prepare_media(Path('assets/demo-video.mp4'),'video',preview_video=False)
                for mode in ('cpu','auto'):
                    out=root/f'{mode}.mp4'
                    job=app.start_render({'photo':photo['id'],'video':video['id'],
                        'template':{'width':360,'height':640,'duration':1,'encoder':mode}},out)
                    deadline=time.monotonic()+60
                    while job['status']=='running' and time.monotonic()<deadline:
                        time.sleep(.1)
                    if job['status']=='running':
                        app.cancel_job(job)
                    self.assertEqual(job['status'],'done',job.get('error'))
                    self.assertEqual(job['settings']['videoOpacity'],.8)
                    self.assertEqual(job['encoder'],app.choose_encoder(mode))
                    info=app.probe(out)
                    self.assertEqual((info['width'],info['height']),(360,640))
                    self.assertAlmostEqual(info['duration'],1,delta=.1)


if __name__=='__main__':
    unittest.main()
