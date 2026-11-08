"""Synthetic collision-policy/temporal protocol tests; no cloud or camera."""
import base64
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from PIL import Image
from collision import CollisionEngine, infer_vision, parse_vision, summary
import webcam_reasoner as webcam
from analysis_log import AnalysisLog, analysis_record


def frame(index, *, generation=0, at=None):
    output=io.BytesIO()
    Image.new('RGB',(640,480),'gray').save(output,'JPEG')
    return webcam.Frame('data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode(),
                        float(index) if at is None else at,index,generation)


def obj(label='person', box=None, motion='approaching', path=True):
    return {'label':label,'box':box or [.4,.2,.6,.8], 'relative_motion':motion,'path':path}


def vision(*objects, camera='still', quality='ok'):
    return {'quality':quality,'camera_motion':camera,'objects':list(objects)}


class CollisionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine=CollisionEngine()

    def assess(self, value, index, count=2):
        return self.engine.assess(value,frame(index),count)

    def test_single_frame_cannot_prove_motion(self):
        result=self.assess(vision(obj(box=[.4,.3,.6,.6])),1,count=1)
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(result['objects'][0]['relative_motion'],'unknown')
        self.assertEqual(result['objects'][0]['independent_motion'],'unknown')

    def test_two_objects_keep_identity_and_have_distinct_motion_and_risk(self):
        first=self.assess(vision(obj(),obj('car',[.8,.2,.95,.4],'away',False)),1)
        next_state=self.assess(vision(obj(box=[.38,.15,.62,.85]),obj('car',[.82,.2,.94,.4],'away',False)),2)
        person,car=next_state['objects']
        self.assertEqual(person['track_id'],first['objects'][0]['track_id'])
        self.assertEqual(person['state'],'possible')
        self.assertEqual(car['state'],'not_observed')
        self.assertNotEqual(person['track_id'],car['track_id'])
        self.assertLess(len(summary(next_state)),150)
        self.assertEqual(next_state['state'],'possible')

    def test_crossing_toward_corridor_and_receding_outside_are_different(self):
        self.assess(vision(obj(box=[0,.2,.2,.4],motion='right')),1)
        crossing=self.assess(vision(obj(box=[.1,.2,.3,.4],motion='right')),2)
        self.assertEqual(crossing['state'],'possible')
        self.assertEqual(crossing['reason'],'crossing_path')
        receding=self.assess(vision(obj(box=[.11,.2,.29,.4],motion='away',path=False)),3)
        self.assertEqual(receding['state'],'not_observed')

    def test_stationary_camera_and_object_do_not_imply_approach(self):
        result=self.assess(vision(obj(motion='still')),1)
        self.assertEqual(result['state'],'not_observed')
        self.assertEqual(result['objects'][0]['independent_motion'],'stationary')
        moving_camera=self.assess(vision(obj(),camera='moving'),2)
        self.assertEqual(moving_camera['state'],'possible')
        self.assertEqual(moving_camera['objects'][0]['independent_motion'],'unknown')

    def test_low_visibility_and_lost_hazard_never_become_clear(self):
        self.assess(vision(obj()),1)
        lost=self.assess(vision(),2)
        self.assertEqual(lost['state'],'unknown')
        self.assertEqual(lost['reason'],'lost_track')
        self.assertEqual(self.assess(vision(),3)['reason'],'lost_track')
        limited=self.assess(vision(quality='limited'),4)
        self.assertEqual(limited['state'],'unknown')
        self.assertEqual(limited['reason'],'limited_visibility')
        self.assertEqual(self.assess(vision(),8)['state'],'not_observed')

    def test_pause_generation_and_long_capture_gap_reset_history_and_tracks(self):
        response={'vision':vision(),'input_format':'image','model':'test','request_latency_ms':1,'usage':{}}
        with patch('collision.infer_vision',return_value=response) as inference:
            self.engine.analyze('unused','test',frame(1))
            second=self.engine.analyze('unused','test',frame(2))
            self.assertEqual(len(second['collision']['temporal_frames']),2)
            paused=self.engine.analyze('unused','test',frame(3,generation=1))
            self.assertEqual(len(paused['collision']['temporal_frames']),1)
            self.assertEqual(paused['collision']['state'],'unknown')
            gap=self.engine.analyze('unused','test',frame(20,generation=1))
            self.assertEqual(len(gap['collision']['temporal_frames']),1)

    def test_stale_completion_is_saved_as_unknown_with_candidate_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            session=webcam.Session('/unused/brev',AnalysisLog(Path(folder)/'frames.jsonl'))
            session.model='synthetic'
            session.collision_engine=unittest.mock.Mock()
            session.pending=frame(1,at=time.time())
            def analyze(*args):
                time.sleep(.15)
                return {'model':'synthetic','observation':'POSSIBLE COLLISION — person center, approaching.',
                        'collision':{'state':'possible','reason':'approaching_path','objects':[]}}
            session.collision_engine.analyze.side_effect=analyze
            with patch.object(webcam,'MAX_FRAME_AGE_SECONDS',.1), patch.object(session.condition,'wait',side_effect=session.cancelled.set):
                session.process()
            self.assertEqual(session.result['collision']['state'],'unknown')
            self.assertEqual(session.result['collision']['candidate_state'],'possible')
            self.assertTrue(session.result['observation'].startswith('UNKNOWN'))
            self.assertEqual(json.loads(session.analysis_log.path.read_text())['collision']['state'],'unknown')


class CollisionProtocolTests(unittest.TestCase):
    def response(self,text,finish='stop'):
        return {'choices':[{'finish_reason':finish,'message':{'content':text}}], 'usage':{'total_tokens':100}}

    def test_latest_image_uses_nvidia_and_disables_thinking(self):
        text=json.dumps({'q':'ok','cam':'still','objects':[]})
        with patch('collision.request_json',return_value=self.response(text)) as request:
            result=infer_vision('unused','cosmos3-edge',[frame(1),frame(2)])
        payload=request.call_args.args[2]
        self.assertEqual(payload['model'],'cosmos3-edge')
        self.assertIn('json',payload['structured_outputs'])
        self.assertEqual(payload['messages'][0]['content'][0]['type'],'image_url')
        self.assertEqual(payload['messages'][0]['content'][0]['image_url']['url'],frame(2).image)
        self.assertFalse(payload['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(result['input_format'],'image')
        self.assertIsNotNone(result['vision'])

    def test_native_nvidia_grounding_tokens_are_bounded_and_model_motion_is_ignored(self):
        parsed=parse_vision('["Circle", <400><402><627><729>]')
        self.assertEqual(parsed['objects'][0]['label'],'circle')
        self.assertEqual(parsed['objects'][0]['relative_motion'],'unknown')
        self.assertAlmostEqual(parsed['objects'][0]['box'][0],.4)
        with self.assertRaises(ValueError):
            parse_vision('["Circle", <0><0><1001><800>]')
        with self.assertRaises(ValueError):
            parse_vision('Here is a scene ["Circle", <0><0><800><800>]')

    def test_malformed_or_truncated_response_is_unknown_without_raw_prose(self):
        for response in (self.response('Descriptive scene prose'), self.response('{}',finish='length'), self.response(None)):
            with patch('collision.request_json',return_value=response):
                result=infer_vision('unused','cosmos3-edge',[frame(1)])
            self.assertIsNone(result['vision'])
            self.assertEqual(result['vision_error'],'invalid_model_output')
            self.assertNotIn('prose',json.dumps(result))
        for box in ([0,0,1001,1000],[True,0,1000,1000],[0,0,0,100],[0,0,float('nan'),1000]):
            with self.assertRaises(ValueError):
                parse_vision(json.dumps({'q':'ok','cam':'still','objects':[{'label':'person','box':box,'motion':'still','path':True}]}))

    def test_persistent_collision_allowlist_drops_unrelated_model_metadata(self):
        result={'frame_id':1,'captured_at':1,'received_at':2,'frame_age_seconds':1,'observation':'UNKNOWN',
            'collision':{'state':'unknown','Authorization':'do-not-save','objects':[{'label':'person','request':'do-not-save'}],
                         'temporal_frames':[{'frame_id':1,'captured_at':1,'image':'do-not-save'}]}}
        record=analysis_record('test',result,active_at_completion=True,max_frame_age_seconds=3)
        self.assertNotIn('do-not-save',json.dumps(record))
        self.assertEqual(record['collision']['temporal_frames'],[{'frame_id':1,'captured_at':1}])


class CollisionRecordingIntegrationTests(unittest.TestCase):
    def test_nvidia_boxes_and_actual_optical_flow_are_recorded_for_two_frames(self):
        from session_recording import SessionRecording
        from test_visual_motion import image
        with tempfile.TemporaryDirectory() as folder:
            recording=SessionRecording(Path(folder))
            session=webcam.Session('/unused/brev',recording,CollisionEngine())
            session.model='cosmos3-edge'
            # NVIDIA protocol response is mocked; optical flow and disk recording are real.
            with patch('collision.request_json') as request:
                for index,(radius,bounds) in enumerate(((45,[430,469,570,656]),(75,[383,406,617,719])),1):
                    request.return_value={'choices':[{'finish_reason':'stop','message':{'content':json.dumps([
                        {'label':'circle','bbox_2d':bounds},{'label':'circle','bbox_2d':bounds}])}}]}
                    session.cancelled.clear()
                    # Use a fixed one-second capture interval while staying inside freshness limits.
                    session.pending=webcam.Frame(image(radius=radius),time.time()-2+index,index,0)
                    with patch.object(session.condition,'wait',side_effect=session.cancelled.set),patch.object(session,'stop') as stop:
                        session.process()
                    stop.assert_not_called()
            rows=[json.loads(line) for line in recording.path.read_text().splitlines()]
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[1]['collision']['objects'][0]['relative_motion'],'approaching')
            self.assertEqual(len(rows[1]['collision']['objects']),1) # Duplicate NVIDIA boxes never create fake identities.
            self.assertEqual(rows[1]['collision']['objects'][0]['motion_source'],'optical_flow')
            self.assertTrue(all((recording.directory/r['image']['path']).exists() for r in rows))


if __name__=='__main__':
    unittest.main()
