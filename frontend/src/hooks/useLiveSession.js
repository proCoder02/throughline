import { useCallback, useRef, useState } from 'react';
import { getToken, apiJson } from '../api.js';

// Ports the mic-capture + /ws/listen logic from the old vanilla-JS app.
// Voice-activity pausing was dropped for brevity; audio streams continuously
// while listening (functionally equivalent, just less bandwidth-efficient).
export function useLiveSession({ onSessionStarted, onEnded }) {
  const [isListening, setIsListening] = useState(false);
  const [status, setStatus] = useState('');
  const [lines, setLines] = useState([]); // {speakerIndex, text}
  const [speakerNames, setSpeakerNames] = useState({});
  const [seenIndices, setSeenIndices] = useState([]);
  const [pendingSpeakerIndex, setPendingSpeakerIndex] = useState(null);
  const [knownSpeakers, setKnownSpeakers] = useState([]);

  const socketRef = useRef(null);
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const pendingRef = useRef(null); // mirror of pendingSpeakerIndex for handlers closed over old state

  const loadKnownSpeakers = useCallback(async () => {
    try { setKnownSpeakers(await apiJson('/speakers')); } catch (e) { /* keep previous list */ }
  }, []);

  const openPrompt = useCallback((idx) => {
    pendingRef.current = idx;
    setPendingSpeakerIndex(idx);
    loadKnownSpeakers();
  }, [loadKnownSpeakers]);

  const start = useCallback(async (resumeConversationId) => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    streamRef.current = stream;
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const token = getToken();
    const params = new URLSearchParams();
    if (token) params.set('token', token);
    if (resumeConversationId) params.set('conversation_id', resumeConversationId);
    const qs = params.toString();
    const url = `${proto}//${window.location.host}/ws/listen` + (qs ? `?${qs}` : '');
    const socket = new WebSocket(url);
    socketRef.current = socket;

    socket.onopen = () => {
      const recorder = new MediaRecorder(stream, { mimeType: 'audio/webm;codecs=opus' });
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0 && socket.readyState === WebSocket.OPEN) socket.send(e.data);
      };
      recorder.start(250);
      recorderRef.current = recorder;
      setIsListening(true);
      setStatus('Listening...');
      setLines([]);
      setSpeakerNames({});
      setSeenIndices([]);
    };

    socket.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === 'transcript') {
        setLines((prev) => [...prev, { speakerIndex: msg.speaker_index, text: msg.line.split(': ').slice(1).join(': ') }]);
      } else if (msg.type === 'new_speaker') {
        setSeenIndices((prev) => (prev.includes(msg.speaker_index) ? prev : [...prev, msg.speaker_index]));
        if (pendingRef.current !== msg.speaker_index) openPrompt(msg.speaker_index);
      } else if (msg.type === 'session_started') {
        onSessionStarted?.(msg.conversation_id);
        openPrompt(0);
      } else if (msg.type === 'background_update') {
        setStatus(`Auto-extracted ${msg.tasks_found} task(s), notes on ${msg.speakers_found} speaker(s).`);
      } else if (msg.type === 'error') {
        setStatus('Error: ' + msg.message);
      }
    };

    socket.onclose = () => {
      setIsListening(false);
      setStatus('Stopped. Saved automatically.');
      onEnded?.();
    };
  }, [onSessionStarted, onEnded, openPrompt]);

  const stop = useCallback(() => {
    if (recorderRef.current && recorderRef.current.state !== 'inactive') recorderRef.current.stop();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    socketRef.current?.close();
  }, []);

  const nameSpeaker = useCallback((idx, name) => {
    setSpeakerNames((prev) => ({ ...prev, [idx]: name }));
    setSeenIndices((prev) => (prev.includes(idx) ? prev : [...prev, idx]));
    socketRef.current?.readyState === WebSocket.OPEN &&
      socketRef.current.send(JSON.stringify({ type: 'rename_speaker', speaker_index: idx, name }));
    pendingRef.current = null;
    setPendingSpeakerIndex(null);
    loadKnownSpeakers();
  }, [loadKnownSpeakers]);

  const dismissPrompt = useCallback(() => {
    pendingRef.current = null;
    setPendingSpeakerIndex(null);
  }, []);

  const transcriptText = lines
    .map((l) => `${speakerNames[l.speakerIndex] || 'Speaker ' + l.speakerIndex}: ${l.text}`)
    .join('\n');

  return {
    isListening, status, lines, speakerNames, seenIndices, transcriptText,
    pendingSpeakerIndex, knownSpeakers, openPrompt,
    start, stop, nameSpeaker, dismissPrompt,
  };
}
