"""Speech tests use synthetic audio and mocked providers; no live key required."""
import base64
import io
import json
import os
import time
import unittest
import urllib.error
from unittest.mock import patch
import engine
import speech
from providers import ModelError

# Pin the date inside the reviewed sources' review window, so results don't depend on the day the tests run.
TODAY = patch('engine.today', return_value='2026-10-27')
setUpModule, tearDownModule = TODAY.start, TODAY.stop

AUDIO = b'\x1a\x45\xdf\xa3'+b'\0'*100
ENCODED = base64.b64encode(AUDIO).decode()

class SpeechTests(unittest.TestCase):
    def setUp(self):
        speech.PLAYBACK.clear()
    def test_audio_bounds_and_mime_validation(self):
        self.assertEqual(speech.decode_recording(ENCODED,'audio/webm'),AUDIO)
        for value,mime in [('%%%','audio/webm'),(ENCODED,'audio/wav'),('', 'audio/webm'),(ENCODED,'text/html'),('A'*(speech.MAX_AUDIO_JSON+1),'audio/webm')]:
            with self.assertRaises(ValueError):speech.decode_recording(value,mime)
    def test_missing_stt_key_never_uploads(self):
        with patch.dict(os.environ,{'DEEPGRAM_API_KEY':'','OPENAI_API_KEY':'separate-openai-key'}),patch('speech._exchange') as exchange:
            with self.assertRaises(ModelError):speech.transcribe(AUDIO,'audio/webm')
            exchange.assert_not_called()
    def test_deepgram_binary_request_and_nested_transcript(self):
        captured=[]
        def exchange(req,limit,provider):
            captured.append(req);return json.dumps({'results':{'channels':[{'alternatives':[{'transcript':'How do I lock my card?'}]}]}}).encode(),'application/json'
        with patch.dict(os.environ,{'DEEPGRAM_API_KEY':'synthetic-key'}),patch('speech._exchange',side_effect=exchange):
            text,model=speech.transcribe(AUDIO,'audio/webm')
        req=captured[0];self.assertEqual(req.full_url,speech.TRANSCRIPTION_URL+'?model=nova-3&smart_format=true&language=en')
        self.assertEqual(req.data,AUDIO);self.assertEqual(req.get_header('Content-type'),'audio/webm')
        self.assertEqual(req.get_header('Authorization'),'Token synthetic-key');self.assertNotIn(b'synthetic-key',req.data)
        self.assertEqual(model,'nova-3')
        self.assertEqual(text,'How do I lock my card?')
    def test_voice_turn_does_not_return_raw_transcript(self):
        transcript='How do I lock my card? demo@example.test OTP 987654 password VoiceSecret_42'
        with patch('speech.transcribe',return_value=(transcript,'mock-stt')):
            result=speech.answer_audio({'audio':ENCODED,'mime':'audio/webm'})
        self.assertEqual(result['status'],'answered');self.assertTrue(result['redacted'])
        self.assertEqual(result['voice']['stt_model'],'mock-stt')
        self.assertEqual(result['voice']['stt_provider'],'Deepgram')
        output=json.dumps(speech.attach_playback(result))
        for secret in ['demo@example.test','987654','VoiceSecret_42',transcript]:self.assertNotIn(secret,output)
    def test_bad_options_rejected_before_transcription(self):
        with patch('speech.transcribe') as transcribe:
            for body in [{'audio':ENCODED,'mime':'audio/webm','mode':'admin'}, {'audio':ENCODED,'mime':'audio/webm','previous':'private'}, {'audio':ENCODED,'mime':'audio/webm','text':'speak this'}]:
                with self.assertRaises(ValueError):speech.answer_audio(body)
            transcribe.assert_not_called()
    def test_malformed_transcription_fails(self):
        with patch.dict(os.environ,{'DEEPGRAM_API_KEY':'synthetic-key'}):
            for raw in [b'{}',b'not-json',b'{"results":{"channels":[]}}']+[json.dumps({'results':{'channels':[{'alternatives':[{'transcript':value}]}]}}).encode() for value in ['',None,'x'*2001]]:
                with patch('speech._exchange',return_value=(raw,'application/json')):
                    with self.assertRaises(ModelError):speech.transcribe(AUDIO,'audio/webm')
    def test_eleven_only_receives_server_checked_reply_and_caches(self):
        result=speech.attach_playback(engine.answer('How do I lock my card?'))
        captured=[]
        def exchange(req,limit,provider):captured.append(req);return b'ID3'+b'x'*100,'audio/mpeg'
        with patch.dict(os.environ,{'ELEVENLABS_API_KEY':'synthetic-eleven-key','ELEVENLABS_VOICE_ID':'testVoice123'}),patch('speech._exchange',side_effect=exchange):
            first,cached=speech.eleven_audio(result['playback_token']);self.assertFalse(cached)
            second,cached=speech.eleven_audio(result['playback_token']);self.assertTrue(cached)
        self.assertEqual(first,second);self.assertEqual(len(captured),1)
        req=captured[0];body=json.loads(req.data)
        self.assertEqual(body['text'],result['speech_text']);self.assertIn('15 days',body['text'])
        self.assertIn('may be declined',body['text']);self.assertNotIn(b'synthetic-eleven-key',req.data)
        self.assertTrue(req.full_url.startswith('https://api.elevenlabs.io/v1/text-to-speech/testVoice123?'))
    def test_invalid_expired_and_changed_knowledge_playback(self):
        with self.assertRaises(ValueError):speech.eleven_audio('made-up')
        result=speech.attach_playback(engine.answer('How do I lock my card?'));token=result['playback_token']
        speech.PLAYBACK[token]['created']-=speech.PLAYBACK_TTL+1
        with self.assertRaises(ValueError):speech.eleven_audio(token)
        result=speech.attach_playback(engine.answer('How do I lock my card?'));token=result['playback_token']
        speech.PLAYBACK[token]['corpus_hash']='changed'
        with self.assertRaises(ValueError):speech.eleven_audio(token)
    def test_speech_provider_errors_are_sanitized(self):
        request=__import__('urllib.request',fromlist=['Request']).Request('https://api.elevenlabs.io/')
        errors=[TimeoutError('private secret'),urllib.error.HTTPError('https://api.elevenlabs.io/',429,'private secret',{},io.BytesIO(b'private secret'))]
        for error in errors:
            with patch('urllib.request.OpenerDirector.open',side_effect=error):
                with self.assertRaises(ModelError) as ctx:speech._exchange(request,100,'ElevenLabs TTS')
                self.assertNotIn('private secret',str(ctx.exception))
    def test_no_key_tts_has_explicit_error(self):
        result=speech.attach_playback(engine.answer('How do I lock my card?'))
        with patch.dict(os.environ,{'ELEVENLABS_API_KEY':'','ELEVENLABS_VOICE_ID':''}),patch('speech._exchange') as exchange:
            with self.assertRaises(ModelError):speech.eleven_audio(result['playback_token'])
            exchange.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
