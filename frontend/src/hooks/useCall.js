import { useCallback, useRef, useState } from 'react';
import { Room, RoomEvent, Track, createLocalAudioTrack } from 'livekit-client';
import { post, postForm } from '../api.js';

// Voice calling (1:1 + group) via LiveKit. Recording is deliberately done
// client-side (not LiveKit Egress, which needs S3/IAM) -- the call
// initiator's browser mixes its own mic with every remote participant's
// audio via the Web Audio API into one combined stream, records it with
// MediaRecorder (same recorder setup as solo dictation in useLiveSession),
// and uploads the result to /calls/<id>/recording when the call ends.
export function useCall() {
  const [activeCall, setActiveCall] = useState(null); // {callId, roomName, isInitiator, muted, participants, droppedNotices}
  const roomRef = useRef(null);
  const audioCtxRef = useRef(null);
  const mixDestRef = useRef(null);
  const recorderRef = useRef(null);
  const recordedChunksRef = useRef([]);
  const localTrackRef = useRef(null);
  const isInitiatorRef = useRef(false);
  const audioElsRef = useRef(new Map()); // track sid -> <audio> element actually playing it
  const leaveCallRef = useRef(null); // always-current leaveCall, for the disconnect-triggered auto-hangup below

  const addTrackToMix = (mediaStreamTrack) => {
    if (!audioCtxRef.current || !mixDestRef.current || !mediaStreamTrack) return;
    try {
      const source = audioCtxRef.current.createMediaStreamSource(new MediaStream([mediaStreamTrack]));
      source.connect(mixDestRef.current);
    } catch (e) { /* a track ending mid-mix shouldn't take down the call */ }
  };

  const connectToRoom = useCallback(async ({ call_id, room_name, token, livekit_url, isInitiator }) => {
    const room = new Room();
    roomRef.current = room;
    isInitiatorRef.current = isInitiator;

    if (isInitiator) {
      const ctx = new AudioContext();
      audioCtxRef.current = ctx;
      mixDestRef.current = ctx.createMediaStreamDestination();
    }

    // Two separate concerns that were previously (wrongly) conflated: every
    // participant needs to actually HEAR remote audio (attach + play it),
    // regardless of who's recording. Only the initiator additionally feeds
    // it into the mix graph for the post-call transcript.
    room.on(RoomEvent.TrackSubscribed, (track) => {
      if (track.kind !== Track.Kind.Audio) return;
      const el = track.attach();
      el.autoplay = true;
      document.body.appendChild(el);
      audioElsRef.current.set(track.sid, el);
      el.play?.().catch(() => {}); // belt-and-suspenders if autoplay is blocked
      if (isInitiatorRef.current) addTrackToMix(track.mediaStreamTrack);
    });
    room.on(RoomEvent.TrackUnsubscribed, (track) => {
      track.detach().forEach((el) => el.remove());
      audioElsRef.current.delete(track.sid);
    });
    // Browsers commonly block audio elements created outside a direct click
    // handler (which TrackSubscribed, firing after async WebRTC signaling,
    // never counts as) -- this is the documented LiveKit-recommended way to
    // detect it and recover via an explicit "tap to enable audio" gesture,
    // rather than silently having no sound with no indication why.
    room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
      setActiveCall((c) => (c ? { ...c, audioBlocked: !room.canPlaybackAudio } : c));
    });
    room.on(RoomEvent.ParticipantConnected, (p) => {
      setActiveCall((c) => (c ? { ...c, participants: [...c.participants, { identity: p.identity, name: p.name || p.identity }] } : c));
    });
    room.on(RoomEvent.ParticipantDisconnected, (p) => {
      const droppedName = p.name || p.identity;
      const noticeId = `${p.identity}-${Date.now()}`;
      setActiveCall((c) => {
        if (!c) return c;
        const participants = c.participants.filter((x) => x.identity !== p.identity);
        // 1:1 call (or a group call that's fully drained): nobody left to
        // talk to, so hang up automatically instead of lingering alone in
        // an empty room -- matches how the "other side dropped" report
        // asked for the call to just close on its own.
        if (participants.length === 0) {
          setTimeout(() => leaveCallRef.current?.(), 0);
        }
        return {
          ...c,
          participants,
          droppedNotices: [...(c.droppedNotices || []), { id: noticeId, name: droppedName }],
        };
      });
      // Auto-expire the "X dropped from the call" banner, Gmeet-style.
      setTimeout(() => {
        setActiveCall((c) => (c ? { ...c, droppedNotices: (c.droppedNotices || []).filter((n) => n.id !== noticeId) } : c));
      }, 4000);
    });

    await room.connect(livekit_url, token);
    const localTrack = await createLocalAudioTrack();
    localTrackRef.current = localTrack;
    await room.localParticipant.publishTrack(localTrack);

    if (isInitiator) {
      addTrackToMix(localTrack.mediaStreamTrack);
      const recorder = new MediaRecorder(mixDestRef.current.stream, { mimeType: 'audio/webm;codecs=opus' });
      recordedChunksRef.current = [];
      recorder.ondataavailable = (e) => { if (e.data.size > 0) recordedChunksRef.current.push(e.data); };
      recorder.start(1000);
      recorderRef.current = recorder;
    }

    setActiveCall({
      callId: call_id, roomName: room_name, isInitiator, muted: false,
      audioBlocked: !room.canPlaybackAudio,
      droppedNotices: [],
      participants: room.remoteParticipants
        ? Array.from(room.remoteParticipants.values()).map((p) => ({ identity: p.identity, name: p.name || p.identity }))
        : [],
    });
  }, []);

  const enableAudio = useCallback(async () => {
    await roomRef.current?.startAudio();
    setActiveCall((c) => (c ? { ...c, audioBlocked: false } : c));
  }, []);

  const startCall = useCallback(async (friendIds) => {
    const data = await post('/calls', { friend_ids: friendIds });
    await connectToRoom({ ...data, isInitiator: true });
    return data;
  }, [connectToRoom]);

  const joinCall = useCallback(async (callId) => {
    const data = await post(`/calls/${callId}/join`, {});
    await connectToRoom({ ...data, isInitiator: false });
  }, [connectToRoom]);

  const declineCall = useCallback(async (callId) => {
    await post(`/calls/${callId}/decline`, {});
  }, []);

  const toggleMute = useCallback(async () => {
    const track = localTrackRef.current;
    if (!track) return;
    if (track.isMuted) await track.unmute(); else await track.mute();
    setActiveCall((c) => (c ? { ...c, muted: track.isMuted } : c));
  }, []);

  const leaveCall = useCallback(async () => {
    const call = activeCall;
    roomRef.current?.disconnect();
    roomRef.current = null;
    localTrackRef.current = null;
    audioElsRef.current.forEach((el) => { el.pause(); el.remove(); });
    audioElsRef.current.clear();

    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      const recorder = recorderRef.current;
      const stopped = new Promise((resolve) => { recorder.onstop = resolve; });
      recorder.stop();
      await stopped;
      recorderRef.current = null;
      try { await audioCtxRef.current?.close(); } catch (e) { /* ignore */ }

      if (call && recordedChunksRef.current.length) {
        const blob = new Blob(recordedChunksRef.current, { type: 'audio/webm' });
        const form = new FormData();
        form.append('audio', blob, 'call.webm');
        try { await postForm(`/calls/${call.callId}/recording`, form); } catch (e) { /* best-effort */ }
      }
      recordedChunksRef.current = [];
    }

    if (call) {
      try { await post(`/calls/${call.callId}/leave`, {}); } catch (e) { /* already ended server-side is fine */ }
    }
    setActiveCall(null);
  }, [activeCall]);

  // Kept current every render so the ParticipantDisconnected handler above
  // (registered once, inside connectToRoom) can trigger an up-to-date leave
  // instead of closing over a stale one.
  leaveCallRef.current = leaveCall;

  return { activeCall, startCall, joinCall, declineCall, toggleMute, leaveCall, enableAudio };
}
