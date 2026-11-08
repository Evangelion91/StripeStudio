"""Synthetic-only render checks: python -m unittest -v test_render."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import app


class RenderTests(unittest.TestCase):
    def test_thirds_order_and_timing(self):
        from mask import save_mask
        with tempfile.TemporaryDirectory() as tmp, patch.object(app,'DATA',Path(tmp)/'data'):
            root=Path(tmp)
            Image.new('RGB',(360,360),(220,20,20)).save(root/'photo.png')
            made=app.command([app.binary('ffmpeg'),'-v','error','-f','lavfi','-i','color=c=black:s=360x360:r=30',
                              '-t','1','-c:v','libx264','-pix_fmt','yuv420p',str(root/'video.mp4')],30)
            self.assertEqual(made.returncode,0)
            template={**app.DEFAULTS,'angle':0,'randomAngle':False,'width':360,'height':360,'stripeMode':'thirds','encoder':'cpu'}
            ys={}
            for g in range(3):
                save_mask(template,360,360,root/f'g{g}.png',g)
                with Image.open(root/f'g{g}.png') as mask:
                    ys[g]=next(y for y in range(10,350) if mask.getpixel((180,y))==255)
            photo=app.prepare_media(root/'photo.png','photo')
            video=app.prepare_media(root/'video.mp4','video',preview_video=False)
            for phase in (0,.2):
                output=root/f'out{phase}.mp4'
                job=app.start_render({'photo':photo['id'],'video':video['id'],
                    'template':{**template,'stripePhase':phase,'duration':.5}},output)
                while job['status']=='running': time.sleep(.05)
                self.assertEqual(job['status'],'done',job.get('error'))
                decoded=app.command([app.binary('ffmpeg'),'-v','error','-i',str(output),'-f','rawvideo','-pix_fmt','rgb24','-'],30)
                self.assertEqual(decoded.returncode,0)
                size=360*360*3
                self.assertEqual(len(decoded.stdout)//size,15)
                for n in range(15):
                    active=(0,2,1)[int((n/30+phase)*6+1e-7)%3]
                    for g,y in ys.items():
                        red=decoded.stdout[n*size+(y*360+180)*3]
                        self.assertEqual(red<120,g==active,(phase,n,g,red,active))

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
