"""Bounded speech adapters and short-lived playback grants. No audio/transcript files."""
import base64
import binascii
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode
from collections import OrderedDict
import engine
from providers import ModelError, NoRedirect

MAX_AUDIO_BYTES = 4 * 1024 * 1024
MAX_AUDIO_JSON = 6 * 1024 * 1024
TRANSCRIPTION_URL = 'https://api.deepgram.com/v1/listen'
PLAYBACK = OrderedDict()
PLAYBACK_LOCK = threading.Lock()
PLAYBACK_TTL = 600
PLAYBACK_LIMIT = 40
MIME_EXT = {'audio/webm':'webm', 'audio/mp4':'mp4', 'audio/wav':'wav', 'audio/mpeg':'mp3'}


def status():
    return {'stt': {'provider':'Deepgram', 'model':os.environ.get('DEEPGRAM_MODEL','nova-3'),
                    'configured':bool(os.environ.get('DEEPGRAM_API_KEY','').strip())},
            'tts': {'provider':'ElevenLabs', 'model':os.environ.get('ELEVENLABS_MODEL','eleven_multilingual_v2'),
                    'configured':bool(os.environ.get('ELEVENLABS_API_KEY','').strip() and os.environ.get('ELEVENLABS_VOICE_ID','').strip())},
            'raw_audio_transfer':'Only Send recording uploads raw audio to Deepgram. Audio is not stored by this app. Provider retention applies.',
            'tts_transfer':'Only the server-approved reply is sent to ElevenLabs. Browser voice uses the browser/OS speech service.'}


def _model_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,120}',value):
        raise ModelError('Invalid speech model setting.')
    return value


def _exchange(req, limit, provider):
    try:
        timeout = min(40,max(1,float(os.environ.get('SPEECH_TIMEOUT','30'))))
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=timeout) as response:
            data=response.read(limit+1)
            mime=response.headers.get('Content-Type','').split(';')[0]
        if len(data)>limit:raise ModelError(provider+' response exceeds the size limit.')
        return data,mime
    except urllib.error.HTTPError as exc:
        if exc.code in (401,403):message='authentication or voice/model access denied'
        elif exc.code==429:message='quota or rate limit reached'
        elif exc.code in (400,404,422):message='audio, voice or model configuration rejected'
        else:message='service error'
        raise ModelError(provider+' '+message+'. No silent fallback.') from None
    except ModelError:raise
    except Exception:raise ModelError(provider+' connection failed or timed out. No automatic retry.') from None


def decode_recording(encoded,mime):
    if not isinstance(encoded,str) or len(encoded)>((MAX_AUDIO_BYTES+2)//3)*4 or mime not in MIME_EXT:
        raise ValueError('Unsupported or oversized recording. Use WebM, MP4, WAV or MP3; maximum 4 MB.')
    try:audio=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise ValueError('Invalid recording encoding.') from None
    if not 32<=len(audio)<=MAX_AUDIO_BYTES:raise ValueError('Recording is empty or exceeds 4 MB.')
    signatures={'audio/webm':audio.startswith(b'\x1a\x45\xdf\xa3'),
                'audio/mp4':audio[4:8]==b'ftyp',
                'audio/wav':audio.startswith(b'RIFF') and audio[8:12]==b'WAVE',
                'audio/mpeg':audio.startswith(b'ID3') or (audio[0]==255 and audio[1]&224==224)}
    if not signatures[mime]:raise ValueError('Recording format does not match its declared media type.')
    return audio


def transcribe(audio,mime):
    key=os.environ.get('DEEPGRAM_API_KEY','').strip()
    if not key:raise ModelError('Deepgram STT needs DEEPGRAM_API_KEY on the server. No recording was sent to Deepgram.')
    model=_model_id(os.environ.get('DEEPGRAM_MODEL','nova-3'))
    query=urlencode({'model':model,'smart_format':'true','language':'en'})
    # Deepgram local-file API takes the original audio bytes, not multipart or a remote URL.
    req=urllib.request.Request(TRANSCRIPTION_URL+'?'+query,data=audio,headers={
        'Authorization':'Token '+key,'Content-Type':mime,'Accept':'application/json'})
    raw,_=_exchange(req,262144,'Deepgram STT')
    try:
        channels=json.loads(raw)['results']['channels']
        if not isinstance(channels,list) or len(channels)!=1:raise ValueError()
        text=channels[0]['alternatives'][0]['transcript']
        if not isinstance(text,str) or not 1<=len(text.strip())<=2000:raise ValueError()
    except (ValueError,TypeError,KeyError,IndexError):raise ModelError('Deepgram STT returned empty, invalid or overlong text. Try a shorter recording.') from None
    return text,model


def answer_audio(body):
    if set(body)-{'audio','mime','mode','previous','fixture'}:raise ValueError('Unknown voice request field.')
    mode=body.get('mode','evidence');previous=body.get('previous');fixture=body.get('fixture','none')
    # Validate all options before any paid external request.
    if mode not in ('evidence','openai') or previous not in (None,*engine.TOPICS) or fixture not in engine.FIXTURES:
        raise ValueError('Invalid voice options.')
    audio=decode_recording(body.get('audio'),body.get('mime'))
    start=time.monotonic()
    transcript,model=transcribe(audio,body['mime'])
    stt_ms=round((time.monotonic()-start)*1000,1)
    result=engine.answer(transcript,mode=mode,previous=previous,fixture=fixture)
    # The transcript remains in this function's memory only and is never returned or logged.
    result['voice']={'input':'recording','stt_provider':'Deepgram','stt_model':model,'stt_ms':stt_ms,
                     'answer_ms':result['latency_ms'],'total_ms':round((time.monotonic()-start)*1000,1)}
    result['trace'].insert(0,{'stage':'Speech to text','detail':f'Raw recording sent to Deepgram ({model}); transcription took {stt_ms} ms. Transcript passed to the local privacy boundary; no transcript echo or audio file saved.'})
    return result


def _purge(now):
    for key in list(PLAYBACK):
        if now-PLAYBACK[key]['created']>PLAYBACK_TTL:del PLAYBACK[key]


def attach_playback(result):
    # Called only on internal engine results. The browser cannot submit arbitrary speech text.
    text=' '.join(' '.join([c['text']]+c['conditions']) for c in result['claims']) if result['claims'] else result['message']
    if result.get('fixture')!='none':text='Synthetic demonstration fixture. '+text
    if result.get('next_step'):text+=' '+result['next_step']
    if not text or len(text)>8000: return result
    token=secrets.token_urlsafe(24)
    with PLAYBACK_LOCK:
        _purge(time.monotonic())
        while len(PLAYBACK)>=PLAYBACK_LIMIT:PLAYBACK.popitem(last=False)
        PLAYBACK[token]={'created':time.monotonic(),'text':text,'audio':None,
                         'corpus_hash':engine.canonical_hash(engine.load_corpus())}
    result['speech_text']=text
    result['playback_token']=token
    return result


def eleven_audio(token):
    if not isinstance(token,str) or len(token)>100:raise ValueError('Invalid playback token.')
    with PLAYBACK_LOCK:
        _purge(time.monotonic())
        entry=PLAYBACK.get(token)
        if not entry:raise ValueError('This reply has expired. Ask again before requesting speech.')
        if entry['corpus_hash'] != engine.canonical_hash(engine.load_corpus()):
            del PLAYBACK[token]
            raise ValueError('Knowledge changed since this reply. Ask again before playback.')
        if entry['audio'] is not None:return entry['audio'],True
        text=entry['text']
    key=os.environ.get('ELEVENLABS_API_KEY','').strip()
    voice=os.environ.get('ELEVENLABS_VOICE_ID','').strip()
    if not key or not voice:raise ModelError('ElevenLabs needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID on the server. Choose Browser voice to demo without them.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',voice):raise ModelError('Invalid ElevenLabs voice ID setting.')
    model=_model_id(os.environ.get('ELEVENLABS_MODEL','eleven_multilingual_v2'))
    payload={'text':text,'model_id':model}
    req=urllib.request.Request('https://api.elevenlabs.io/v1/text-to-speech/'+voice+'?output_format=mp3_44100_128',
                               data=json.dumps(payload).encode(),headers={'xi-api-key':key,'Content-Type':'application/json','Accept':'audio/mpeg'})
    audio,mime=_exchange(req,4*1024*1024,'ElevenLabs TTS')
    if not audio or mime not in ('audio/mpeg','audio/mp3','application/octet-stream'):
        raise ModelError('ElevenLabs did not return valid audio media.')
    with PLAYBACK_LOCK:
        if token in PLAYBACK:PLAYBACK[token]['audio']=audio
    return audio,False
