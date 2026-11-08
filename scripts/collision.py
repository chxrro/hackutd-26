"""NVIDIA object vision + measured optical-flow collision candidates.

No metric depth, physical TTC, safety guarantee, or belt commands. All vision
labels and boxes come from Cosmos3-Edge. OpenCV measures frame-to-frame motion.
"""
from __future__ import annotations

import base64
import io
import json
import math
import re
import time
from collections import deque

from perception import ObjectTracker, iou
from visual_motion import MotionPair
from reasoner import request_json

MAX_HISTORY_SECONDS = 5.0
MAX_FRAMES = 4
MAX_OBJECTS = 4
# Prototype image-space settings, not validated stopping/safety thresholds.
CORRIDOR = (.35, .65)
LOOKAHEAD_SECONDS = 2.0
DETECTION_SCHEMA = {
    'type':'array','maxItems':MAX_OBJECTS,
    'items':{'type':'object','required':['label','bbox_2d'],'additionalProperties':False,
        'properties':{'label':{'type':'string','minLength':1,'maxLength':32},
            'bbox_2d':{'type':'array','minItems':4,'maxItems':4,
                'items':{'type':'number','minimum':0,'maximum':1000}}}}
}
PROMPT = (
    'Locate the visible foreground objects. Return only a JSON array of objects with label and '
    'bbox_2d [left,top,right,bottom], coordinates 0 to 1000. '
    'List each object once. Return only objects actually present, at most four. Ignore background floor and ceiling.'
)

def parse_vision(text):
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        # Cosmos grounding also emits ["label", <x1><y1><x2><y2>]. Parse
        # that bounded native format, never repair arbitrary malformed prose.
        native = re.compile(r'\[\s*"([^"\\]+)"\s*,\s*<(\d{1,4})>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*<(\d{1,4})>\s*\]')
        matches = list(native.finditer(text))
        remainder = native.sub('', text)
        if not matches or not re.fullmatch(r'[\s,\[\]]*', remainder):
            raise
        value = [{'label':m.group(1),'bbox_2d':[int(m.group(i)) for i in range(2,6)]} for m in matches]
    if isinstance(value, dict) and value.get('error') == 'limited_visibility':
        return {'quality':'limited','camera_motion':'unknown','objects':[]}
    if isinstance(value, list):
        objects, quality = value, 'ok'
    elif isinstance(value, dict) and 'label' in value:
        objects, quality = [value], 'ok'
    elif isinstance(value, dict) and value.get('q') in {'ok','limited'}:
        objects, quality = value.get('objects'), value['q']
    else:
        raise ValueError('Invalid detection response')
    if not isinstance(objects,list) or len(objects)>MAX_OBJECTS:
        raise ValueError('Invalid object list')
    clean=[]
    for item in objects:
        if not isinstance(item,dict):
            raise ValueError('Invalid object')
        label=item.get('label')
        box=item.get('bbox_2d',item.get('box'))
        if (not isinstance(label,str) or not 1<=len(label.strip())<=32
                or not re.fullmatch(r'[\w -]+',label.strip())
                or not isinstance(box,list) or len(box)!=4
                or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) or not 0<=n<=1000 for n in box)
                or box[2]<=box[0] or box[3]<=box[1]):
            raise ValueError('Invalid object geometry')
        normalized=[float(n)/1000 for n in box]
        clean.append({'label':label.strip().lower(),'box':normalized,
            'relative_motion':'unknown',
            'path':normalized[2]>=CORRIDOR[0] and normalized[0]<=CORRIDOR[1]})
    unique=[]
    for item in clean:
        if not any(item['label']==prior['label'] and iou(item['box'],prior['box'])>.7 for prior in unique):
            unique.append(item)
    return {'quality':quality,'camera_motion':'unknown','objects':unique}


def infer_vision(base_url,model,frames):
    # NVIDIA recognizes/locates objects. Measured optical flow supplies time/motion.
    payload={'model':model,'messages':[{'role':'user','content':[
        {'type':'image_url','image_url':{'url':frames[-1].image}},
        {'type':'text','text':PROMPT}]}],'max_tokens':320,'temperature':0,
        'chat_template_kwargs':{'enable_thinking':False},'structured_outputs':{'json':DETECTION_SCHEMA}}
    started=time.monotonic()
    result=request_json(base_url,'/chat/completions',payload,60)
    parsed,error=None,None
    try:
        choice=result['choices'][0]
        if choice.get('finish_reason')!='stop':
            raise ValueError('Incomplete collision response')
        parsed=parse_vision(choice['message']['content'])
    except (ValueError,TypeError,KeyError,IndexError,AttributeError):
        # AttributeError: vLLM can return content null.
        error='invalid_model_output'
    return {'model':model,'request_latency_ms':round((time.monotonic()-started)*1000),
        'usage':result.get('usage'),'vision':parsed,'vision_error':error,'input_format':'image'}


class CollisionEngine:
    def __init__(self):
        self.history = deque(maxlen=MAX_FRAMES-1)
        self.generation = None
        self.tracker = ObjectTracker(max_gap_seconds=MAX_HISTORY_SECONDS, match_iou=.15)
        self.positions = {}
        self.previous_hazards = set()
        self.image_size = None

    def context(self, frame):
        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(frame.image.split(',', 1)[1]))) as image:
            size = image.size
        if (size != self.image_size or frame.generation != self.generation or (self.history and
                not 0 < frame.captured_at-self.history[-1].captured_at <= MAX_HISTORY_SECONDS)):
            self.history.clear()
            self.tracker = ObjectTracker(max_gap_seconds=MAX_HISTORY_SECONDS, match_iou=.15)
            self.positions.clear()
            self.previous_hazards.clear()
        self.generation = frame.generation
        self.image_size = size
        previous = [f for f in self.history if frame.captured_at-f.captured_at <= MAX_HISTORY_SECONDS]
        return [*previous, frame]

    def analyze(self, base_url, model, frame):
        frames = self.context(frame)
        response = infer_vision(base_url, model, frames)
        motion, camera_motion = {}, 'unknown'
        if len(frames) > 1:
            previous=frames[-2]
            pair=MotionPair(previous.image,frame.image,frame.captured_at-previous.captured_at,
                            [t['box'] for t in self.tracker.tracks.values()])
            camera_motion=pair.camera_motion
            for key,track in self.tracker.tracks.items():
                if track['captured_at']==previous.captured_at:
                    estimate=pair.object(track['box'])
                    motion[key]=estimate
                    if estimate['motion_status']=='measured':
                        # Associate against the measured projected box, not only the old position.
                        track['box']=estimate['predicted_box']
        if response['vision'] is not None:
            from visual_motion import gray_frame
            current_gray=gray_frame(frame.image)
            # A nearly uniform/dark/washed-out frame cannot support a clear-view inference.
            if current_gray.std()<3 or current_gray.mean()<12:
                response['vision']['quality']='limited'
        assessment = self.assess(response['vision'],frame,len(frames),motion,camera_motion)
        assessment.update({'schema_version': 1, 'validated': False,
            'input_format': response['input_format'],
            'temporal_frames': [{'frame_id': f.frame_id, 'captured_at': f.captured_at} for f in frames],
            'range_m': None, 'ttc_seconds': None})
        self.history.append(frame)
        result = {key: response[key] for key in ('model', 'request_latency_ms', 'usage')}
        result.update({'collision': assessment, 'observation': summary(assessment)})
        return result

    def assess(self, vision, frame, count, measured_motion=None, camera_estimate=None):
        if vision is None:
            return {'state': 'unknown', 'reason': 'invalid_model_output', 'camera_motion': 'unknown',
                    'objects': [], 'missing_tracks': []}
        observations = [{**obj, 'relative_motion': obj['relative_motion'] if count > 1 else 'unknown'}
                        for obj in vision['objects']]
        tracking = self.tracker.update(observations, frame.captured_at, 1)
        objects, hazards = [], set()
        camera_motion = (camera_estimate if camera_estimate is not None else vision['camera_motion']) if count>1 else 'unknown'
        for item in tracking['objects']:
            box, key = item['box'], item['track_id']
            measurement={} if measured_motion is None else measured_motion.get(key,{})
            if measured_motion is not None:
                item['relative_motion']=measurement.get('relative_motion','unknown')
            positions = self.positions.setdefault(key, deque(maxlen=3))
            positions.append((frame.captured_at, box))
            center = (box[0]+box[2])/2
            overlap = box[2] >= CORRIDOR[0] and box[0] <= CORRIDOR[1]
            entering = False
            if len(positions) >= 2:
                (t0, b0), (t1, b1) = positions[-2], positions[-1]
                vx = ((b1[0]+b1[2])-(b0[0]+b0[2])) / (2*(t1-t0))
                # Swept image interval, not metric trajectory or collision time.
                projected = center + vx*LOOKAHEAD_SECONDS
                entering = min(center, projected) <= CORRIDOR[1] and max(center, projected) >= CORRIDOR[0]
            motion = item['relative_motion']
            reason = None
            if vision['quality'] == 'ok':
                if item['path'] and overlap and motion == 'approaching':
                    reason = 'approaching_path'
                elif motion in {'left', 'right'} and (overlap or entering):
                    reason = 'crossing_path'

            state = 'possible' if reason else 'not_observed'
            if count < 2 or vision['quality'] != 'ok' or motion == 'unknown':
                if state != 'possible':
                    state = 'unknown'
            independent = ('unknown' if camera_motion != 'still' or motion == 'unknown'
                           else 'stationary' if motion == 'still' else 'moving')
            objects.append({'track_id': key, 'label': item['label'], 'box': box,
                'direction': item['direction'], 'relative_motion': motion,
                'independent_motion': independent, 'state': state,
                'reason': reason or ('uncertain_motion' if state == 'unknown' else 'no_candidate'),
                'range_m':None,'ttc_seconds':None,
                **{field:measurement[field] for field in ('motion_source','motion_status','scale_ratio',
                   'horizontal_velocity','flow_points','flow_inlier_fraction') if field in measurement}})
            if state == 'possible':
                hazards.add(key)
        live_keys = set(self.tracker.tracks)
        self.positions = {key: samples for key, samples in self.positions.items() if key in live_keys}
        lost_hazard = any(item['track_id'] in self.previous_hazards for item in tracking['missing_tracks'])
        # Keep a lost hazard uncertain until its timestamp expires, including multiple missing frames.
        missing_keys = {item['track_id'] for item in tracking['missing_tracks']}
        self.previous_hazards = hazards | (self.previous_hazards & missing_keys)
        if hazards:
            state, reason = 'possible', next(obj['reason'] for obj in objects if obj['state'] == 'possible')
        elif vision['quality'] != 'ok':
            state, reason = 'unknown', 'limited_visibility'
        elif lost_hazard:
            state, reason = 'unknown', 'lost_track'
        elif count < 2:
            state, reason = 'unknown', 'insufficient_history'
        elif any(obj['state'] == 'unknown' for obj in objects):
            state, reason = 'unknown', 'uncertain_motion'
        else:
            state, reason = 'not_observed', 'no_candidate'
        return {'state': state, 'reason': reason, 'camera_motion': camera_motion,
                'objects': objects, 'missing_tracks': tracking['missing_tracks']}


def summary(assessment):
    if assessment['state'] == 'possible':
        candidates = [obj for obj in assessment['objects'] if obj['state'] == 'possible'][:2]
        motions = {'approaching_path':'approaching','crossing_path':'crossing'}
        details = '; '.join(f'{obj["label"]} {obj["direction"]}, {motions[obj["reason"]]}' for obj in candidates)
        return 'POSSIBLE COLLISION — ' + details + '.'
    if assessment['state'] == 'not_observed':
        return 'NO APPARENT COLLISION DETECTED.'
    reasons = {'invalid_model_output': 'vision response unavailable',
        'limited_visibility': 'limited visibility', 'insufficient_history': 'collecting motion history',
        'lost_track': 'previous hazard is no longer visible', 'uncertain_motion': 'motion is uncertain',
        'stale_frame': 'frame is stale'}
    return 'UNKNOWN — ' + reasons.get(assessment['reason'], 'insufficient evidence') + '.'
