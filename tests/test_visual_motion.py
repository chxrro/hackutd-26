"""Real OpenCV on synthetic pixels; no camera, NVIDIA or invented metric depth."""
import base64
import io
import sys
import unittest
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from visual_motion import MotionPair


def image(x=320,radius=45):
    pixels=Image.new('RGB',(640,480),(210,210,210))
    draw=ImageDraw.Draw(pixels)
    draw.ellipse((x-radius,270-radius,x+radius,270+radius),fill='red',outline='black',width=2)
    output=io.BytesIO();pixels.save(output,'JPEG')
    return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()


def box(x=320,radius=45):
    return [(x-radius)/640,(270-radius)/480,(x+radius)/640,(270+radius)/480]


class VisualMotionTests(unittest.TestCase):
    def test_measured_expansion_contraction_and_stationary_are_distinct(self):
        for r0,r1,expected in ((45,75,'approaching'),(75,45,'away'),(45,45,'still')):
            with self.subTest(expected=expected):
                bounds=box(radius=r0)
                result=MotionPair(image(radius=r0),image(radius=r1),1,[bounds]).object(bounds)
                self.assertEqual(result['relative_motion'],expected)
                self.assertEqual(result['motion_status'],'measured')
                self.assertGreaterEqual(result['flow_points'],6)

    def test_lateral_motion_and_camera_motion_have_separate_measurements(self):
        bounds=box(x=240)
        pair=MotionPair(image(x=240),image(x=280),1,[bounds])
        measured=pair.object(bounds)
        self.assertEqual(measured['relative_motion'],'right')
        self.assertGreater(measured['horizontal_velocity'],0)
        self.assertEqual(pair.camera_motion,'unknown') # Plain background cannot establish a stationary camera.
        noise=np.random.default_rng(11).integers(0,256,(240,320),dtype=np.uint8)
        shifted=cv2.warpAffine(noise,np.float32([[1,0,8],[0,1,0]]),(320,240))
        def data(pixels):
            ok,encoded=cv2.imencode('.jpg',pixels)
            self.assertTrue(ok)
            return 'data:image/jpeg;base64,'+base64.b64encode(encoded).decode()
        background=MotionPair(data(noise),data(shifted),1,[])
        self.assertEqual(background.camera_motion,'moving')

    def test_missing_texture_long_gap_and_fast_motion_are_unknown(self):
        pixels=Image.new('RGB',(640,480),'gray')
        output=io.BytesIO();pixels.save(output,'JPEG')
        blank='data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()
        for pair in (MotionPair(blank,blank,1,[]),MotionPair(image(),image(),10,[])):
            self.assertEqual(pair.object(box())['relative_motion'],'unknown')
        result=MotionPair(image(x=100),image(x=340),1,[box(x=100)]).object(box(x=100))
        self.assertEqual(result['relative_motion'],'unknown')


if __name__=='__main__':
    unittest.main()
